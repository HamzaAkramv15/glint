#!/usr/bin/env python3
"""
Glint — a modern screenshot tool for X11 Linux desktops (Ubuntu Unity
and Ubuntu MATE, and anywhere else GTK3 + X11 + Wnck run).

Full screen / current monitor / active window / window-click / region
capture, with independent Copy-to-clipboard and Save-to-folder outputs,
an optional capture delay, an optional cursor overlay, and an optional
hop through the annotation editor before finishing.

Usage:
    glint.py                  # opens the picker window
    glint.py full              # capture the whole screen immediately
    glint.py monitor            # capture whichever monitor the mouse is on
    glint.py active-window       # capture the currently focused window
    glint.py window                # click a window to capture it
    glint.py region                 # drag a region to capture it
    glint.py record-full             # record the whole screen
    glint.py record-region            # drag a region, then record it
"""
import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, GdkX11  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glint_common as gc  # noqa: E402

APP_NAME = gc.APP_NAME


# --------------------------------------------------------------------------
# Selection overlay: region drag-select and window hover-pick.
# Can return either a captured pixbuf (screenshot) or raw geometry
# (used by the recorder to pick a region without taking a picture of it).
# --------------------------------------------------------------------------

class SelectionOverlay(Gtk.Window):
    """A screen-covering popup used for region and window capture modes.
    Being a POPUP window means it bypasses the window manager entirely -
    no fullscreen negotiation, no WM hints, it just appears exactly
    where we put it. This is what makes it work identically under
    Unity/Compiz and MATE/Marco."""

    def __init__(self, mode: str, on_done, on_cancel, return_geometry=False):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.mode = mode  # "region" or "window"
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.return_geometry = return_geometry

        self.backdrop = gc.capture_full_screen()
        self.screen_width = self.backdrop.get_width()
        self.screen_height = self.backdrop.get_height()

        self.drag_start = None
        self.current_pos = (0, 0)
        self.hovered_window = None
        self.windows = gc.list_capturable_windows() if mode == "window" else []

        screen = Gdk.Screen.get_default()
        visual = screen.get_rgba_visual()
        if visual is not None:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.set_default_size(self.screen_width, self.screen_height)
        self.move(0, 0)

        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
        )
        self.set_can_focus(True)

        self.connect("draw", self._on_draw)
        self.connect("button-press-event", self._on_press)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("button-release-event", self._on_release)
        self.connect("key-press-event", self._on_key)

        self.set_cursor_name("crosshair" if mode == "region" else "pointer")

        self.show_all()
        self._grab_input()

    def set_cursor_name(self, name):
        def apply_cursor():
            window = self.get_window()
            if window is not None:
                display = Gdk.Display.get_default()
                cursor = Gdk.Cursor.new_from_name(display, name)
                if cursor is not None:
                    window.set_cursor(cursor)
        GLib.idle_add(apply_cursor)

    def _grab_input(self):
        window = self.get_window()
        if window is None:
            return
        self.grab_focus()
        self.grab_add()
        seat = Gdk.Display.get_default().get_default_seat()
        seat.grab(window, Gdk.SeatCapabilities.ALL, True, None, None, None, None)

    def _release_input(self):
        self.grab_remove()
        Gdk.Display.get_default().get_default_seat().ungrab()

    def _close(self):
        self._release_input()
        self.destroy()

    # -- drawing --------------------------------------------------------

    def _on_draw(self, widget, cr):
        Gdk.cairo_set_source_pixbuf(cr, self.backdrop, 0, 0)
        cr.paint()

        cr.set_source_rgba(0.05, 0.05, 0.07, 0.55)
        cr.rectangle(0, 0, self.screen_width, self.screen_height)
        cr.fill()

        if self.mode == "region" and self.drag_start:
            self._draw_selection(cr)
        elif self.mode == "window" and self.hovered_window:
            self._draw_window_highlight(cr)
        return False

    def _draw_selection(self, cr):
        sx, sy = self.drag_start
        cx, cy = self.current_pos
        x, y = min(sx, cx), min(sy, cy)
        w, h = abs(cx - sx), abs(cy - sy)

        cr.save()
        cr.rectangle(x, y, w, h)
        cr.clip()
        Gdk.cairo_set_source_pixbuf(cr, self.backdrop, 0, 0)
        cr.paint()
        cr.restore()

        cr.set_source_rgb(0xE9 / 255, 0x54 / 255, 0x20 / 255)
        cr.set_line_width(2)
        cr.rectangle(x + 1, y + 1, max(w - 2, 0), max(h - 2, 0))
        cr.stroke()

        self._draw_label(cr, x, max(y - 26, 4), f"{int(w)} × {int(h)}")

    def _draw_window_highlight(self, cr):
        x, y, w, h = self.hovered_window.get_geometry()
        cr.set_source_rgb(0xE9 / 255, 0x54 / 255, 0x20 / 255)
        cr.set_line_width(3)
        cr.rectangle(x + 1.5, y + 1.5, max(w - 3, 0), max(h - 3, 0))
        cr.stroke()
        name = self.hovered_window.get_name() or "Untitled window"
        self._draw_label(cr, x, max(y - 26, 4), name)

    def _draw_label(self, cr, x, y, text):
        cr.select_font_face("sans-serif", 0, 1)
        cr.set_font_size(13)
        extents = cr.text_extents(text)
        pad = 8
        cr.set_source_rgba(0.11, 0.11, 0.15, 0.92)
        cr.rectangle(x, y, extents.width + pad * 2, 22)
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.move_to(x + pad, y + 15)
        cr.show_text(text)

    # -- input ------------------------------------------------------------

    def _on_press(self, widget, event):
        if event.button != 1:
            return False
        if self.mode == "region":
            self.drag_start = (event.x_root, event.y_root)
            self.current_pos = self.drag_start
            self.queue_draw()
        elif self.mode == "window" and self.hovered_window:
            x, y, w, h = self.hovered_window.get_client_window_geometry()
            self._deliver(x, y, w, h)
        return True

    def _on_motion(self, widget, event):
        self.current_pos = (event.x_root, event.y_root)
        if self.mode == "window":
            self.hovered_window = self._window_at(event.x_root, event.y_root)
        self.queue_draw()
        return True

    def _on_release(self, widget, event):
        if event.button != 1 or self.mode != "region" or not self.drag_start:
            return False
        sx, sy = self.drag_start
        ex, ey = event.x_root, event.y_root
        x, y = min(sx, ex), min(sy, ey)
        w, h = abs(ex - sx), abs(ey - sy)
        if w > 3 and h > 3:
            self._deliver(int(x), int(y), int(w), int(h))
        else:
            self.drag_start = None
            self.queue_draw()
        return True

    def _on_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self._close()
            self.on_cancel()
            return True
        return False

    def _window_at(self, x, y):
        for w in reversed(self.windows):
            wx, wy, ww, wh = w.get_geometry()
            if wx <= x < wx + ww and wy <= y < wy + wh:
                return w
        return None

    def _deliver(self, x, y, w, h):
        self._close()
        if self.return_geometry:
            self.on_done((x, y, w, h))
        else:
            self.on_done(self._crop_backdrop(x, y, w, h), (x, y))

    def _crop_backdrop(self, x, y, w, h) -> GdkPixbuf.Pixbuf:
        x = max(0, min(x, self.screen_width - 1))
        y = max(0, min(y, self.screen_height - 1))
        w = max(1, min(w, self.screen_width - x))
        h = max(1, min(h, self.screen_height - y))
        return self.backdrop.new_subpixbuf(x, y, w, h)


