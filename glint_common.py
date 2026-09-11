"""
glint_common.py — shared helpers for Glint: settings, capture primitives,
notifications, styling, and the filename/collision logic. Split out of
glint.py so the editor and recorder don't need to duplicate any of this.
"""
import os
import sys
import json
import shutil
import datetime
import subprocess

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
try:
    gi.require_version("Wnck", "3.0")
    from gi.repository import Wnck
    HAVE_WNCK = True
except Exception:
    HAVE_WNCK = False

from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Gio

APP_NAME = "Glint"
ACCENT = "#E95420"          # Ubuntu orange
ACCENT_DARK = "#C34113"
HEADER_BG = "#3C3C3C"
HEADER_BG_DARK = "#2C2C2C"

CONFIG_DIR = os.path.join(GLib.get_user_config_dir(), "glint")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DELAY_OPTIONS = [("None", 0), ("2 sec", 2), ("3 sec", 3), ("5 sec", 5), ("10 sec", 10)]

DEFAULT_SETTINGS = {
    "copy": True,
    "save": False,
    "folder": None,          # filled in with default_screenshot_dir() if None
    "delay": 0,
    "include_cursor": False,
    "edit_after_capture": False,
}


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def load_settings() -> dict:
    """Persisted user choices. Migrates the old single dest="clipboard"/
    "folder" scheme (from before Copy and Save became independent
    checkboxes) so upgrading doesn't silently reset anyone's preference:
    old "clipboard" -> copy only; old "folder" -> copy AND save, matching
    exactly what that mode used to do."""
    data = dict(DEFAULT_SETTINGS)
    try:
        with open(CONFIG_PATH, "r") as f:
            saved = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        saved = {}

    if "dest" in saved and "copy" not in saved and "save" not in saved:
        if saved["dest"] == "folder":
            saved["copy"], saved["save"] = True, True
        else:
            saved["copy"], saved["save"] = True, False

    data.update({k: v for k, v in saved.items() if k in DEFAULT_SETTINGS})

    if not data.get("folder"):
        data["folder"] = default_screenshot_dir()
    if not data["copy"] and not data["save"]:
        # Never let both end up off - that would make capture a no-op.
        data["copy"] = True
    return data


def save_settings(settings: dict):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        merged = load_settings()
        merged.update({k: v for k, v in settings.items() if k in DEFAULT_SETTINGS})
        with open(CONFIG_PATH, "w") as f:
            json.dump(merged, f)
    except OSError:
        pass  # Not critical enough to interrupt the user over.


# --------------------------------------------------------------------------
# Theming / desktop-environment detection
# --------------------------------------------------------------------------

def detect_dark_mode() -> bool:
    settings = Gtk.Settings.get_default()
    if settings is None:
        return False
    try:
        if settings.get_property("gtk-application-prefer-dark-theme"):
            return True
    except Exception:
        pass
    try:
        theme_name = (settings.get_property("gtk-theme-name") or "").lower()
        if "dark" in theme_name:
            return True
    except Exception:
        pass
    return False


def current_desktop() -> str:
    """Best-effort desktop identifier ("unity", "mate", "gnome", ...),
    lowercased, from the standard XDG env var. Used only to decide
    whether Unity's left-aligned window-button convention applies -
    everything else in Glint works identically regardless of DE."""
    return os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def preferred_decoration_layout():
    """Where possible, ask GTK itself where window buttons go (this is
    what makes a native app match MATE/GNOME's actual configured layout,
    not just a guess) - only falling back to Unity's hardcoded
    left-side layout when we know we're actually running under Unity,
    since Unity is the one DE that doesn't reliably expose this via
    gtk-decoration-layout the way GNOME/MATE do."""
    settings = Gtk.Settings.get_default()
    if settings is not None:
        try:
            layout = settings.get_property("gtk-decoration-layout")
            if layout:
                return layout
        except Exception:
            pass
    if "unity" in current_desktop():
        return "close,minimize,maximize:"
    return None  # let GTK/the theme decide - correct default for MATE/GNOME


# --------------------------------------------------------------------------
# Capture primitives
# --------------------------------------------------------------------------

def capture_full_screen() -> GdkPixbuf.Pixbuf:
    """Grab the whole X11 root window - spans every monitor, no
    stitching needed."""
    root = Gdk.get_default_root_window()
    width, height = root.get_width(), root.get_height()
    return Gdk.pixbuf_get_from_window(root, 0, 0, width, height)


def capture_region(x: int, y: int, width: int, height: int) -> GdkPixbuf.Pixbuf:
    root = Gdk.get_default_root_window()
    x = max(0, x)
    y = max(0, y)
    width = max(1, width)
    height = max(1, height)
    return Gdk.pixbuf_get_from_window(root, x, y, width, height)


