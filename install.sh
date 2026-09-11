#!/usr/bin/env bash
# Installs Glint for the current user. No build step - it's Python, so
# this just puts a launcher on your PATH and registers the desktop
# entry / icon. Works the same under Ubuntu Unity and Ubuntu MATE (or
# any other X11 desktop with GTK3 available).
#
# IMPORTANT: Glint is built on GI (GObject Introspection) - the
# python3-gi / gir1.2-gtk-3.0 bindings. This isn't a minor dependency:
# GI is what gives Glint its clipboard support (Gtk.Clipboard), the
# Copy/Save output checkboxes, and the folder-picker dialog. Without it
# properly installed, the app won't even start.
set -euo pipefail

BIN_DIR="${HOME}/.local/bin"
LIB_DIR="${HOME}/.local/lib/glint"
APPS_DIR="${HOME}/.local/share/applications"
ICONS_DIR="${HOME}/.local/share/icons/hicolor/512x512/apps"

echo "==> Checking required dependencies..."
MISSING=()
python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null \
  || MISSING+=("python3-gi gir1.2-gtk-3.0")
python3 -c "import gi; gi.require_version('Wnck','3.0'); from gi.repository import Wnck" 2>/dev/null \
  || MISSING+=("gir1.2-wnck-3.0")

if [ ${#MISSING[@]} -ne 0 ]; then
    echo ""
    echo "Missing dependencies - Glint cannot function without these:"
    for pkg in "${MISSING[@]}"; do
        echo "  - $pkg"
    done
    echo ""
    echo "gir1.2-gtk-3.0 provides clipboard access and the folder-chooser"
    echo "dialog; gir1.2-wnck-3.0 lets Window mode see real windows on"
    echo "both Unity and MATE. Install them with:"
    echo "  sudo apt install ${MISSING[*]}"
    exit 1
fi
echo "    OK - GTK3/GI bindings found."

echo "==> Checking optional dependencies..."
if ! python3 -c "import cairo" 2>/dev/null; then
    echo "    (!) python3-gi-cairo not found - the annotation editor"
    echo "        (pen/arrow/rectangle/text/pixelate/crop) needs it."
    echo "        Install with: sudo apt install python3-gi-cairo"
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "    (!) ffmpeg not found - screen recording needs it."
    echo "        Install with: sudo apt install ffmpeg"
fi
if ! command -v notify-send >/dev/null 2>&1; then
    echo "    (!) notify-send not found - desktop notifications will just"
    echo "        print to the terminal instead. Usually already installed"
    echo "        on both Unity and MATE; if not: sudo apt install libnotify-bin"
fi

mkdir -p "$BIN_DIR" "$LIB_DIR" "$APPS_DIR" "$ICONS_DIR"

echo "==> Installing Glint's modules to $LIB_DIR"
install -m 644 glint_common.py "$LIB_DIR/glint_common.py"
install -m 644 glint.py "$LIB_DIR/glint.py"
install -m 644 glint_editor.py "$LIB_DIR/glint_editor.py"
install -m 644 glint_record.py "$LIB_DIR/glint_record.py"

echo "==> Installing launcher to $BIN_DIR/glint"
cat > "$BIN_DIR/glint" <<EOF
#!/usr/bin/env bash
exec python3 "$LIB_DIR/glint.py" "\$@"
EOF
chmod 755 "$BIN_DIR/glint"

echo "==> Installing desktop entry"
install -m 644 data/glint.desktop "$APPS_DIR/glint.desktop"

echo "==> Installing icon"
install -m 644 data/icons/glint.png "$ICONS_DIR/glint.png"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache "${HOME}/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi

echo ""
echo "Done. Make sure $BIN_DIR is on your PATH (add to ~/.profile if not):"
echo '  export PATH="$HOME/.local/bin:$PATH"'
echo ""
echo "==> Strongly recommended: set up a keyboard shortcut"
echo ""
echo "Glint remembers your Copy/Save/delay/cursor settings and applies"
echo "them automatically, even when launched straight from a shortcut."
echo ""
echo "Unity: Settings > Keyboard > Shortcuts"
echo "MATE:  MATE Control Center > Keyboard Shortcuts > Add Custom Shortcut"
echo ""
echo "  Print                -> glint full"
echo "  Shift+Print          -> glint region"
echo "  Ctrl+Print           -> glint window"
echo "  Super+Print          -> glint record-full     # record the whole screen"
echo "  Super+Shift+Print    -> glint record-region    # drag a region, then record it"
echo "  (no modifier at all) -> glint                  # opens the picker"
echo ""
echo "If a shortcut silently does nothing, some keybinding daemons run"
echo "commands with a stripped PATH - use the full path instead:"
echo "  $BIN_DIR/glint region"
