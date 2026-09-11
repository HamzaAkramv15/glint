# Glint

A modern screenshot and screen-recording tool for X11 Linux desktops —
built for Ubuntu Unity, and now Ubuntu MATE too. Pick **Screen**,
**This Monitor**, **Active Window**, **Window**, or **Selection** —
each fires immediately. Independent **Copy to clipboard** and
**Save to folder** checkboxes control output, with an optional capture
delay, an optional cursor overlay, and an optional detour through a
built-in annotation editor before finishing. **Record Screen** /
**Record Region** capture video instead of a still, via ffmpeg.
Styled to match Unity's Ambiance chrome where that's actually the
active desktop, and falls back to whatever your theme's own window-
button layout is everywhere else (MATE, GNOME, etc.) — with a
dark-mode palette for dark GTK themes either way.

## What's new in this version

- **Ubuntu MATE support** — window-button layout now follows GTK's
  own `gtk-decoration-layout` setting instead of always assuming
  Unity's left-aligned convention; only actually hardcodes that when
  `XDG_CURRENT_DESKTOP` really says Unity.
- **This Monitor** and **Active Window** as capture modes — available
  via `glint monitor` / `glint active-window` for a keyboard shortcut;
  kept out of the picker's Screenshot tab to keep it to the three
  buttons that cover almost every use (Screen / Window / Selection).
- **Tabbed picker**: Screenshot / Options / Record, instead of one
  window with everything visible at once. Options are explicitly
  shared between Screenshot and Record (delay, output folder).
- **Capture delay** (2/3/5/10s) for menus, tooltips, and anything that
  disappears the instant you'd otherwise hit the shortcut.
- **Include cursor** toggle.
- **Independent Copy / Save checkboxes** replacing the old single
  Clipboard-or-Folder switch — you can now have both, or either, on
  their own terms.
- **Filename collision protection** — two captures in the same second
  used to silently overwrite each other; now the second one becomes
  `... (1).png`.
- **Real desktop notifications** via `notify-send`, working
  consistently under Unity, MATE, and GNOME (the old code only worked
  if a `Gio.Application` happened to be running, which Glint never
  actually does — it fell back to printing to the terminal).
- **A real annotation/preview editor** (`glint_editor.py`) — Pen,
  Arrow, Rectangle, Text, Pixelate (for redacting private info), Crop,
  undo/redo, and Copy / Save / Save As / Open. Opt in via "Open editor
  before finishing"; off by default so instant capture stays instant.
- **Screen recording** (`glint_record.py`) — Record Screen or Record
  Region, via ffmpeg's `x11grab`, with a small floating Stop control
  and elapsed timer. Needs `ffmpeg` installed.
- Split into `glint.py` / `glint_common.py` / `glint_editor.py` /
  `glint_record.py` so the editor and recorder don't duplicate capture
  or settings logic, and each runs as its own short-lived process —
  same "small process per invocation" philosophy as before, just
  spread across a few files now instead of one 855-line one.

**Deliberately not included yet** (separate future projects, not
scope-creep on this pass): OCR, cloud upload/sharing, screenshot
history/search, Wayland support, true isolated-window capture via
XComposite, numbered-step annotations, aspect-ratio-locked selection,
and configurable in-app shortcuts (the OS's own keyboard shortcut
settings work fine for that already).

## Installing