def get_pointer_position():
    display = Gdk.Display.get_default()
    seat = display.get_default_seat()
    pointer = seat.get_pointer()
    _screen, x, y = pointer.get_position()
    return x, y


def monitor_geometry_at_pointer():
    """(x, y, w, h) of whichever monitor the mouse is currently on -
    this is what "Current Monitor" capture means: not a fixed monitor
    index, but wherever you're actually looking right now."""
    display = Gdk.Display.get_default()
    x, y = get_pointer_position()
    monitor = display.get_monitor_at_point(x, y)
    if monitor is None:
        monitor = display.get_primary_monitor() or display.get_monitor(0)
    rect = monitor.get_geometry()
    return rect.x, rect.y, rect.width, rect.height


def get_active_window_geometry():
    """Client-area geometry (x, y, w, h) of the currently focused window,
    or None if Wnck isn't available or nothing is focused. Client
    geometry excludes the window manager's frame/titlebar, same as the
    existing click-to-pick Window mode."""
    if not HAVE_WNCK:
        return None
    screen = Wnck.Screen.get_default()
    screen.force_update()
    win = screen.get_active_window()
    if win is None:
        return None
    return win.get_client_window_geometry()


def list_capturable_windows():
    if not HAVE_WNCK:
        return []
    screen = Wnck.Screen.get_default()
    screen.force_update()
    windows = []
    for w in screen.get_windows_stacked():
        if w.is_minimized():
            continue
        if w.get_window_type() not in (Wnck.WindowType.NORMAL, Wnck.WindowType.DIALOG):
            continue
        x, y, width, height = w.get_geometry()
        if width <= 0 or height <= 0:
            continue
        windows.append(w)
    return windows


def draw_cursor_on_pixbuf(pixbuf, capture_x, capture_y):
    """Bakes a simple pointer-arrow glyph onto `pixbuf` at the real
    cursor position, if the cursor is actually within the captured
    rectangle. This draws a generic arrow rather than the theme's real
    cursor pixmap (getting that exactly requires XFixesGetCursorImage,
    which is a lot of extra plumbing for a cosmetic detail) - good
    enough for the common "show where I was pointing" use case in
    tutorials."""
    import cairo

    px, py = get_pointer_position()
    lx, ly = px - capture_x, py - capture_y
    w, h = pixbuf.get_width(), pixbuf.get_height()
    if not (0 <= lx < w and 0 <= ly < h):
        return pixbuf  # cursor isn't inside the captured area

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    cr = cairo.Context(surface)
    Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
    cr.paint()

    # A simple filled arrow, white with a black outline so it reads on
    # any background.
    size = 22
    path = [
        (lx, ly), (lx, ly + size * 0.75), (lx + size * 0.22, ly + size * 0.55),
        (lx + size * 0.38, ly + size * 0.95), (lx + size * 0.5, ly + size * 0.88),
        (lx + size * 0.35, ly + size * 0.48), (lx + size * 0.62, ly + size * 0.48),
    ]
    cr.move_to(*path[0])
    for point in path[1:]:
        cr.line_to(*point)
    cr.close_path()
    cr.set_source_rgb(1, 1, 1)
    cr.fill_preserve()
    cr.set_source_rgb(0, 0, 0)
    cr.set_line_width(1.2)
    cr.stroke()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, w, h)


# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------

def copy_to_clipboard(pixbuf: GdkPixbuf.Pixbuf):
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    clipboard.set_image(pixbuf)
    clipboard.store()


def default_screenshot_dir() -> str:
    pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    base = pictures or os.path.expanduser("~")
    path = os.path.join(base, "Screenshots")
    os.makedirs(path, exist_ok=True)
    return path


def unique_path(folder: str, stem: str, ext: str) -> str:
    """Collision-safe path: 'stem.ext', then 'stem (1).ext', 'stem (2).ext',
    etc. Filenames used to only carry second-level precision, so two
    captures in the same second would silently overwrite each other -
    this checks for real, rather than assuming a timestamp is unique."""
    candidate = os.path.join(folder, f"{stem}.{ext}")
    if not os.path.exists(candidate):
        return candidate
    n = 1
    while True:
        candidate = os.path.join(folder, f"{stem} ({n}).{ext}")
        if not os.path.exists(candidate):
            return candidate
        n += 1


def save_pixbuf_to_folder(pixbuf: GdkPixbuf.Pixbuf, folder: str, prefix="Screenshot from") -> str:
    os.makedirs(folder, exist_ok=True)
    stem = f"{prefix} {datetime.datetime.now().strftime('%Y-%m-%d %H-%M-%S')}"
    path = unique_path(folder, stem, "png")
    pixbuf.savev(path, "png", [], [])
    return path