# --------------------------------------------------------------------------
# Main picker window
# --------------------------------------------------------------------------

IS_DARK_MODE = False  # set once in main(), read by icon-drawing callbacks


class MainWindow(Gtk.Window):
    def __init__(self, initial_mode=None):
        super().__init__(title=APP_NAME)
        self.set_default_size(360, 0)
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER)

        header = Gtk.HeaderBar()
        header.set_title("Take Screenshot")
        header.set_show_close_button(True)
        layout = gc.preferred_decoration_layout()
        if layout:
            header.set_decoration_layout(layout)
        header.get_style_context().add_class("glint-header")
        self.set_titlebar(header)
        self.get_style_context().add_class("glint-main")

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(root)

        settings = gc.load_settings()
        self.copy_enabled = settings["copy"]
        self.save_enabled = settings["save"]
        self.folder = settings["folder"]
        self.delay_seconds = settings["delay"]
        self.include_cursor = settings["include_cursor"]
        self.edit_after_capture = settings["edit_after_capture"]

        notebook = Gtk.Notebook()
        root.pack_start(notebook, True, True, 0)

        # ==================================================================
        # Tab 1: Screenshot
        # ==================================================================
        screenshot_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        screenshot_page.set_border_width(16)
        notebook.append_page(screenshot_page, Gtk.Label(label="Screenshot"))

        primary_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        primary_row.set_homogeneous(True)
        screenshot_page.pack_start(primary_row, False, False, 0)
        for mode, label in [("full", "Screen"), ("window", "Window"), ("region", "Selection")]:
            btn = self._make_area_button(mode, label)
            btn.connect("clicked", self._on_mode_clicked, mode)
            primary_row.pack_start(btn, True, True, 0)

        # ==================================================================
        # Tab 2: Options (shared between Screenshot and Record)
        # ==================================================================
        options_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        options_page.set_border_width(16)
        notebook.append_page(options_page, Gtk.Label(label="Options"))

        shared_note = Gtk.Label(
            label="These apply to both Screenshot and Record.", xalign=0
        )
        shared_note.get_style_context().add_class("dim-label")
        options_page.pack_start(shared_note, False, False, 0)

        options_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        options_page.pack_start(options_row, False, False, 0)

        delay_label = Gtk.Label(label="Delay")
        options_row.pack_start(delay_label, False, False, 0)
        self.delay_combo = Gtk.ComboBoxText()
        active_index = 0
        for i, (text, seconds) in enumerate(gc.DELAY_OPTIONS):
            self.delay_combo.append_text(text)
            if seconds == self.delay_seconds:
                active_index = i
        self.delay_combo.set_active(active_index)
        self.delay_combo.connect("changed", self._on_delay_changed)
        options_row.pack_start(self.delay_combo, False, False, 0)

        self.cursor_check = Gtk.CheckButton(label="Include cursor")
        self.cursor_check.set_active(self.include_cursor)
        self.cursor_check.set_tooltip_text(
            "Screenshots only - recordings always include the cursor."
        )
        self.cursor_check.connect("toggled", self._on_cursor_toggled)
        options_row.pack_end(self.cursor_check, False, False, 0)

        options_page.pack_start(Gtk.Separator(), False, False, 2)

        output_label = Gtk.Label(label="Output folder", xalign=0)
        output_label.get_style_context().add_class("dim-label")
        options_page.pack_start(output_label, False, False, 0)

        self.copy_check = Gtk.CheckButton(label="Copy screenshots to clipboard")
        self.copy_check.set_active(self.copy_enabled)
        self.copy_check.connect("toggled", self._on_copy_toggled)
        options_page.pack_start(self.copy_check, False, False, 0)

        save_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.save_check = Gtk.CheckButton(label="Save screenshots to folder")
        self.save_check.set_active(self.save_enabled)
        self.save_check.connect("toggled", self._on_save_toggled)
        save_row.pack_start(self.save_check, False, False, 0)
        options_page.pack_start(save_row, False, False, 0)

        self.folder_chooser_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.folder_chooser_row.get_style_context().add_class("glint-folder-row")
        self.folder_path_label = Gtk.Label(label=self._shorten(self.folder), xalign=0)
        self.folder_path_label.set_ellipsize(3)
        change_btn = Gtk.Button()
        change_btn.get_style_context().add_class("glint-folder-icon-btn")
        change_btn.set_tooltip_text("Choose a different folder")
        folder_icon = Gtk.DrawingArea()
        folder_icon.set_size_request(18, 18)
        folder_icon.connect("draw", self._draw_folder_icon)
        change_btn.add(folder_icon)
        change_btn.connect("clicked", self._on_change_folder)
        self.folder_path_label.show()
        folder_icon.show()
        change_btn.show()
        self.folder_chooser_row.pack_start(self.folder_path_label, True, True, 0)
        self.folder_chooser_row.pack_start(change_btn, False, False, 0)
        folder_note = Gtk.Label(
            label="Also used as the save location for recordings.", xalign=0
        )
        folder_note.get_style_context().add_class("dim-label")
        options_page.pack_start(self.folder_chooser_row, False, False, 0)
        options_page.pack_start(folder_note, False, False, 0)
        # The folder row is only meaningful to look at when Save is on,
        # but recordings always need a folder regardless - so unlike
        # before, this row's visibility no longer hides based on the
        # Save checkbox alone.

        self.edit_check = Gtk.CheckButton(label="Open editor before finishing (screenshots only)")
        self.edit_check.set_active(self.edit_after_capture)
        self.edit_check.connect("toggled", self._on_edit_toggled)
        options_page.pack_start(self.edit_check, False, False, 0)

        # ==================================================================
        # Tab 3: Record
        # ==================================================================
        record_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        record_page.set_border_width(16)
        notebook.append_page(record_page, Gtk.Label(label="Record"))

        record_note = Gtk.Label(
            label="Saves an .mp4 to the folder set in Options.", xalign=0
        )
        record_note.get_style_context().add_class("dim-label")
        record_page.pack_start(record_note, False, False, 0)

        record_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        record_row.set_homogeneous(True)
        record_page.pack_start(record_row, False, False, 0)
        for mode, label in [("record-full", "Record Screen"), ("record-region", "Record Region")]:
            icon_kind = "record-full" if mode == "record-full" else "record-region"
            btn = self._make_record_button(label, icon_kind)
            btn.connect("clicked", self._on_record_clicked, mode)
            record_row.pack_start(btn, True, True, 0)

        self.connect("destroy", lambda *_: Gtk.main_quit())

        if initial_mode is not None:
            self.set_no_show_all(True)
            GLib.timeout_add(50, lambda: self._begin_capture(initial_mode))
        else:
            self.show_all()

    # -- icon-theme icons ---------------------------------------------------
    # Hand-drawn Cairo icons kept looking off (proportions, anti-aliasing)
    # no matter how much they got tweaked. Using the system icon theme's
    # own symbolic icons instead means Screen/Window/Selection render with
    # the same crisp, professionally-drawn glyphs as every other GTK app on
    # the desktop, and automatically match whatever theme is active
    # (Adwaita, Breeze, Papirus, Humanity on Unity, ...).

    _ICON_CANDIDATES = {
        "full": ["video-display-symbolic", "computer-symbolic", "video-display"],
        "monitor": ["video-display-symbolic", "computer-symbolic", "video-display"],
        "window": [
            "focus-windows-symbolic",
            "preferences-system-windows-symbolic",
            "window-new-symbolic",
        ],
        "activewindow": [
            "focus-windows-symbolic",
            "preferences-system-windows-symbolic",
            "window-new-symbolic",
        ],
        "region": [
            "edit-select-all-symbolic",
            "selection-mode-symbolic",
            "view-fullscreen-symbolic",
        ],
    }
    _RECORD_BADGE_CANDIDATES = ["media-record-symbolic", "media-playback-start-symbolic"]

    @classmethod
    def _resolve_icon_name(cls, candidates):
        theme = Gtk.IconTheme.get_default()
        for name in candidates:
            if theme.has_icon(name):
                return name
        return candidates[-1]  # let GTK show its "missing icon" rather than crash

    @classmethod
    def _make_icon_widget(cls, icon_kind, pixel_size=32):
        base_kind = {"record-full": "full", "record-region": "region"}.get(
            icon_kind, icon_kind
        )
        name = cls._resolve_icon_name(cls._ICON_CANDIDATES[base_kind])
        image = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.DIALOG)
        image.set_pixel_size(pixel_size)

        if icon_kind not in ("record-full", "record-region", "activewindow"):
            return image

        # Record-tab variants (and active-window) get a small red badge
        # overlaid on the corner instead of a second hand-drawn shape -
        # still a system icon, just tinted and scaled down.
        overlay = Gtk.Overlay()
        overlay.add(image)
        badge_name = cls._resolve_icon_name(cls._RECORD_BADGE_CANDIDATES)
        badge = Gtk.Image.new_from_icon_name(badge_name, Gtk.IconSize.MENU)
        badge.set_pixel_size(14)
        badge.get_style_context().add_class("glint-rec-badge")
        badge.set_halign(Gtk.Align.END)
        badge.set_valign(Gtk.Align.START)
        overlay.add_overlay(badge)
        overlay.set_overlay_pass_through(badge, True)
        return overlay

    def _make_area_button(self, icon_kind, label_text):
        btn = Gtk.Button()
        btn.get_style_context().add_class("glint-area-btn")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.pack_start(self._make_icon_widget(icon_kind), False, False, 0)
        box.pack_start(Gtk.Label(label=label_text), False, False, 0)
        btn.add(box)
        return btn

    def _make_record_button(self, label_text, icon_kind):
        # Same visual language as the screenshot buttons - icon over
        # label, same size and button class - so the Record tab doesn't
        # feel like a different, bolted-on app.
        btn = Gtk.Button()
        btn.get_style_context().add_class("glint-area-btn")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.pack_start(self._make_icon_widget(icon_kind), False, False, 0)
        box.pack_start(Gtk.Label(label=label_text), False, False, 0)
        btn.add(box)
        return btn

    @staticmethod
    def _draw_folder_icon(widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        fill = (0.75, 0.75, 0.75) if IS_DARK_MODE else (0.4, 0.4, 0.4)
        cr.set_source_rgb(*fill)
        tab_w = w * 0.35
        body_y = h * 0.32
        cr.move_to(w * 0.1, body_y)
        cr.line_to(w * 0.1 + tab_w * 0.5, body_y)
        cr.line_to(w * 0.1 + tab_w, body_y - h * 0.12)
        cr.line_to(w * 0.9, body_y - h * 0.12)
        cr.line_to(w * 0.9, h * 0.85)
        cr.line_to(w * 0.1, h * 0.85)
        cr.close_path()
        cr.fill()
        return False

    def _shorten(self, path, max_len=26):
        home = os.path.expanduser("~")
        if path.startswith(home):
            path = "~" + path[len(home):]
        if len(path) > max_len:
            path = "…" + path[-(max_len - 1):]
        return path

    # -- settings toggles ---------------------------------------------------

    def _persist(self):
        gc.save_settings({
            "copy": self.copy_enabled,
            "save": self.save_enabled,
            "folder": self.folder,
            "delay": self.delay_seconds,
            "include_cursor": self.include_cursor,
            "edit_after_capture": self.edit_after_capture,
        })

    def _on_copy_toggled(self, btn):
        self.copy_enabled = btn.get_active()
        if not self.copy_enabled and not self.save_enabled:
            # Don't let the user land on a no-op configuration - flip
            # Save on instead of silently ignoring the click.
            self.save_check.set_active(True)
        self._persist()

    def _on_save_toggled(self, btn):
        self.save_enabled = btn.get_active()
        if not self.copy_enabled and not self.save_enabled:
            self.copy_check.set_active(True)
        self._persist()

    def _on_edit_toggled(self, btn):
        self.edit_after_capture = btn.get_active()
        self._persist()

    def _on_cursor_toggled(self, btn):
        self.include_cursor = btn.get_active()
        self._persist()

    def _on_delay_changed(self, combo):
        self.delay_seconds = gc.DELAY_OPTIONS[combo.get_active()][1]
        self._persist()

    def _snap_to_natural_size(self):
        self.resize(1, 1)
        return False

    def _on_change_folder(self, _btn):
        dialog = Gtk.FileChooserDialog(
            title="Choose a folder for screenshots",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        dialog.set_filename(self.folder)
        if dialog.run() == Gtk.ResponseType.OK:
            self.folder = dialog.get_filename()
            self.folder_path_label.set_text(self._shorten(self.folder))
            self._persist()
        dialog.destroy()

    # -- capture dispatch -----------------------------------------------------

    def _on_mode_clicked(self, _btn, mode):
        self.hide()
        GLib.timeout_add(150, lambda: self._start_delay_then_capture(mode))

    def _on_record_clicked(self, _btn, mode):
        self.hide()
        GLib.timeout_add(150, lambda: self._start_recording(mode))

    def _start_delay_then_capture(self, mode):
        if self.delay_seconds > 0:
            gc.notify("Glint", f"Capturing in {self.delay_seconds}s…")
            GLib.timeout_add_seconds(self.delay_seconds, lambda: self._begin_capture(mode))
        else:
            self._begin_capture(mode)
        return False

    def _start_recording(self, mode):
        target = "full" if mode == "record-full" else "region"
        if self.delay_seconds > 0:
            gc.notify("Glint", f"Recording starts in {self.delay_seconds}s…")
            GLib.timeout_add_seconds(self.delay_seconds, lambda: self._launch_recorder(target))
        else:
            self._launch_recorder(target)
        return False

    def _launch_recorder(self, target):
        gc.spawn_helper("glint_record.py", target)
        Gtk.main_quit()
        return False

    def _begin_capture(self, mode):
        if mode == "full":
            self._handle_result(gc.capture_full_screen(), (0, 0))
        elif mode == "monitor":
            x, y, w, h = gc.monitor_geometry_at_pointer()
            self._handle_result(gc.capture_region(x, y, w, h), (x, y))
        elif mode == "activewindow":
            geo = gc.get_active_window_geometry()
            if geo is None:
                gc.notify("Glint", "Couldn't find a focused window to capture.")
                Gtk.main_quit()
                return False
            self._handle_result(gc.capture_region(*geo), (geo[0], geo[1]))
        elif mode == "window":
            SelectionOverlay("window", self._handle_result, self._on_cancelled)
        elif mode == "region":
            SelectionOverlay("region", self._handle_result, self._on_cancelled)
        return False

    def _on_cancelled(self):
        Gtk.main_quit()

    def _handle_result(self, pixbuf: GdkPixbuf.Pixbuf, origin=(0, 0)):
        if self.include_cursor:
            pixbuf = gc.draw_cursor_on_pixbuf(pixbuf, *origin)

        if self.edit_after_capture:
            gc.spawn_helper(
                "glint_editor.py",
                self._write_temp(pixbuf),
                "1" if self.copy_enabled else "0",
                self.folder if self.save_enabled else "",
            )
            GLib.timeout_add(200, Gtk.main_quit)
            return

        if self.copy_enabled:
            gc.copy_to_clipboard(pixbuf)
        if self.save_enabled:
            path = gc.save_pixbuf_to_folder(pixbuf, self.folder)
            if self.copy_enabled:
                gc.notify("Screenshot saved", f"{path}\nAlso copied to clipboard.")
            else:
                gc.notify("Screenshot saved", path)
        elif self.copy_enabled:
            gc.notify("Screenshot copied", "Ready to paste.")

        GLib.timeout_add(300, Gtk.main_quit)

    def _write_temp(self, pixbuf):
        import tempfile
        fd, path = tempfile.mkstemp(prefix="glint-", suffix=".png")
        os.close(fd)
        pixbuf.savev(path, "png", [], [])
        return path


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def parse_cli_mode():
    if len(sys.argv) < 2:
        return None
    arg = sys.argv[1].lower()
    if arg in ("full", "fullscreen", "full-screen", "screen"):
        return "full"
    if arg in ("monitor", "current-monitor", "currentmonitor"):
        return "monitor"
    if arg in ("active-window", "activewindow", "active"):
        return "activewindow"
    if arg in ("window", "win"):
        return "window"
    if arg in ("region", "area", "selection"):
        return "region"
    if arg in ("record", "record-full", "recordfull", "record-screen"):
        return "record-full"
    if arg in ("record-region", "recordregion", "record-area"):
        return "record-region"
    return None


def main():
    global IS_DARK_MODE
    IS_DARK_MODE = gc.detect_dark_mode()

    css_provider = Gtk.CssProvider()
    css_provider.load_from_data(gc.build_css(IS_DARK_MODE).encode("utf-8"))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        css_provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )

    mode = parse_cli_mode()
    if mode in ("record-full", "record-region"):
        gc.spawn_helper("glint_record.py", "full" if mode == "record-full" else "region")
        return

    MainWindow(initial_mode=mode)
    Gtk.main()


if __name__ == "__main__":
    main()
