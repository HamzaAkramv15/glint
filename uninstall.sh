#!/usr/bin/env bash
# Removes everything install.sh put in place: the launcher, the module
# files, the desktop entry, the icon, and (optionally) your saved
# preferences.
set -euo pipefail

BIN_DIR="${HOME}/.local/bin"
LIB_DIR="${HOME}/.local/lib/glint"
APPS_DIR="${HOME}/.local/share/applications"
ICONS_DIR="${HOME}/.local/share/icons/hicolor/512x512/apps"
CONFIG_DIR="${HOME}/.config/glint"

echo "==> Removing $BIN_DIR/glint"
rm -f "$BIN_DIR/glint"

echo "==> Removing $LIB_DIR"
rm -rf "$LIB_DIR"

echo "==> Removing desktop entry"
rm -f "$APPS_DIR/glint.desktop"

echo "==> Removing icon"
rm -f "$ICONS_DIR/glint.png"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache "${HOME}/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi

if [ -d "$CONFIG_DIR" ]; then
    read -r -p "Also remove saved preferences in $CONFIG_DIR? [y/N] " REPLY
    if [[ "$REPLY" =~ ^[Yy]$ ]]; then
        rm -rf "$CONFIG_DIR"
        echo "Removed $CONFIG_DIR"
    fi
fi

echo ""
echo "Glint has been uninstalled."
echo "If you bound any keyboard shortcuts to it, remove those entries"
echo "manually - they aren't tracked here."