**This step matters more than it might look — don't skip it.** Glint
is built entirely on GI (GObject Introspection), the `python3-gi` /
`gir1.2-gtk-3.0` bindings.

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-wnck-3.0
./install.sh
```

Two more packages are *optional*, needed only for specific features —
`install.sh` will tell you if they're missing, but won't refuse to
install without them:

- `python3-gi-cairo` — needed for the annotation editor
  (`Open editor before finishing`, or the Edit button in a preview).
- `ffmpeg` — needed for Record Screen / Record Region.

`install.sh` puts the four `.py` modules in `~/.local/lib/glint/` and
a small launcher script at `~/.local/bin/glint` that runs
`glint.py` from there — plus a `.desktop` entry and icon, no `sudo`,
no build step. Shows up in the Unity dash or MATE menu immediately.

To remove it later:
```bash
./uninstall.sh
```
This removes the launcher, the `~/.local/lib/glint/` modules, the
desktop entry, and the icon. It'll ask before deleting your saved
preferences in `~/.config/glint/` — say no if you think you'll
reinstall later and want them to carry over.

## Binding to keys (recommended — this is where Glint actually pays off)

Glint remembers your Copy/Save/delay/cursor settings and applies them
automatically, even when launched straight from a keyboard shortcut
with the picker window never appearing at all.

**Unity**: Settings → Keyboard → Shortcuts
**MATE**: MATE Control Center → Keyboard Shortcuts → Add Custom Shortcut

| Shortcut         | Command          |
|-------------------|------------------|
| `Print`            | `glint full`     |
| `Shift+Print`       | `glint region`   |
| `Ctrl+Print`        | `glint window`   |
| `Super+Print`         | `glint record-full`     |
| `Super+Shift+Print`    | `glint record-region`  |
| *(anything else)*   | `glint` — opens the picker |

If a shortcut silently does nothing, some keybinding daemons run
commands with a stripped-down `PATH` that doesn't include
`~/.local/bin`. Use the full path instead:
```
/home/YOUR_USERNAME/.local/bin/glint region
```


## Behavior notes

- **Clicking a capture mode fires immediately** — there's no separate
  "Take Screenshot" button to click afterward.
- **Clipboard** destination copies only — nothing is written to disk.
- **Folder** destination saves a file *and* copies to clipboard, so
  you get both a permanent copy and something immediately pasteable.
- **Your destination choice persists.** Whatever you pick (Clipboard
  vs. Folder, and which folder) is saved to
  `~/.config/glint/config.json` and becomes the default next time you
  open Glint — including when launched via a keyboard shortcut like
  `glint full`, which skips the picker UI entirely.
- The folder path shown is always the real current path — the default
  `~/Screenshots` or whatever you last chose — never blank. Click the
  small folder icon next to it to change it; this does *not* pop open
  automatically just from selecting "Folder".

## Why Python + GTK3

An earlier version of this was Rust + GTK4. It kept hitting real,
reproducible bugs that all traced back to fighting the wrong tools for
this environment:

- GTK4's fullscreen negotiation with Compiz (Unity's compositor) was
  unreliable — a resizable hint was enough to leave the selection
  overlay invisible in a corner while clicks landed on the desktop
  underneath it.
- `GApplication`'s default argv handling tried to interpret CLI args
  like `region` as files to open, silently aborting activation.
- Rust's borrow checker caught real bugs, but debugging a `RefCell`
  double-borrow crash deep in a GTK signal callback isn't a good use
  of anyone's time for a tool this size.

Python + GTK3 sidesteps all three:

- A `Gtk.WindowType.POPUP` overlay **bypasses the window manager
  entirely** — it always appears at the exact geometry requested, no
  negotiation with Compiz needed. This is the standard pattern used by
  other region-select screenshot tools on X11.
- GTK3's clipboard supports `.store()`, which persists the copied
  image after the process exits — no manual daemonizing.
- X11's root window already spans every monitor, so full-screen
  capture is one call, no stitching.
- No GObject application framework fighting over argv — plain
  `sys.argv` parsing, plain `Gtk.main()`.

Every code path in this app (full screen, region drag, window click,
clipboard copy, folder save, Escape-to-cancel, settings persistence,
dark mode, install/uninstall) was tested by actually running it under
a virtual X server with simulated mouse/keyboard input before being
shipped — not just read over.

## How it works

```
glint_common.py          shared helpers, used by all three entry points
  load_settings() / save_settings()   ~/.config/glint/config.json
  capture_full_screen() / capture_region(x,y,w,h)
  monitor_geometry_at_pointer()        "This Monitor" support
  get_active_window_geometry()         "Active Window" support
  list_capturable_windows()            enumerate real windows via Wnck
  unique_path()                        collision-safe filenames
  notify()                             real notify-send notifications
  preferred_decoration_layout()        Unity vs MATE/GNOME window buttons
  build_css(is_dark)                   shared stylesheet

glint.py                 the picker + fast capture path
  SelectionOverlay        POPUP window for region-drag / window-click,
                            can return a pixbuf OR raw geometry
  MainWindow              Capture Area, Delay, Include cursor, Output
                            (Copy/Save), Record - each area button fires
                            capture immediately

glint_editor.py           optional post-capture preview/annotation editor
  EditorWindow             Pen/Arrow/Rectangle/Text/Pixelate/Crop,
                            undo/redo, Copy/Save/Save As/Open
                            (spawned as its own process, own Gtk.main())

glint_record.py            screen recording via ffmpeg x11grab
  ControlBar                floating elapsed-time + Stop/Cancel bar
  Recorder                  builds/runs/stops the ffmpeg subprocess
                              (spawned as its own process, own Gtk.main())
```

Each of `glint.py`, `glint_editor.py`, and `glint_record.py` runs as an
independent process with its own `Gtk.main()` — the picker spawns the
editor or recorder and exits, rather than nesting event loops. Same
philosophy as the original: short-lived, per-invocation processes, no
background service.

## Known limitations

- **Window capture** crops using the window's *client* geometry (via
  Wnck's `get_client_window_geometry()`), which excludes the window
  manager's own frame — title bar, borders — so that grey chrome
  doesn't end up in the saved image. It still crops from a single
  full-screen backdrop rather than compositing the window in isolation,
  so if another window overlaps it at the moment you click, that
  overlap will show up in the crop. Fixing that properly needs
  XComposite off-screen buffers, which is a reasonable v2 addition but
  was cut here to keep the tool simple and dependency-light.
- **Clipboard persistence** relies on GTK3's `.store()`, which hands
  the image off to whatever is listening for clipboard requests. This
  is the standard mechanism and works the same way `xclip -selection
  clipboard` and other GTK3 apps behave — no daemon required.
- **Dark mode detection** is best-effort: it checks GTK's
  `gtk-application-prefer-dark-theme` setting and falls back to
  checking whether the active theme's name contains "dark". If your
  setup uses neither signal, Glint will render in its light palette
  regardless of your actual desktop mood.

## License

MIT — do whatever you want with it.

