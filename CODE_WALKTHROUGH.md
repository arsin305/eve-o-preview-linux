# PodSight — Code Walkthrough

> A developer-friendly guide to the PodSight codebase (formerly *EVE-O Preview for Linux*).

---

## What This Program Does

PodSight is a **live window-preview tool** for [EVE Online](https://www.eveonline.com/) players who multibox (run multiple game clients simultaneously). It creates small, floating, live-updating thumbnail windows — one per EVE client — so you can monitor all your characters at a glance. Clicking a thumbnail brings that client into focus.

It is an independent Linux implementation (not a port of the Windows EVE-O Preview) and handles the extra complexity of X11, Wayland, XWayland, Wine/Proton, and KDE Plasma / GNOME.

### User-Facing Features

- **Live thumbnails** — server-side scaled capture at 10–30 FPS in floating overlay windows
- **Click to focus** — left-click a thumbnail to switch to that EVE client
- **Drag to reposition** — left-drag or right-drag; positions persist across sessions
- **Ctrl+Click to minimize** the corresponding client
- **Zoom on hover** with configurable factor
- **Active client highlight** — colored border on the focused client's thumbnail
- **System tray** — Show/Hide menu, optional close-to-tray and start-in-tray
- **Hotkey switching** — Ctrl+Alt+arrows cycle clients, Ctrl+Alt+1-9 jump directly (focus only, never input)
- **Pins, snapping, layouts** — middle-click pin, live 32 px grid + edge magnetism, named layout profiles
- **Settings dialog** — size, opacity, FPS, border color, behavior toggles

---

## Architecture Overview

```
┌───────────────────────────────────────────────────────────┐
│                 podsight.py — entry point                 │
│  imports platform FIRST (env setup), sets prgname/icon,   │
│  wires tray + delete-event, starts Wnck + GTK main loop   │
└──────────────────────────┬────────────────────────────────┘
                           │
                           ▼
┌───────────────────────────────────────────────────────────┐
│                     app.EVEOPreview                       │
│               Main management window (GTK)                │
│  • Detects EVE clients via Wnck signals + title watchers  │
│  • Creates/destroys one ThumbnailWindow per client        │
│  • Tracks the active window, drives border highlights     │
│  • Hosts SettingsDialog                                   │
└──────────────┬────────────────────────────────────────────┘
               │  one per detected EVE client
               ▼
┌───────────────────────────────────────────────────────────┐
│                 thumbnail.ThumbnailWindow                 │
│  • Capture tick (GLib timer, PRIORITY_LOW)                │
│  • capture.grab_scaled() fast path                        │
│  • Legacy Gdk fallback + Wine child-XID rebinding         │
│  • Click / drag / hover-zoom input                        │
│  • Delegates display to _LayerShellDisplay on Wayland     │
└───────┬──────────────────────────────┬────────────────────┘
        │ every frame                  │ Wayland only
        ▼                              ▼
┌──────────────────────┐   ┌───────────────────────────────┐
│    capture (module)  │   │  layer_shell._LayerShellDisplay│
│ XComposite + XRender │   │  subprocess wrapper, pipe IPC  │
│ server-side scaling  │   │  spawns _LAYER_SHELL_HELPER    │
└──────────────────────┘   │  (GDK_BACKEND=wayland,         │
                           │   gtk-layer-shell OVERLAY)     │
                           └───────────────────────────────┘

Supporting modules: platform (env/gi/X11 ctypes), config (settings +
migration), stats (--stats counters), tray (StatusNotifier icon).
```

### Why the Subprocess Architecture (Wayland)?

The main process **must** use the X11 GDK backend because `Wnck` (window enumeration) only works on X11. But on Wayland, X11 windows can't reliably float above fullscreen XWayland surfaces. So each thumbnail can be displayed by a child process running `GDK_BACKEND=wayland` with **gtk-layer-shell**, which creates a compositor-level OVERLAY surface that is unconditionally above everything. Main process and helper speak a small text protocol over stdin/stdout.

### Why Server-Side Capture?

The original implementation called `Gdk.pixbuf_get_from_window()` per frame per client — a synchronous `XGetImage` that copies the **full window** (~8 MB at 1080p+) over the X socket, then CPU-scales it. Measured cost: ~30 ms per capture; three clients saturated the GLib main loop at ~10 fps each.

The current pipeline (`capture.py`):

1. `XCompositeRedirectWindow` (once per window) + `XCompositeNameWindowPixmap` — a server-side reference to the window's offscreen backing, no pixels copied
2. `XRenderSetPictureTransform` + `XRenderComposite` — the X server scales down to thumbnail size (GPU-accelerated under XWayland/glamor)
3. `XGetImage` on the **small** destination pixmap — only ~250 KB crosses the socket
4. Byte-swizzle BGRX→RGB using Python slice assignment (C speed) into a `GdkPixbuf`

Measured result: ~2–3 ms per capture, 25 fps locked with three clients, RSS down from ~200 MB to ~70 MB (no more full-size intermediate pixbufs).

---

## Package Layout

```
podsight/
├── podsight.py                 entry point (~90 lines)
├── install.sh / uninstall.sh   per-user installer (XDG paths)
├── assets/                     icon (SVG) + .desktop template
└── podsight_pkg/
    ├── __init__.py             version string
    ├── platform.py             env + gi init, X11 ctypes helpers   (~250)
    ├── capture.py              XComposite/XRender capture          (~300)
    ├── layer_shell.py          Wayland helper + IPC wrapper        (~460)
    ├── config.py               settings load/save + migration      (~60)
    ├── stats.py                --stats performance counters        (~95)
    ├── thumbnail.py            per-client preview window           (~680)
    ├── app.py                  detection + management window       (~510)
    ├── settings_dialog.py      settings UI                         (~440)
    ├── tray.py                 StatusNotifier tray icon            (~60)
    └── hotkeys.py              XGrabKey client switching           (~185)
```

---

## Module Breakdown

### `podsight.py` — entry point

The **import order is a contract**: `from podsight_pkg import platform` must be the first package import, because `platform` sets `GDK_BACKEND` *before* gi loads GTK. Then `main()`:

- `GLib.set_prgname("podsight")` + `Gdk.set_program_class("podsight")` — makes `WM_CLASS = "podsight","podsight"` so Plasma's task manager matches the `.desktop` file (its `StartupWMClass=podsight`)
- `Gtk.Window.set_default_icon_name("podsight")` — every window advertises the themed icon (titlebar/taskbar)
- Creates the tray if available; wires `delete-event` so closing hides to tray only when the `close_to_tray` setting is on; `destroy` still quits
- Keep-above is re-asserted on `map` and on a 4 s timer (a `window-state-event` handler would feed back into itself and freeze the UI on alt-tab)

### `platform.py` — environment, gi, X11 helpers

Import side effects, in order: Wayland session detection (forces `GDK_BACKEND=x11`; EVE runs via XWayland so capture works), a subprocess probe for gtk-layer-shell (must be tested under `GDK_BACKEND=wayland`, hence the subprocess), then `gi.require_version` for Gtk/Wnck/GdkX11 with a friendly error naming the dnf/apt packages.

Also owns: CLI flags (`--debug`, IPC debug env vars), `SELF_PID`/`SCRIPT_BASENAME` (self-window exclusion, from `argv[0]` — `__file__` would name the module), and the ctypes Xlib helpers:

- `_net_activate_window(xid)` — sends `_NET_ACTIVE_WINDOW` with source=1 (direct user action), which KWin honors despite focus-stealing prevention; re-maps withdrawn windows first; falls through `XSetInputFocus` and `WM_TAKE_FOCUS`
- `_get_child_xids(parent)` — `XQueryTree`, used for the Wine "Fixed Window" case below

### `capture.py` — server-side capture

Stateless module API: `capture.AVAILABLE` and `grab_scaled(xid, src_w, src_h, dst_w, dst_h) -> GdkPixbuf | None`.

Details worth knowing before touching it:

- **Own display connection** with a no-op X error handler (kept referenced to survive GC). A window can be destroyed between our request and the server processing it; `BadWindow`/`BadDrawable` set a flag instead of killing the process. The flag is checked after `XGetImage` (the only round trip, which surfaces earlier async errors).
- **Caches:** redirected-XID set, per-XID `XRenderPictFormat` (from the window's visual), and one destination pixmap+picture per thumbnail size. Per-frame resources (named pixmap, source picture, XImage) are freed in a `finally`.
- **Channel-order gotcha:** `XGetImage` on a *pixmap* reports zero RGB masks (masks come from a visual). Since the destination is always `PictStandardRGB24`, the default layout is BGRX little-endian; nonzero masks override.
- **Contract:** any failure returns `None`; the caller's legacy path then runs. Never raise across this boundary.

### `thumbnail.py` — per-client preview

One `Gtk.Window` per client (type hint UTILITY, skip-taskbar/pager — thumbnails deliberately don't appear in the task manager). The capture tick runs on a `GLib` timer at **`PRIORITY_LOW`** so frame updates always yield to input events and IPC callbacks — this is what keeps the UI responsive with many clients.

Tick order: minimized → icon fallback; not viewable → icon fallback; `capture.grab_scaled()`; on `None`, legacy `Gdk.pixbuf_get_from_window` + `scale_simple`. If the legacy path also yields nothing, the window may be a Wine **Fixed Window** whose content renders into a *child* XID: `_try_bind_child()` (rate-limited to once per second) walks `XQueryTree` children and rebinds capture to the topmost child with a real size.

Also here: click/drag state machine (click-vs-drag threshold), Ctrl+click minimize, hover zoom, position persistence (debounced saves), and the layer-shell delegation (`self._use_ls`).

### `layer_shell.py` — Wayland OVERLAY display

`_LAYER_SHELL_HELPER` is a Python script kept as a string; each thumbnail spawns it with `GDK_BACKEND=wayland`. It builds a GtkLayerShell OVERLAY window and exchanges newline-delimited commands: frames go down as base64 pixel data; `CLICK`/`ENTER`/`LEAVE`/`POS` come back. `_LayerShellDisplay` wraps one child process: writer with a queue (frames dropped if the pipe backs up rather than blocking the main loop), reader thread dispatching via `GLib.idle_add`. The helper uses `XQueryPointer` via ctypes because GDK3's Wayland "root" coordinates are wrong for layer-shell surfaces.

### `app.py` — detection + management window

Client discovery is deliberately conservative (a wrong match once caused multi-client hangs):

1. Reject our own windows (`SELF_PID`, title containing the script basename / "podsight")
2. Accept immediately on the stable title `EVE - <CharacterName>`
3. Reject launcher signatures (title heuristics + `/proc/<pid>/cmdline` markers like `qtwebengine`)
4. Reject transient titles (`untitled window`, bare `eve`) — a name-changed watcher adds them once the character loads
5. Last resort: accept on client-process cmdline markers (`exefile.exe`, `steam_app_8500`, …)

Wnck signals (`window-opened`/`closed`, `active-window-changed`) plus periodic rescans drive thumbnail lifecycle; the active-border poll runs at 150 ms while settling.

### `hotkeys.py` — client-switching hotkeys

XGrabKey on the X/XWayland root (own display connection, GLib fd watch), grabbing every CapsLock/NumLock modifier variant. Cycling and the 1-9 direct keys resolve targets through `app._ordered_xids()` — **on-screen order**, the same source of truth the badges and thumbnail bars display. 250 ms debounce tames key auto-repeat; colliding grabs (existing KDE shortcuts) are reported per-combo and skipped. Scope is deliberately focus-only: sending input to clients violates CCP's EULA and is out of scope by design.

Related machinery elsewhere: thumbnails expose `activate_client()` (the same cascade clicks use), pins live in `thumbnail.py` (middle-click, persisted per window name, enforced on both display paths), live grid/edge snapping runs inside the layer-shell helper (float accumulator to avoid quantization feedback; sibling rects pushed via the `RECTS` IPC message), and named layouts are position profiles in `app.py` applied in screen order.

### `config.py`, `stats.py`, `tray.py`

- **config** — JSON at `~/.config/podsight/config.json`; defaults merged over file contents so new keys appear automatically. One-time migration *copies* (not moves) the old `eve-o-preview-linux` config.
- **stats** — enabled by `--stats`; thumbnails register counter records, the tick records capture+scale ms, a 5 s GLib timer prints per-thumbnail achieved FPS, average capture time, and RSS (from `/proc/self/status`). Zero overhead when off (`STATS is None`).
- **tray** — StatusNotifier via AyatanaAppIndicator3; `TRAY_AVAILABLE=False` if the typelib is absent, and the two tray settings grey out with an install hint. The tray owns no state: Show/Hide toggles the management window, Quit calls `Gtk.main_quit`.

---

## Key Design Decisions

**Why ctypes instead of python-xlib?** No third-party dependencies — everything ships with a stock Fedora/GTK install. The bindings are small and local to where they're used.

**Why a package instead of one file?** At ~2,400 lines the single file mixed six concerns. The split (done as a purely mechanical refactor, verified against recorded `--stats` baselines) keeps each module under ~700 lines and gave the capture rewrite a clean home.

**Why XComposite/XRender instead of PipeWire?** EVE always runs through Wine/Proton → XWayland, so its windows are X windows even on a Wayland desktop; X capture works everywhere PodSight can run and needs no portal permission dialogs. PipeWire capture would only matter for native Wayland windows, which EVE never is.

**Why keep the legacy capture path?** It costs ~15 lines, covers X servers without the extensions, and — more importantly — its failure mode drives the Wine child-XID rebinding logic. `grab_scaled()` returning `None` deliberately falls through to it.

**Why the activation cascade?** No single activation method works across KWin configurations and XWayland quirks. `_NET_ACTIVE_WINDOW` → `XSetInputFocus`/`WM_TAKE_FOCUS` → `wmctrl` → `xdotool`, first success wins.

---

## Dependencies

### Required (system packages, Fedora names)

- `python3`, `python3-gobject`, `gtk3`, `libwnck3`

### Optional

- `gtk-layer-shell` — Wayland OVERLAY thumbnails (above fullscreen EVE)
- `wmctrl`, `xdotool` — extra click-to-focus fallbacks
- `libayatana-appindicator-gtk3` — system tray

No pip installs; standard library only beyond GObject introspection.

---

## Debug & Diagnostics

```bash
podsight --debug     # per-frame capture diagnostics
podsight --stats     # per-thumbnail FPS / capture ms / RSS every 5 s (stderr)
PODSIGHT_IPC_DEBUG=1 podsight   # layer-shell IPC message tracing
```

Menu launches log to `~/.local/state/podsight/podsight.log` (rotated at ~1 MB).

---

## Installed Layout (install.sh)

| Path | Contents |
|---|---|
| `~/.local/share/podsight/` | application files |
| `~/.local/bin/podsight` | launcher wrapper (logging + rotation) |
| `~/.local/share/applications/podsight.desktop` | menu entry (absolute Exec path substituted at install time) |
| `~/.local/share/icons/hicolor/scalable/apps/podsight.svg` | icon |
| `~/.config/podsight/` | settings (survives uninstall) |
| `~/.local/state/podsight/` | logs (survives uninstall) |

---

## Contributing Notes

- Work one change at a time on a branch; keep modules focused.
- For anything touching the capture or tick path, record `--stats` output before and after with multiple clients — performance regressions here are the project's main risk.
- `capture.grab_scaled()` must never raise; return `None` and let the fallback run.
- The `platform`-first import order is load-bearing. New gi-using modules start with `from . import platform`.
- Detection heuristics in `app.py` have history — read the inline comments before "simplifying" them.
