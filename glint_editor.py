#!/usr/bin/env python3
"""
glint_editor.py — Glint's post-capture preview and annotation editor.

Launched as its own process (see glint_common.spawn_helper) with a path
to a temp PNG plus the Copy/Save intent the picker was configured with.
Runs its own Gtk.main() independently, so it never has to coordinate a
nested event loop with the picker that spawned it.

Usage:
    glint_editor.py <image_path> <copy:0|1> <save_folder-or-empty>
"""
import os
import sys
import math

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib  # noqa: E402

try:
    import cairo
except ImportError:
    print(
        "glint_editor: the 'cairo' Python module is missing. "
        "Install it with: sudo apt install python3-gi-cairo",
        file=sys.stderr,
    )
    sys.exit(1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glint_common as gc  # noqa: E402

TOOLS = ["pen", "arrow", "rect", "text", "pixelate", "crop"]


def pixbuf_to_surface(pixbuf: GdkPixbuf.Pixbuf) -> "cairo.ImageSurface":
    w, h = pixbuf.get_width(), pixbuf.get_height()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surface)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint()
    return surface


def surface_to_pixbuf(surface: "cairo.ImageSurface") -> GdkPixbuf.Pixbuf:
    w, h = surface.get_width(), surface.get_height()
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, w, h)


