# EVE-O Preview for Linux — Code Walkthrough

> A developer-friendly guide to understanding the codebase of `eve_o_preview_linux.py`.

---

## What This Program Does

EVE-O Preview is a **live window-preview tool** for [EVE Online](https://www.eveonline.com/) players who multibox (run multiple game clients simultaneously). It creates small, floating, live-updating thumbnail windows — one per EVE client — so you can monitor all your characters at a glance. Clicking a thumbnail brings that game client into focus.

The Windows version of this tool has existed for years. This is a **Linux-native port** that must handle the extra complexity of X11, Wayland, XWayland, Wine/Proton, and multiple desktop environments (KDE Plasma, GNOME, etc.).

### User-Facing Features

- **Live thumbnails** — Periodic screen capture at 10–30 FPS, scaled down and displayed in floating overlay windows.
- **Click to focus** — Left-click a thumbnail to switch to that EVE client.
- **Drag to reposition** — Left-drag or right-drag to move thumbnails around.
- **Ctrl+Click to minimize** — Minimize the corresponding game client.
- **Zoom on hover** — Thumbnails enlarge when the mouse hovers over them (configurable factor).
- **Active client highlight** — The thumbnail for the focused client gets a colored border.
- **Position memory** — Thumbnail positions persist across sessions.
- **Settings dialog** — GUI for thumbnail size, opacity, FPS, border color, and behavior toggles.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                    main() entry point                    │
│                 Initializes Wnck + GTK loop              │
└────────────────────────┬─────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────┐
│                     EVEOPreview                          │
│              Main management window (GTK)                │
│  • Monitors desktop for EVE windows via Wnck signals     │
│  • Manages list of ThumbnailWindow instances              │
│  • Tracks active window and updates borders               │
│  • Hosts Settings dialog                                  │
└────────────┬─────────────────────────────────────────────┘
             │  creates one per detected EVE client
             ▼
┌──────────────────────────────────────────────────────────┐
│                   ThumbnailWindow                        │
│            One per EVE client (GTK.Window)                │
│  • Captures screenshots via GdkX11                        │
│  • Scales and displays in an image widget                 │
│  • Handles click/drag/hover input                         │
│  • Delegates display to LayerShellDisplay on Wayland      │
└────────────┬─────────────────────────────────────────────┘
             │  (Wayland only)
             ▼
┌──────────────────────────────────────────────────────────┐
│               _LayerShellDisplay                         │
│         Subprocess wrapper (IPC over stdin/stdout)        │
│  • Spawns _LAYER_SHELL_HELPER as a child process          │
│  • Sends frames, position, size, active state             │
│  • Receives CLICK, POS, ENTER, LEAVE events               │
└────────────┬─────────────────────────────────────────────┘
             │  subprocess (GDK_BACKEND=wayland)
             ▼
┌──────────────────────────────────────────────────────────┐
│              _LAYER_SHELL_HELPER script                   │
│         Wayland OVERLAY window via gtk-layer-shell        │
│  • Renders thumbnail image + character label              │
│  • Handles mouse input, reports back via stdout           │
│  • Guaranteed to float above fullscreen surfaces          │
└──────────────────────────────────────────────────────────┘
```

### Why the Subprocess Architecture?

The main process **must** use the X11 GDK backend because `Wnck` (the window-list library) only works on X11. But on Wayland, regular X11 windows can't reliably float above fullscreen XWayland surfaces. The solution: spawn each thumbnail as a separate process using `GDK_BACKEND=wayland` with `gtk-layer-shell`, which creates a compositor-level OVERLAY that is unconditionally above everything. The two processes communicate via a simple text-based protocol over pipes.

---

## File Structure (Single File)

The entire application lives in one `~2,300-line` Python file. Here's a map of what's where:

| Lines | Section | Purpose |
|-------|---------|---------|
| 1–9 | Header | Shebang, feature summary |
| 11–51 | **Wayland Detection** | Detect session type, force X11 backend, check for `gtk-layer-shell` |
| 52–65 | **Imports** | GTK3, Wnck, GdkX11, ctypes — with user-friendly error messages |
| 73–347 | **`_LAYER_SHELL_HELPER`** | Embedded Python script run as subprocess for Wayland overlays |
| 352–514 | **`_LayerShellDisplay`** | Main-process IPC wrapper for one layer-shell subprocess |
| 516–690 | **Xlib Helpers** | Direct X11 C calls via ctypes for window activation + child discovery |
| 692–760 | **EVE Window Detection** | Filters desktop windows to find real EVE game clients |
| 762–800 | **`Config`** | JSON-based settings persistence (`~/.config/eve-o-preview-linux/`) |
| 802–1448 | **`ThumbnailWindow`** | Core class — live capture, display, click/drag/zoom per client |
| 1450–1825 | **`EVEOPreview`** | Main app window — client list, window monitoring, active tracking |
| 1827–2238 | **`SettingsDialog`** | Two-tab settings UI (Display + Behavior) |
| 2240–2287 | **`main()`** | Entry point — Wnck init, GTK event loop, keep-above workarounds |

---

## Detailed Section Breakdown

### 1. Wayland Detection & Backend Forcing

**Lines 11–51**

```python
_WAYLAND_SESSION = bool(os.environ.get("WAYLAND_DISPLAY") or
                        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland")
if _WAYLAND_SESSION:
    os.environ["GDK_BACKEND"] = "x11"
```

**Problem:** EVE Online runs through Wine/Proton, which uses XWayland (X11 emulation inside Wayland). The screen-capture technique (`gdk_pixbuf_get_from_window`) only works through X11/GdkX11.

**Solution:** Force GTK to use the X11 backend regardless of the session type. Then, optionally, detect `gtk-layer-shell` availability by spawning a quick test subprocess — this determines whether thumbnails can use Wayland OVERLAY mode (which guarantees they float above fullscreen games).

**Key globals set here:**
- `_WAYLAND_SESSION` — `True` if running under Wayland
- `_LAYER_SHELL_AVAILABLE` — `True` if `gtk-layer-shell` is installed and functional

---

### 2. GTK / System Imports

**Lines 52–65**

Loads GTK 3.0, Wnck 3.0, and GdkX11 3.0 via GObject Introspection (`gi`). If any typelib is missing, it prints distro-specific install commands and exits. This is a much better experience than letting Python throw an opaque `ImportError`.

**Dependencies loaded:**
- `Gtk` — UI toolkit
- `Gdk` / `GdkPixbuf` — Graphics, screen capture, image scaling
- `Wnck` — Window list, active-window tracking, minimize/unminimize
- `GdkX11` — X11-specific display and window access
- `GLib` — Event loop, timers, idle callbacks
- `ctypes` — Foreign function interface for calling C libraries

---

### 3. Layer-Shell Helper Script

**Lines 73–347** — `_LAYER_SHELL_HELPER`

This is a **complete, self-contained Python program stored as a multi-line string**. It gets executed as a subprocess via `python3 -c <script>`. Each thumbnail on Wayland gets its own instance.

#### `_X11Ptr` (lines 84–120)
Loads `libX11.so.6` via ctypes and calls `XQueryPointer` to get the real screen-absolute cursor position. This is necessary because GDK3's Wayland backend computes root coordinates incorrectly for layer-shell surfaces (it assumes `origin = (0,0)`).

#### `_Thumb` (lines 128–309)
A `Gtk.Window` subclass configured as a Wayland overlay:

```python
GtkLayerShell.init_for_window(self)
GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
```

Handles:
- **Frame display** — Receives raw pixel data, creates a `GdkPixbuf`, sets it on the image widget.
- **Border drawing** — Uses Cairo's `connect_after("draw")` to paint a colored border on top of the image (not under it).
- **Mouse events** — Distinguishes click vs. drag using a pixel-distance threshold. Reports events as text to stdout: `CLICK`, `CTRL_CLICK`, `POS x y`, `ENTER`, `LEAVE`.
- **Debouncing** — Suppresses rapid double-click events (GTK generates press+release twice for a double-click).

#### `_reader` (lines 314–346)
A background thread that reads commands from stdin and dispatches them to the GTK main loop via `GLib.idle_add`. Supported commands:

| Command | Format | Action |
|---------|--------|--------|
| `FRAME` | `FRAME w h rowstride base64data` | Update displayed image |
| `POS` | `POS x y` | Move window |
| `SIZE` | `SIZE w h` | Resize window |
| `ACTIVE` | `ACTIVE 0\|1 #color` | Set border highlight |
| `TITLE` | `TITLE text` | Update character name label |
| `SHOW` | `SHOW` | Show window |
| `HIDE` | `HIDE` | Hide window |
| `QUIT` | `QUIT` | Exit subprocess |

---

### 4. `_LayerShellDisplay` Class

**Lines 352–514**

The main-process counterpart to the helper script. Manages one subprocess and exposes a clean API.

#### Queue Architecture
```
Main GTK thread
    │
    ├──► _ctrl_queue (unbounded) ──► _writer thread ──► subprocess stdin
    │     SIZE, POS, ACTIVE, TITLE, SHOW, HIDE, QUIT
    │
    └──► _frame_queue (maxsize=1) ──► _writer thread ──► subprocess stdin
          FRAME (latest-wins, stale frames dropped)
```

**Design rationale:**
- The GTK main thread must **never block** on pipe I/O — all writes go through a dedicated background writer thread.
- Control messages are always delivered in order and never dropped.
- Frame payloads are large (base64 pixel data). Only the newest frame matters, so the queue is size 1 — if a new frame arrives before the old one was sent, the old one is silently replaced.

#### Reader Thread
Reads subprocess stdout, parses text events, and dispatches them into the GTK main loop at `GLib.PRIORITY_HIGH` (not default idle priority). This is critical — with multiple clients, capture timers at default priority can starve idle callbacks, causing click and hover to stop working entirely.

---

### 5. Xlib Helpers

**Lines 516–690**

Direct X11 C library calls via `ctypes`. These exist because GTK/Wnck alone can't reliably focus XWayland windows, especially under KDE's focus-stealing prevention.

#### `_net_activate_window(xid, timestamp)`

The most complex function in the file. It hand-assembles X11 C structs in Python to send a `_NET_ACTIVE_WINDOW` `ClientMessage` — the same protocol that `wmctrl` and `xdotool` use internally.

**Activation sequence:**
1. `XMapWindow` — Ensure the window is mapped (EVE's loading screen can temporarily unmap it).
2. `_NET_ACTIVE_WINDOW` ClientMessage — Ask the window manager to activate it. Uses `source=1` (direct user action) so KDE honors it.
3. `XRaiseWindow` — Raise it in the X11 stacking order.
4. `XSetInputFocus` — Directly assign keyboard focus, bypassing WM focus-stealing prevention.
5. `WM_TAKE_FOCUS` — Send ICCCM focus protocol message for Wine windows that need it.

#### `_get_child_xids(parent_xid)`

Uses `XQueryTree` to enumerate child windows. Wine's "Fixed Window" mode wraps the game rendering in a child window — the parent is just a container that returns no pixels when captured.

---

### 6. EVE Window Detection

**Lines 692–760**

A set of heuristic functions that filter desktop windows to find real EVE game clients.

#### Detection Strategy

```
Window opened on desktop
    │
    ├── Is it our own window? ──► skip
    ├── Is it the EVE Launcher? ──► skip
    ├── Title is "Untitled window" or "Wine Desktop"? ──► skip
    ├── Title matches "EVE - <CharacterName>"? ──► ✅ accept
    ├── Title is generic "EVE" or "EVE Online"? ──► skip (wait for title to resolve)
    └── Process is exefile.exe / eve.exe / steam_app_8500? ──► ✅ accept
```

**Why not just match "EVE" in the title?** Because Wine creates many transient windows during startup with generic titles. Tracking them causes duplicate thumbnails and multi-client hangs. The code waits for the title to resolve to the actual character name before adding a thumbnail.

The process-level check reads `/proc/<pid>/cmdline` — a Linux-specific mechanism for inspecting what executable a running process belongs to.

---

### 7. `Config` Class

**Lines 762–800**

Straightforward JSON config manager.

**Config location:** `~/.config/eve-o-preview-linux/config.json`

**Default settings:**
```json
{
  "thumbnail_width": 320,
  "thumbnail_height": 200,
  "opacity": 0.95,
  "always_on_top": true,
  "hide_active_client": false,
  "zoom_on_hover": true,
  "zoom_factor": 1.25,
  "show_overlay": true,
  "refresh_fps": 10,
  "active_border_color": "#00FF00",
  "thumbnail_positions": {}
}
```

Uses a merge strategy on load: saved values override defaults, but new default keys (added in updates) are always available. This prevents crashes when upgrading to a version that adds new settings.

---

### 8. `ThumbnailWindow` Class

**Lines 802–1448**

The heart of the application. One instance per EVE client.

#### Initialization (lines 802–910)

Creates a borderless, skip-taskbar, utility-type GTK window. Key setup:

- **Visual:** Enables RGBA visual for transparency support.
- **Overlay:** Uses `Gtk.Overlay` to layer the character name label on top of the screenshot image.
- **Border:** A `Gtk.Frame` with CSS styling that changes color when the thumbnail is active.
- **Mode detection:** If layer-shell is available, the GTK window itself is never shown — it serves only as a controller object while display is delegated to `_LayerShellDisplay`.

#### Live Capture Pipeline (lines 1206–1327)

```
GLib.timeout (10-30 FPS)
    │
    ▼
tick()
    ├── Window minimized? ──► show icon fallback
    ├── Window not viewable? ──► show icon fallback
    └── Capture via Gdk.pixbuf_get_from_window()
         ├── Got pixels? ──► scale to target size ──► display
         └── No pixels? ──► try child window (Wine Fixed Window)
                             └── Child found? ──► rebind and retry next tick
```

**Priority management:** Capture timers run at `GLib.PRIORITY_LOW` (300) so they yield to user input events (priority 0) and IPC callbacks (priority -100). Without this, two capture timers would consume the entire main loop and starve click/hover handling.

**Error handling:** Uses `Gdk.error_trap_push/pop` around `pixbuf_get_from_window` to catch X11 `BadDrawable` errors when windows disappear mid-capture, rather than crashing.

#### Click Handling (lines 917–1031, Layer-Shell Mode)

The click handler is a **multi-fallback activation cascade**:

```
1. wmctrl -ia 0x<xid>
   └── Failed?
2. xdotool windowactivate --sync <xid>
   └── Failed?
3. Search for sibling window with same PID
   └── wmctrl on sibling
   └── Failed?
4. Raw Xlib _NET_ACTIVE_WINDOW + XSetInputFocus
   └── Failed?
5. Wnck activate()
```

Each layer handles edge cases the previous one can't. For example, `wmctrl` fails on minimized windows, `xdotool` sometimes can't find XWayland windows, and KDE's focus-stealing prevention blocks all of the above in certain scenarios.

#### Zoom on Hover (lines 1039–1070, 1417–1430)

When the mouse enters a thumbnail, it enlarges by the configured zoom factor. A debounce timer (80ms) on the leave event prevents flickering when the compositor sends rapid leave/enter cycles during drag operations.

#### Position Persistence (lines 904–909, 1172–1190)

Positions are saved to config on drag-end, with a 400ms debounce to avoid blocking the main loop with JSON writes during continuous drag motion.

---

### 9. `EVEOPreview` Class

**Lines 1450–1825**

The main management window.

#### Window Monitoring (lines 1544–1691)

Three Wnck signal handlers drive the window lifecycle:

| Signal | Handler | Action |
|--------|---------|--------|
| `window-opened` | `_on_window_opened` | Check if EVE client. If not yet, install `name-changed` watcher on windows from EVE processes. |
| `window-closed` | `_on_window_closed` | Remove thumbnail, clean up watcher. |
| `active-window-changed` | `_on_active_changed` | Update border highlights across all thumbnails. |

**Pending watches:** When Wine opens a new window, its title is often generic ("EVE", "Untitled window"). The code installs a `name-changed` signal handler *only* on windows whose PID matches a real EVE client process. When the title resolves to "EVE - CharacterName", the watcher fires, creates the thumbnail, and disconnects itself.

#### Active Window Tracking (lines 1693–1781)

Handles several KDE Plasma 6 edge cases:

- **Null active window:** On Wayland, the active window becomes `None` when native surfaces (alt-tab switcher, app launcher) take focus. The code retries up to 6 times over ~900ms.
- **Missing signals:** KDE sometimes doesn't fire `active-window-changed` after alt-tab between XWayland windows. A 1-second periodic poll acts as a safety net.
- **Efficient polling:** Avoids calling `Wnck.force_update()` (which is expensive — it processes all pending X events synchronously) unless the cheaper path fails.

---

### 10. `SettingsDialog` Class

**Lines 1827–2238**

A `Gtk.Dialog` with two notebook tabs:

**Display Tab:**
- Thumbnail width/height (spin buttons, 100–800px / 80–600px)
- Opacity slider (0.2–1.0)
- Active border color: hex entry + color picker button + preset swatches (green, cyan, magenta, yellow, red, blue) + live preview

**Behavior Tab:**
- Always on top (checkbox)
- Hide active client thumbnail (checkbox)
- Show character name overlay (checkbox)
- Zoom on hover (checkbox) + zoom factor slider (1.1×–2.0×)
- Refresh rate (radio buttons: 10 / 15 / 25 / 30 FPS)

---

### 11. `main()` Entry Point

**Lines 2240–2287**

```python
def main():
    screen = Wnck.Screen.get_default()
    app = EVEOPreview()
    app.connect("delete-event", lambda w, e: w.iconify() or True)
    app.show_all()
    Gtk.main()
```

Notable behavior:

- **Close = minimize:** Closing the management window minimizes it to the taskbar instead of quitting. Thumbnails keep running. Use `Ctrl+C` in the terminal to fully exit.
- **Keep-above timer:** A 4-second periodic timer re-asserts `set_keep_above(True)` on the management window. This works around KDE Plasma stripping the above-flag when fullscreen XWayland EVE windows take focus.
- **Map handler:** Re-asserts keep-above whenever the window is un-minimized from the taskbar.

---

## Key Design Decisions

### Why ctypes instead of a proper X11 binding?

Python X11 bindings (`python-xlib`) would add a dependency and don't always interoperate cleanly with GDK's own X11 connection. Using `ctypes` to call `libX11.so` directly keeps the dependency list minimal (just system GTK + Wnck packages) and avoids connection conflicts.

### Why a single file?

The application is distributed as a standalone script — users just run `python3 eve_o_preview_linux.py`. No packaging, no `setup.py`, no virtual environment needed. The embedded helper script (`_LAYER_SHELL_HELPER`) avoids needing a second file to ship alongside it.

### Why not Wayland-native capture (PipeWire)?

EVE runs through Wine/Proton → XWayland. The XWayland windows are fully accessible via `GdkX11`, so X11 capture works even on Wayland sessions. PipeWire-based capture would be needed for native Wayland windows, which EVE doesn't use. This is noted as a potential "Phase 2" feature.

### Why the activation cascade?

Focusing an XWayland window from a different process is surprisingly difficult on Linux. Each window manager has its own focus-stealing prevention policy, Wine windows may or may not support ICCCM/EWMH protocols correctly, and windows can be in various states (minimized, unmapped, managed vs. unmanaged by the compositor). The cascade (`wmctrl` → `xdotool` → PID sibling search → raw Xlib → Wnck) ensures reliable activation across KDE, GNOME, and other compositors.

---

## Dependencies

### Required (system packages)

| Package | Fedora | Ubuntu |
|---------|--------|--------|
| GTK 3 | `gtk3` | `gir1.2-gtk-3.0` |
| Wnck 3 | `libwnck3` | `gir1.2-wnck-3.0` |
| Python GObject | `python3-gobject` | `python3-gi` |

### Optional (recommended for Wayland)

| Package | Fedora | Ubuntu |
|---------|--------|--------|
| gtk-layer-shell | `gtk-layer-shell` | `gir1.2-gtk-layer-shell-0` |
| wmctrl | `wmctrl` | `wmctrl` |
| xdotool | `xdotool` | `xdotool` |

### Python Standard Library (no pip installs)

`os`, `json`, `sys`, `ctypes`, `subprocess`, `threading`, `queue`, `base64`, `warnings`, `pathlib`, `time`

---

## Debug Flags

| Flag | How to Enable | What It Does |
|------|--------------|--------------|
| `--debug` | Command line argument | Prints per-frame capture diagnostics (XID, size, pixbuf status, child window enumeration) |
| `EVE_PREVIEW_IPC_DEBUG=1` | Environment variable | Logs all IPC messages between main process and layer-shell subprocesses |

```bash
# Example: run with both debug modes
EVE_PREVIEW_IPC_DEBUG=1 python3 eve_o_preview_linux.py --debug
```

---

## Contributing Notes

- The codebase is intentionally a single file for ease of distribution. If it grows significantly, consider splitting into a package.
- All X11 calls use `ctypes` — match the existing patterns for struct definitions and error handling.
- Test on both X11 and Wayland sessions, and with both single and multiple EVE clients.
- KDE Plasma 6 has the most edge cases around focus and stacking — test there first.
- Wine "Fixed Window" vs. "Windowed" mode affects child window structure — test both.
