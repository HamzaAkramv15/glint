#!/usr/bin/env python3
"""
glint_record.py — Glint's screen recording add-on.

Records the full screen or a dragged region to an .mp4 using ffmpeg's
x11grab input, the same way most lightweight Linux screen recorders do
it under X11. Runs as its own process (see glint_common.spawn_helper),
with a small always-on-top control bar showing elapsed time and a Stop
button.

Usage:
    glint_record.py full
    glint_record.py region
"""
import os
import shutil
import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import glint_common as gc  # noqa: E402

# Reuse the picker's selection overlay for dragging a record region,
# without duplicating that drag/highlight logic.
import glint as glint_app  # noqa: E402


def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def build_ffmpeg_cmd(x, y, w, h, output_path, with_audio):
    display = os.environ.get("DISPLAY", ":0")
    # Dimensions must be even for libx264's default yuv420p pixel format.
    w -= w % 2
    h -= h % 2
    cmd = [
        "ffmpeg", "-y",
        "-f", "x11grab",
        "-video_size", f"{w}x{h}",
        "-framerate", "30",
        "-i", f"{display}+{x},{y}",
    ]
    if with_audio and shutil.which("pactl"):
        cmd += ["-f", "pulse", "-i", "default"]
    cmd += [
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p",
    ]
    if with_audio and shutil.which("pactl"):
        cmd += ["-c:a", "aac"]
    cmd.append(output_path)
    return cmd


class ControlBar(Gtk.Window):
    """A small always-on-top bar: elapsed time + Stop. Kept deliberately
    tiny and undecorated so it doesn't itself show up as a distracting
    window in the recording."""

    def __init__(self, on_stop, on_cancel):
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.on_stop = on_stop
        self.on_cancel = on_cancel
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.get_style_context().add_class("glint-record-bar")

        css = Gtk.CssProvider()
        css.load_from_data(b"""
            .glint-record-bar { background: #1a1a1a; border-radius: 8px; }
            .glint-record-bar label { color: #f0f0f0; font-family: "Ubuntu", sans-serif; }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_border_width(8)
        self.add(box)

        dot = Gtk.DrawingArea()
        dot.set_size_request(12, 12)
        dot.connect("draw", self._draw_dot)
        box.pack_start(dot, False, False, 0)

        self.time_label = Gtk.Label(label="00:00")
        box.pack_start(self.time_label, False, False, 0)

        stop_btn = Gtk.Button(label="Stop")
        stop_btn.connect("clicked", lambda _b: self.on_stop())
        box.pack_start(stop_btn, False, False, 0)

        cancel_btn = Gtk.Button(label="✕")
        cancel_btn.set_tooltip_text("Cancel and discard")
        cancel_btn.connect("clicked", lambda _b: self.on_cancel())
        box.pack_start(cancel_btn, False, False, 0)

        self._start_time = time.time()
        GLib.timeout_add(500, self._tick)
        self.show_all()

    @staticmethod
    def _draw_dot(widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        cr.set_source_rgb(0.9, 0.15, 0.15)
        cr.arc(w / 2, h / 2, min(w, h) / 2, 0, 2 * 3.14159)
        cr.fill()
        return False

    def _tick(self):
        elapsed = int(time.time() - self._start_time)
        self.time_label.set_text(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        return True  # keep ticking


def time_stamp():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H-%M-%S")


class Recorder:
    def __init__(self, x, y, w, h, with_audio=False):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.folder = gc.load_settings()["folder"]
        self.output_path = gc.unique_path(self.folder, "Recording from " + time_stamp(), "mp4")
        self.with_audio = with_audio and shutil.which("pactl") is not None
        self.process = None
        self.bar = None

    def start(self):
        if not ffmpeg_available():
            gc.notify(
                "Glint",
                "Screen recording needs ffmpeg, which isn't installed.\n"
                "Install it with: sudo apt install ffmpeg",
            )
            Gtk.main_quit()
            return
        cmd = build_ffmpeg_cmd(self.x, self.y, self.w, self.h, self.output_path, self.with_audio)
        self.process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self.bar = ControlBar(self.stop, self.cancel)

    def stop(self):
        if self.process and self.process.poll() is None:
            try:
                # ffmpeg listens for 'q' on stdin and finalizes the mp4
                # container cleanly - much safer than SIGKILL, which can
                # leave an unplayable file.
                self.process.stdin.write(b"q")
                self.process.stdin.flush()
                self.process.wait(timeout=10)
            except Exception:
                self.process.terminate()
        if self.bar:
            self.bar.destroy()
        if os.path.exists(self.output_path):
            gc.notify("Recording saved", self.output_path)
        else:
            gc.notify("Glint", "Recording didn't produce a file - check that ffmpeg can access your display.")
        Gtk.main_quit()

    def cancel(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
        if self.bar:
            self.bar.destroy()
        # Best-effort cleanup of a partial/corrupt file from a cancelled
        # recording - a truncated mp4 isn't useful and would just clutter
        # the screenshots folder.
        try:
            if os.path.exists(self.output_path):
                os.remove(self.output_path)
        except OSError:
            pass
        Gtk.main_quit()


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("full", "region"):
        print("Usage: glint_record.py <full|region>", file=sys.stderr)
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "full":
        pixbuf = gc.capture_full_screen()
        recorder = Recorder(0, 0, pixbuf.get_width(), pixbuf.get_height())
        recorder.start()
        Gtk.main()
        return

    # region: reuse the same drag-select overlay the picker uses for
    # screenshots, just asking it for raw geometry instead of pixels.
    def on_geometry(geo):
        x, y, w, h = geo
        recorder = Recorder(x, y, w, h)
        recorder.start()

    def on_cancel():
        Gtk.main_quit()

    glint_app.SelectionOverlay("region", on_geometry, on_cancel, return_geometry=True)
    Gtk.main()


if __name__ == "__main__":
    main()