class EditorWindow(Gtk.Window):
    def __init__(self, image_path, copy_enabled, save_folder):
        super().__init__(title="Glint — Edit Screenshot")
        self.set_position(Gtk.WindowPosition.CENTER)
        self.copy_enabled = copy_enabled
        self.save_folder = save_folder or None

        pixbuf = GdkPixbuf.Pixbuf.new_from_file(image_path)
        self.surface = pixbuf_to_surface(pixbuf)
        self.undo_stack = []
        self.redo_stack = []

        self.tool = "pen"
        self.color = Gdk.RGBA()
        self.color.parse(gc.ACCENT)
        self.line_width = 3

        self.drag_start = None
        self.drag_current = None

        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        layout = gc.preferred_decoration_layout()
        if layout:
            header.set_decoration_layout(layout)
        self.set_titlebar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        # -- toolbar ------------------------------------------------------
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        toolbar.set_border_width(6)
        root.pack_start(toolbar, False, False, 0)

        group = None
        tool_labels = {
            "pen": "Pen", "arrow": "Arrow", "rect": "Rectangle",
            "text": "Text", "pixelate": "Pixelate", "crop": "Crop",
        }
        for name in TOOLS:
            btn = Gtk.RadioToolButton.new_from_widget(group)
            if group is None:
                group = btn
            btn.set_label(tool_labels[name])
            btn.set_active(name == self.tool)
            btn.connect("toggled", self._on_tool_toggled, name)
            toolbar.pack_start(btn, False, False, 0)

        toolbar.pack_start(Gtk.SeparatorToolItem(), False, False, 4)

        color_btn = Gtk.ColorButton()
        color_btn.set_rgba(self.color)
        color_btn.set_tooltip_text("Annotation color")
        color_btn.connect("color-set", self._on_color_set)
        toolbar.pack_start(color_btn, False, False, 0)

        width_label = Gtk.Label(label="Width")
        toolbar.pack_start(width_label, False, False, 4)
        width_spin = Gtk.SpinButton()
        width_spin.set_range(1, 20)
        width_spin.set_increments(1, 2)
        width_spin.set_value(self.line_width)
        width_spin.connect("value-changed", self._on_width_changed)
        toolbar.pack_start(width_spin, False, False, 0)

        toolbar.pack_start(Gtk.SeparatorToolItem(), False, False, 4)

        undo_btn = Gtk.Button(label="Undo")
        undo_btn.connect("clicked", lambda _b: self._undo())
        toolbar.pack_start(undo_btn, False, False, 0)
        redo_btn = Gtk.Button(label="Redo")
        redo_btn.connect("clicked", lambda _b: self._redo())
        toolbar.pack_start(redo_btn, False, False, 0)

        # -- canvas ---------------------------------------------------------
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        root.pack_start(scroller, True, True, 0)

        self.canvas = Gtk.DrawingArea()
        self.canvas.set_size_request(self.surface.get_width(), self.surface.get_height())
        self.canvas.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.canvas.connect("draw", self._on_draw)
        self.canvas.connect("button-press-event", self._on_press)
        self.canvas.connect("motion-notify-event", self._on_motion)
        self.canvas.connect("button-release-event", self._on_release)
        scroller.add(self.canvas)

        # -- bottom actions ---------------------------------------------------
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.set_border_width(8)
        actions.set_halign(Gtk.Align.END)
        root.pack_start(actions, False, False, 0)

        cancel_btn = Gtk.Button(label="Discard")
        cancel_btn.connect("clicked", lambda _b: self.destroy())
        actions.pack_start(cancel_btn, False, False, 0)

        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", self._on_open_clicked)
        actions.pack_start(open_btn, False, False, 0)

        copy_btn = Gtk.Button(label="Copy")
        copy_btn.connect("clicked", self._on_copy_clicked)
        actions.pack_start(copy_btn, False, False, 0)

        save_as_btn = Gtk.Button(label="Save As…")
        save_as_btn.connect("clicked", self._on_save_as_clicked)
        actions.pack_start(save_as_btn, False, False, 0)

        finish_label = "Save" if self.save_folder else "Copy && Close"
        finish_btn = Gtk.Button(label=finish_label)
        finish_btn.get_style_context().add_class("suggested-action")
        finish_btn.connect("clicked", self._on_finish_clicked)
        actions.pack_start(finish_btn, False, False, 0)

        self.connect("destroy", lambda *_: Gtk.main_quit())
        self.set_default_size(
            min(self.surface.get_width() + 40, 1100),
            min(self.surface.get_height() + 120, 800),
        )
        self.show_all()

    # -- undo/redo -----------------------------------------------------------

    def _snapshot_surface(self):
        copy = cairo.ImageSurface(cairo.FORMAT_ARGB32, self.surface.get_width(), self.surface.get_height())
        cr = cairo.Context(copy)
        cr.set_source_surface(self.surface, 0, 0)
        cr.paint()
        return copy

    def _push_undo(self):
        self.undo_stack.append(self._snapshot_surface())
        self.redo_stack.clear()
        if len(self.undo_stack) > 30:
            self.undo_stack.pop(0)

    def _undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self._snapshot_surface())
        self.surface = self.undo_stack.pop()
        self._resize_canvas_to_surface()
        self.canvas.queue_draw()

    def _redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self._snapshot_surface())
        self.surface = self.redo_stack.pop()
        self._resize_canvas_to_surface()
        self.canvas.queue_draw()

    def _resize_canvas_to_surface(self):
        self.canvas.set_size_request(self.surface.get_width(), self.surface.get_height())

    # -- toolbar callbacks ------------------------------------------------

    def _on_tool_toggled(self, btn, name):
        if btn.get_active():
            self.tool = name

    def _on_color_set(self, btn):
        self.color = btn.get_rgba()

    def _on_width_changed(self, spin):
        self.line_width = spin.get_value()

    # -- drawing -----------------------------------------------------------

    def _on_draw(self, widget, cr):
        cr.set_source_surface(self.surface, 0, 0)
        cr.paint()
        if self.drag_start and self.drag_current and self.tool in ("arrow", "rect", "pixelate", "crop"):
            self._draw_preview_shape(cr)
        return False

    def _draw_preview_shape(self, cr):
        x0, y0 = self.drag_start
        x1, y1 = self.drag_current
        cr.save()
        cr.set_source_rgba(self.color.red, self.color.green, self.color.blue, 0.9)
        cr.set_line_width(self.line_width)
        if self.tool == "rect":
            cr.rectangle(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            cr.stroke()
        elif self.tool == "arrow":
            self._stroke_arrow(cr, x0, y0, x1, y1)
        elif self.tool in ("pixelate", "crop"):
            cr.set_dash([5, 4])
            cr.rectangle(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            cr.stroke()
        cr.restore()

    @staticmethod
    def _stroke_arrow(cr, x0, y0, x1, y1):
        """Draws a shaft plus a simple triangular head at (x1, y1),
        pointing in the direction of travel."""
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.move_to(x0, y0)
        cr.line_to(x1, y1)
        cr.stroke()
        angle = math.atan2(y1 - y0, x1 - x0)
        head_len = 14
        head_angle = 0.45
        for a in (angle + math.pi - head_angle, angle + math.pi + head_angle):
            cr.move_to(x1, y1)
            cr.line_to(x1 + head_len * math.cos(a), y1 + head_len * math.sin(a))
            cr.stroke()

    def _on_press(self, widget, event):
        x, y = event.x, event.y
        if self.tool == "text":
            self._push_undo()
            self._prompt_text(x, y)
            return True
        self.drag_start = (x, y)
        self.drag_current = (x, y)
        if self.tool == "pen":
            self._push_undo()
        return True

    def _on_motion(self, widget, event):
        if self.drag_start is None:
            return False
        x, y = event.x, event.y
        if self.tool == "pen":
            cr = cairo.Context(self.surface)
            cr.set_source_rgba(self.color.red, self.color.green, self.color.blue, 1.0)
            cr.set_line_width(self.line_width)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            sx, sy = self.drag_start
            cr.move_to(sx, sy)
            cr.line_to(x, y)
            cr.stroke()
            self.drag_start = (x, y)
            self.canvas.queue_draw()
        else:
            self.drag_current = (x, y)
            self.canvas.queue_draw()
        return True

    def _on_release(self, widget, event):
        if self.drag_start is None:
            return False
        x0, y0 = self.drag_start
        x1, y1 = event.x, event.y

        if self.tool == "pen":
            self.drag_start = None
            self.drag_current = None
            return True

        if self.tool in ("rect", "arrow"):
            self._push_undo()
            cr = cairo.Context(self.surface)
            cr.set_source_rgba(self.color.red, self.color.green, self.color.blue, 1.0)
            cr.set_line_width(self.line_width)
            if self.tool == "rect":
                cr.rectangle(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
                cr.stroke()
            else:
                self._stroke_arrow(cr, x0, y0, x1, y1)
        elif self.tool == "pixelate":
            self._push_undo()
            self._pixelate_region(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        elif self.tool == "crop":
            self._push_undo()
            self._crop_to(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))

        self.drag_start = None
        self.drag_current = None
        self.canvas.queue_draw()
        return True

    def _pixelate_region(self, x, y, w, h):
        if w < 4 or h < 4:
            return
        x, y = int(x), int(y)
        w, h = int(w), int(h)
        sw, sh = self.surface.get_width(), self.surface.get_height()
        w = min(w, sw - x)
        h = min(h, sh - y)
        pixbuf = surface_to_pixbuf(self.surface)
        region = pixbuf.new_subpixbuf(x, y, w, h)
        block = max(4, min(w, h) // 12)
        small_w = max(1, w // block)
        small_h = max(1, h // block)
        small = region.scale_simple(small_w, small_h, GdkPixbuf.InterpType.BILINEAR)
        pixelated = small.scale_simple(w, h, GdkPixbuf.InterpType.NEAREST)
        cr = cairo.Context(self.surface)
        cr.save()
        cr.rectangle(x, y, w, h)
        cr.clip()
        Gdk.cairo_set_source_pixbuf(cr, pixelated, x, y)
        cr.paint()
        cr.restore()

    def _crop_to(self, x, y, w, h):
        if w < 4 or h < 4:
            return
        x, y, w, h = int(x), int(y), int(w), int(h)
        new_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(new_surface)
        cr.set_source_surface(self.surface, -x, -y)
        cr.paint()
        self.surface = new_surface
        self._resize_canvas_to_surface()

    def _prompt_text(self, x, y):
        popover = Gtk.Popover()
        popover.set_relative_to(self.canvas)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        entry = Gtk.Entry()
        entry.set_width_chars(24)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.set_border_width(6)
        box.pack_start(entry, True, True, 0)
        ok_btn = Gtk.Button(label="Add")
        box.pack_start(ok_btn, False, False, 0)
        popover.add(box)

        def commit(*_a):
            text = entry.get_text()
            if text:
                cr = cairo.Context(self.surface)
                cr.set_source_rgba(self.color.red, self.color.green, self.color.blue, 1.0)
                cr.select_font_face("sans-serif", 0, 1)
                cr.set_font_size(max(14, self.line_width * 6))
                cr.move_to(x, y)
                cr.show_text(text)
                self.canvas.queue_draw()
            popover.popdown()

        entry.connect("activate", commit)
        ok_btn.connect("clicked", commit)
        popover.show_all()
        popover.popup()
        entry.grab_focus()

    # -- final actions ---------------------------------------------------------

    def _current_pixbuf(self):
        return surface_to_pixbuf(self.surface)

    def _on_copy_clicked(self, _btn):
        gc.copy_to_clipboard(self._current_pixbuf())
        gc.notify("Glint", "Copied to clipboard.")

    def _on_save_as_clicked(self, _btn):
        dialog = Gtk.FileChooserDialog(
            title="Save screenshot as",
            parent=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK,
        )
        dialog.set_current_name("Screenshot.png")
        dialog.set_do_overwrite_confirmation(True)
        if dialog.run() == Gtk.ResponseType.OK:
            path = dialog.get_filename()
            if not path.lower().endswith(".png"):
                path += ".png"
            self._current_pixbuf().savev(path, "png", [], [])
            gc.notify("Glint", f"Saved to {path}")
        dialog.destroy()

    def _on_open_clicked(self, _btn):
        import tempfile
        fd, path = tempfile.mkstemp(prefix="glint-preview-", suffix=".png")
        os.close(fd)
        self._current_pixbuf().savev(path, "png", [], [])
        gc.open_path(path)

    def _on_finish_clicked(self, _btn):
        pixbuf = self._current_pixbuf()
        if self.copy_enabled:
            gc.copy_to_clipboard(pixbuf)
        if self.save_folder:
            path = gc.save_pixbuf_to_folder(pixbuf, self.save_folder)
            gc.notify("Screenshot saved", path + ("\nAlso copied to clipboard." if self.copy_enabled else ""))
        elif self.copy_enabled:
            gc.notify("Screenshot copied", "Ready to paste.")
        GLib.timeout_add(200, self.destroy)


def main():
    if len(sys.argv) < 2:
        print("Usage: glint_editor.py <image_path> [copy:0|1] [save_folder]", file=sys.stderr)
        sys.exit(1)
    image_path = sys.argv[1]
    copy_enabled = len(sys.argv) > 2 and sys.argv[2] == "1"
    save_folder = sys.argv[3] if len(sys.argv) > 3 else ""

    win = EditorWindow(image_path, copy_enabled, save_folder)
    win.connect("destroy", Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    main()