def notify(title: str, body: str):
    """Real desktop notifications via notify-send (libnotify) - works
    identically under Unity, MATE, and GNOME, unlike relying on a
    Gio.Application (which Glint deliberately doesn't run one of, being
    a short-lived per-invocation process, not a background service)."""
    if shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send", "-a", APP_NAME, "-i", "camera-photo", title, body],
                check=False,
            )
            return
        except OSError:
            pass
    app = Gio.Application.get_default()
    if app is not None:
        notification = Gio.Notification.new(title)
        notification.set_body(body)
        app.send_notification(None, notification)
    else:
        print(f"{title}: {body}")


def open_path(path: str):
    """Opens a file or folder with whatever the desktop considers
    default - xdg-open is the one thing that's consistent across
    Unity/MATE/GNOME/KDE."""
    opener = shutil.which("xdg-open")
    if opener:
        subprocess.Popen([opener, path])
    else:
        notify("Couldn't open", f"xdg-open isn't installed. File is at:\n{path}")


def module_dir():
    return os.path.dirname(os.path.abspath(__file__))


def spawn_helper(script_name, *args):
    """Launches another Glint module (editor/recorder) as an independent
    process - each keeps its own GTK main loop, so there's no nested-
    mainloop juggling, matching Glint's existing philosophy of small,
    short-lived, per-invocation processes rather than a background
    service."""
    script_path = os.path.join(module_dir(), script_name)
    subprocess.Popen([sys.executable, script_path, *[str(a) for a in args]])


# --------------------------------------------------------------------------
# Shared styling
# --------------------------------------------------------------------------

def build_css(is_dark: bool) -> str:
    if is_dark:
        body_bg, panel_bg, border_color = "#2D2D2D", "#3A3A3A", "#555555"
        text_color, dim_color = "#E6E6E6", "#A8A8A8"
        area_hover_bg, area_active_bg = "#4A2E22", "#5A3628"
        folder_hover_bg = "#4A4A4A"
    else:
        body_bg, panel_bg, border_color = "#F2F1F0", "#FFFFFF", "#D0CFCD"
        text_color, dim_color = "#3C3C3C", "#6E6E6E"
        area_hover_bg, area_active_bg = "#FBEEE8", "#F6DED2"
        folder_hover_bg = "#EAE9E7"

    return f"""
window.glint-main {{ background: {body_bg}; }}
headerbar.glint-header {{
    background: {HEADER_BG};
    background-image: linear-gradient(to bottom, {HEADER_BG}, {HEADER_BG_DARK});
    color: #E6E6E6;
    min-height: 34px;
    border-radius: 0px;
    box-shadow: none;
    font-family: "Ubuntu", sans-serif;
}}
headerbar.glint-header .title {{ color: #F2F1F0; font-weight: 500; font-size: 13px; }}
headerbar.glint-header button {{ color: #E6E6E6; background: transparent; border: none; box-shadow: none; }}
headerbar.glint-header button:hover {{ background: rgba(255, 255, 255, 0.08); }}
label {{ font-family: "Ubuntu", sans-serif; color: {text_color}; }}
.glint-area-btn {{ background: {panel_bg}; border: 1px solid {border_color}; border-radius: 3px; padding: 10px 6px; }}
.glint-area-btn:hover {{ background: {area_hover_bg}; border-color: {ACCENT}; }}
.glint-area-btn:active {{ background: {area_active_bg}; }}
.glint-area-btn label {{ color: {text_color}; font-size: 11px; }}
.glint-dest-btn {{ background: {panel_bg}; border: 1px solid {border_color}; border-radius: 3px; color: {text_color}; padding: 6px 10px; }}
.glint-dest-btn:checked {{ background: {ACCENT}; background-image: linear-gradient(to bottom, {ACCENT}, {ACCENT_DARK}); color: white; border-color: {ACCENT_DARK}; }}
.glint-folder-row {{ background: {panel_bg}; border: 1px solid {border_color}; border-radius: 3px; padding: 4px 6px; }}
.glint-folder-row label {{ color: {text_color}; }}
.glint-folder-icon-btn {{ background: transparent; border: none; padding: 2px; }}
.glint-folder-icon-btn:hover {{ background: {folder_hover_bg}; border-radius: 3px; }}
.dim-label {{ color: {dim_color}; }}
.glint-record-btn {{ background: {panel_bg}; border: 1px solid {border_color}; border-radius: 3px; padding: 8px; color: {text_color}; }}
.glint-record-btn:hover {{ background: {area_hover_bg}; border-color: #d02020; }}
.glint-rec-badge {{ color: #dc2929; }}
"""
