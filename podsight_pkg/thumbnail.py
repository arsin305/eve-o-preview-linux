"""ThumbnailWindow: one live preview per EVE client, including the capture loop."""
import os, time, ctypes
from . import platform  # noqa: F401 — env/gi setup must run first
from .platform import (DEBUG_CAPTURE, _net_activate_window, _get_child_xids,
                       _get_xlib, _xlib_display,
                       _LAYER_SHELL_AVAILABLE, _WAYLAND_SESSION)
from .layer_shell import _LayerShellDisplay
from . import capture
from .stats import STATS, _Stats
from gi.repository import Gtk, Gdk, GdkPixbuf, Wnck, GLib, GdkX11

class ThumbnailWindow(Gtk.Window):
    def toggle_pin(self):
        """Middle-click handler: lock/unlock this thumbnail against drags."""
        self.pinned = not self.pinned
        name = self.wnck_window.get_name() or ""
        pins = self.config.settings.setdefault("thumbnail_pins", {})
        if self.pinned:
            pins[name] = True
        else:
            pins.pop(name, None)
        self.config.save()
        if self._use_ls and self._ls:
            self._ls.set_pinned(self.pinned)
        else:
            self.pin_label.show() if self.pinned else self.pin_label.hide()
        print(f"[pin] '{name}' {'pinned' if self.pinned else 'unpinned'}", flush=True)

    def apply_snap_settings(self):
        """Push the current snapping config to the layer-shell helper."""
        if self._use_ls and self._ls:
            grid = (self.config.settings.get("grid_size", 32)
                    if self.config.settings.get("snap_to_grid", False) else 0)
            edge = self.config.settings.get("edge_snap", False)
            self._ls.set_snap(grid, edge)
            if edge:
                provider = getattr(self.config, "snap_rects_provider", None)
                if provider:
                    self._ls.set_rects(provider(exclude=self))

    def push_sibling_rects(self, rects):
        """Refresh the helper's magnet targets (edge snapping)."""
        if self._use_ls and self._ls:
            self._ls.set_rects(rects)

    def _snap_position(self, x, y):
        """Grid + edge snapping for a proposed position. Returns (x, y)."""
        cfg = self.config.settings
        if cfg.get("snap_to_grid", False):
            g = max(1, int(cfg.get("grid_size", 32)))
            x = int(round(x / g)) * g
            y = int(round(y / g)) * g
        if cfg.get("edge_snap", False):
            provider = getattr(self.config, "snap_rects_provider", None)
            if provider:
                dist = 12
                w, h = self._target_w, self._target_h
                for (ox, oy, ow, oh) in provider(exclude=self):
                    # horizontal flush: my left to their right / my right to their left
                    if abs(x - (ox + ow)) <= dist and not (y + h < oy or y > oy + oh):
                        x = ox + ow
                    elif abs((x + w) - ox) <= dist and not (y + h < oy or y > oy + oh):
                        x = ox - w
                    # vertical flush
                    if abs(y - (oy + oh)) <= dist and not (x + w < ox or x > ox + ow):
                        y = oy + oh
                    elif abs((y + h) - oy) <= dist and not (x + w < ox or x > ox + ow):
                        y = oy - h
                    # align edges when side-by-side
                    if abs(y - oy) <= dist and (x == ox + ow or x + w == ox):
                        y = oy
                    if abs(x - ox) <= dist and (y == oy + oh or y + h == oy):
                        x = ox
        return max(0, int(x)), max(0, int(y))

    def _on_ls_dragend(self, x, y):
        """Drag finished in the layer-shell helper: apply edge/grid snap and
        push the corrected position back down."""
        nx, ny = self._snap_position(x, y)
        if (nx, ny) != (x, y):
            self._ls.set_pos(nx, ny)
        self._on_ls_pos(nx, ny)   # updates _ls_x/_ls_y and debounce-saves

    def move_to(self, x, y):
        """Programmatic placement (layout apply). Works on both paths."""
        if self._use_ls and self._ls:
            self._ls.set_pos(int(x), int(y))
            self._on_ls_pos(int(x), int(y))
        else:
            self.move(int(x), int(y))

    def get_screen_pos(self):
        """Current on-screen (x, y) — layer-shell tracks its own coords."""
        if self._use_ls:
            return (self._ls_x, self._ls_y)
        try:
            return self.get_position()
        except Exception:
            return (0, 0)

    def set_hotkey_label(self, text):
        """Show/refresh the bottom hotkey bar ('' hides). Called by the app
        whenever client ordering changes; honors the show_hotkey_overlay
        setting on both display paths."""
        if not self.config.settings.get("show_hotkey_overlay", True):
            text = ""
        self._hotkey_text = text
        if self._use_ls and self._ls:
            self._ls.set_hotkey(text)
        else:
            if text:
                self.hotkey_label.set_text(text)
                self.hotkey_label.show()
            else:
                self.hotkey_label.hide()

    def activate_client(self):
        """Bring this client to the front — same cascade as clicking the
        thumbnail. Used by hotkey switching (focus change only, never input)."""
        action = getattr(self, "_click_action", None)
        if action is not None:          # layer-shell path: full cascade
            action()
        else:                           # X11 path: Wnck activation
            try:
                if self.wnck_window.is_minimized():
                    self.wnck_window.unminimize(0)
                self.on_activate_callback(self.wnck_window)
            except Exception as e:
                print(f"[hotkey] activate error: {e}")

    def __init__(self, wnck_window, config, on_activate_callback):
        super().__init__()
        self.wnck_window = wnck_window
        self.config = config
        self.on_activate_callback = on_activate_callback

        self.original_size = (config.settings["thumbnail_width"],
                              config.settings["thumbnail_height"])
        self.is_hovering = False
        self.is_active = False
        self.live_window = None
        self.update_id = None
        self._root_xid = None
        self._capture_xid = None
        self._target_w, self._target_h = self.original_size

        # Layer-shell mode: display via a Wayland OVERLAY subprocess.
        # This guarantees thumbnails appear above every fullscreen/fixed-window
        # surface because the Wayland compositor renders OVERLAY above all managed
        # windows unconditionally, regardless of XWayland stacking tricks.
        self._use_ls = _LAYER_SHELL_AVAILABLE and _WAYLAND_SESSION
        self._ls = None       # _LayerShellDisplay, created after GTK init
        self._ls_x = self._ls_y = 0

        # GTK window setup (still needed for the capture machinery and as a
        # controller object; the window itself is NOT shown in layer-shell mode).
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(not self._use_ls)
        self.set_decorated(False)
        self._always_on_top = self.config.settings.get("always_on_top", True)
        if self._always_on_top and not self._use_ls:
            self.set_keep_above(True)
        self.connect("realize", self._on_realize)
        self.connect("map", self._on_map)
        try:
            self.set_opacity(self.config.settings.get("opacity", 0.95))
        except Exception:
            pass
        self.set_default_size(*self.original_size)

        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)

        # Create a frame for the border effect
        self.border_frame = Gtk.Frame()
        self.border_frame.set_shadow_type(Gtk.ShadowType.NONE)
        self.add(self.border_frame)
        
        self.overlay = Gtk.Overlay()
        self.border_frame.add(self.overlay)
        self.image = Gtk.Image()
        self.overlay.add(self.image)
        
        # Apply initial border style
        self._update_border_style()

        # Always create the label for character name
        self.label = Gtk.Label()
        title = self.wnck_window.get_name() or "EVE"
        # Extract character name from window title (format: "EVE - Character Name")
        if " - " in title:
            char_name = title.split(" - ", 1)[1]
            # Remove any trailing parts like " [Omega]" or similar
            if "[" in char_name:
                char_name = char_name.split("[")[0].strip()
            title = char_name
        self.label.set_markup(f"<b>{title}</b>")
        self.label.set_halign(Gtk.Align.CENTER)
        self.label.set_valign(Gtk.Align.START)
        css = Gtk.CssProvider()
        css.load_from_data(b"label { background: rgba(0,0,0,0.7); color: white; padding: 6px 10px; margin: 6px; border-radius: 3px; font-size: 11px; }")
        self.label.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        
        if self.config.settings.get("show_overlay", True):
            self.overlay.add_overlay(self.label)
        else:
            self.label.set_no_show_all(True)
            self.label.hide()

        # Bottom hotkey bar (X11 path; the layer-shell path draws its own).
        self.hotkey_label = Gtk.Label()
        self.hotkey_label.set_halign(Gtk.Align.FILL)
        self.hotkey_label.set_valign(Gtk.Align.END)
        hk_css = Gtk.CssProvider()
        hk_css.load_from_data(
            b"label { background: rgba(0,0,0,0.78); color: #7dd3fc;"
            b"  padding: 3px 0; font-size: 11px; font-weight: bold; }")
        self.hotkey_label.get_style_context().add_provider(
            hk_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.hotkey_label.set_no_show_all(True)
        self.overlay.add_overlay(self.hotkey_label)
        self._hotkey_text = ""

        # Pin state: pinned thumbnails ignore drags. Persisted by window name,
        # same key scheme the saved positions use.
        pins = self.config.settings.get("thumbnail_pins", {})
        self.pinned = bool(pins.get(self.wnck_window.get_name() or ""))
        self.pin_label = Gtk.Label(label="\U0001F4CC")
        self.pin_label.set_halign(Gtk.Align.END)
        self.pin_label.set_valign(Gtk.Align.START)
        self.pin_label.set_margin_top(4)
        self.pin_label.set_margin_end(6)
        self.pin_label.set_no_show_all(True)
        self.overlay.add_overlay(self.pin_label)
        if self.pinned:
            self.pin_label.show()


        # click/drag logic
        self._press_pos = None
        self._dragging = False
        self._drag_threshold = 6

        self.add_events(
            Gdk.EventMask.ENTER_NOTIFY_MASK |
            Gdk.EventMask.LEAVE_NOTIFY_MASK |
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK
        )
        self.connect("button-press-event", self._on_button_press)
        self.connect("button-release-event", self._on_button_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("enter-notify-event", self._on_mouse_enter)
        self.connect("leave-notify-event", self._on_mouse_leave)
        self.connect("destroy", self._on_destroy)

        # restore position
        name = self.wnck_window.get_name()
        pos = self.config.settings.get("thumbnail_positions", {}).get(name)
        if pos:
            self._ls_x, self._ls_y = int(pos[0]), int(pos[1])

        # Create the layer-shell subprocess now that GTK widget tree is built.
        # Position/size are sent after subprocess starts; the actual show() call
        # comes later from _add_thumb() via show_all().
        if self._use_ls:
            import time as _time_mod
            _last_click_time = [0.0]

            def _click():
                # Secondary debounce in the main process: in case the subprocess
                # debounce is bypassed (e.g. two subprocesses both firing CLICK),
                # reject calls arriving within 500 ms of the last activation.
                now = _time_mod.monotonic()
                if now - _last_click_time[0] < 0.5:
                    print(f"[click] debounced", flush=True)
                    return
                _last_click_time[0] = now
                # Use the XID actually being screenshotted (_capture_xid).  Wine/
                # Proton often opens an "Untitled window" parent that KWin doesn't
                # track; the real EVE content is in a child window found by
                # bind_live.  Activating the capture XID targets the visible surface.
                xid = getattr(self, "_capture_xid", None) or self.wnck_window.get_xid()
                name = self.wnck_window.get_name()
                print(f"[click] activating '{name}' xid=0x{xid:x} (capture={xid!=self.wnck_window.get_xid()})", flush=True)

                # Get a real X11 server timestamp — use for all activation methods.
                try:
                    _ts = GdkX11.x11_get_server_time(Gdk.get_default_root_window())
                except Exception:
                    _ts = 0

                # Unminimize first — wmctrl/xdotool exit=1 on minimized windows.
                try:
                    if self.wnck_window.is_minimized():
                        print(f"[click] unminimizing first", flush=True)
                        self.wnck_window.unminimize(_ts)
                except Exception as e:
                    print(f"[click] unminimize error: {e}", flush=True)

                # Map the X11 window if it's currently unmapped (EVE's loading
                # screen temporarily withdraws the XWayland surface). We must do
                # this BEFORE wmctrl/xdotool so they get a mapped window.
                xlib = _get_xlib()
                dpy  = _xlib_display()
                if xlib and dpy:
                    try:
                        xlib.XMapWindow.restype  = ctypes.c_int
                        xlib.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
                        xlib.XMapWindow(ctypes.c_void_p(dpy), ctypes.c_ulong(xid))
                        xlib.XFlush.restype = ctypes.c_int
                        xlib.XFlush(ctypes.c_void_p(dpy))
                        print(f"[click] XMapWindow called", flush=True)
                    except Exception as e:
                        print(f"[click] XMapWindow error: {e}", flush=True)

                import subprocess as _sp
                # 1) wmctrl — plain activation request to KWin
                _wmctrl_ok = False
                try:
                    r = _sp.run(["wmctrl", "-ia", hex(xid)],
                                capture_output=True, timeout=2)
                    print(f"[click] wmctrl exit={r.returncode}", flush=True)
                    _wmctrl_ok = (r.returncode == 0)
                except FileNotFoundError:
                    print("[click] wmctrl not found", flush=True)
                except Exception as e:
                    print(f"[click] wmctrl error: {e}", flush=True)

                # 2) xdotool — often more reliable for XWayland windows
                #    (sudo dnf install xdotool / sudo apt install xdotool)
                _xdotool_ok = False
                try:
                    r = _sp.run(
                        ["xdotool", "windowactivate", "--sync", str(xid)],
                        capture_output=True, timeout=2)
                    print(f"[click] xdotool exit={r.returncode}", flush=True)
                    _xdotool_ok = (r.returncode == 0)
                except FileNotFoundError:
                    print("[click] xdotool not found (dnf install xdotool)", flush=True)
                except Exception as e:
                    print(f"[click] xdotool error: {e}", flush=True)

                if _wmctrl_ok or _xdotool_ok:
                    return

                # 2b) PID fallback: search for a sibling window from the same
                # process that KWin DOES manage (_NET_CLIENT_LIST).  Wine/Proton
                # at character-selection creates an "Untitled window" that KWin
                # doesn't track; another window from the same PID is the one
                # KWin can activate.
                if not _wmctrl_ok:
                    try:
                        target_pid = self.wnck_window.get_pid()
                        if target_pid:
                            for sibling in Wnck.Screen.get_default().get_windows():
                                s_xid = sibling.get_xid()
                                if s_xid == xid or sibling.get_pid() != target_pid:
                                    continue
                                s_name = sibling.get_name()
                                print(f"[click] trying PID sibling 0x{s_xid:x} '{s_name}'", flush=True)
                                r2 = _sp.run(["wmctrl", "-ia", hex(s_xid)],
                                             capture_output=True, timeout=2)
                                print(f"[click] sibling wmctrl exit={r2.returncode}", flush=True)
                                if r2.returncode == 0:
                                    _wmctrl_ok = True
                                    break
                    except Exception as e:
                        print(f"[click] sibling search: {e}", flush=True)

                if _wmctrl_ok:
                    return

                # 3) Direct Xlib _NET_ACTIVE_WINDOW + XSetInputFocus
                ok = _net_activate_window(xid, _ts)
                print(f"[click] _net_activate_window={ok}", flush=True)
                # 4) Wnck fallback
                try:
                    print(f"[click] Wnck activate ts={_ts}", flush=True)
                    if self.wnck_window.is_minimized():
                        self.wnck_window.unminimize(_ts)
                    self.wnck_window.activate(_ts)
                except Exception as e:
                    print(f"[click] Wnck error: {e}", flush=True)

            # Expose the full activation cascade for hotkey-driven switching.
            self._click_action = _click

            def _ctrl_click():
                try:
                    self.wnck_window.minimize()
                except Exception:
                    pass

            # Zoom debounce state — shared across _enter/_leave closures.
            _leave_timer = [None]

            def _enter():
                if not self.config.settings.get("zoom_on_hover", True):
                    return
                if _leave_timer[0]:
                    GLib.source_remove(_leave_timer[0])
                    _leave_timer[0] = None
                if not self.is_hovering:
                    self.is_hovering = True
                    z = self.config.settings.get("zoom_factor", 1.25)
                    zw = int(self.original_size[0] * z)
                    zh = int(self.original_size[1] * z)
                    # Update capture size so frames render at zoomed resolution
                    self._target_w, self._target_h = zw, zh
                    if self._ls:
                        self._ls.set_size(zw, zh)

            def _leave():
                if not self.config.settings.get("zoom_on_hover", True):
                    return
                if _leave_timer[0]:
                    GLib.source_remove(_leave_timer[0])
                def _do_leave():
                    _leave_timer[0] = None
                    if self.is_hovering:
                        self.is_hovering = False
                        self._target_w, self._target_h = self.original_size
                        if self._ls:
                            self._ls.set_size(*self.original_size)
                _leave_timer[0] = GLib.timeout_add(80, _do_leave)

            self._ls = _LayerShellDisplay(
                self._ls_x, self._ls_y,
                self._target_w, self._target_h,
                _click, _ctrl_click, self._on_ls_pos,
                _enter, _leave,
                mclick_cb=self.toggle_pin, dragend_cb=self._on_ls_dragend,
            )
            self._ls_save_timer = None

            def _send_title():
                if not self._ls or not self.config.settings.get("show_overlay", True):
                    return
                raw = self.wnck_window.get_name() or ""
                # EVE window title format: "EVE - CharacterName" or just "EVE"
                # Extract everything after the first " - "
                if " - " in raw:
                    title = raw.split(" - ", 1)[1].split("[")[0].strip()
                else:
                    title = raw
                self._ls.send_title(title)

            _send_title()
            if getattr(self, "_hotkey_text", ""):
                self._ls.set_hotkey(self._hotkey_text)
            if self.pinned:
                self._ls.set_pinned(True)
            self.apply_snap_settings()
            # Update label whenever EVE finishes loading the character.
            self.wnck_window.connect("name-changed", lambda *_: _send_title())
            # Fallback poll: EVE may update its title AFTER name-changed fires
            # (or not fire it at all on some Wine versions). Retry for 60s.
            _poll_count = [0]
            def _poll_title():
                _poll_count[0] += 1
                _send_title()
                return _poll_count[0] < 20  # stop after 20 * 3s = 60s
            GLib.timeout_add(3000, _poll_title)

    # ------------------------------------------------------------------
    # Layer-shell delegation — override GTK.Window methods so callers
    # in EVEOPreview don't need to know which display mode is active.
    # ------------------------------------------------------------------

    def show(self):
        if self._use_ls:
            if self._ls:
                self._ls.show()
        else:
            super().show()

    def show_all(self):
        if self._use_ls:
            if self._ls:
                self._ls.show()
        else:
            super().show_all()

    def hide(self):
        if self._use_ls:
            if self._ls:
                self._ls.hide()
        else:
            super().hide()

    def move(self, x, y):
        self._ls_x, self._ls_y = x, y
        if self._use_ls:
            if self._ls:
                self._ls.set_pos(x, y)
        else:
            super().move(x, y)

    def resize(self, w, h):
        self._target_w, self._target_h = w, h
        if self._use_ls:
            if self._ls:
                self._ls.set_size(w, h)
        else:
            super().resize(w, h)

    def set_keep_above(self, above):
        if not self._use_ls:
            super().set_keep_above(above)
        # Layer-shell OVERLAY is unconditionally above everything; no-op here.

    def destroy(self):
        if self._use_ls:
            # Save position before destroying subprocess
            try:
                name = self.wnck_window.get_name()
                cfg = self.config.settings.setdefault("thumbnail_positions", {})
                cfg[name] = [self._ls_x, self._ls_y]
                self.config.save()
            except Exception:
                pass
            if self._ls:
                self._ls.destroy()
            if self.update_id:
                try:
                    GLib.source_remove(self.update_id)
                except Exception:
                    pass
                self.update_id = None
        else:
            super().destroy()

    def _on_ls_pos(self, x, y):
        """Called when the layer-shell subprocess reports a drag-move."""
        self._ls_x, self._ls_y = x, y
        # Debounce config saves: writing JSON on every motion event blocks the
        # GLib main loop and causes frame drops / visual stutter during drag.
        if hasattr(self, '_ls_save_timer') and self._ls_save_timer:
            GLib.source_remove(self._ls_save_timer)
        self._ls_save_timer = GLib.timeout_add(400, self._save_ls_pos)

    def _save_ls_pos(self):
        self._ls_save_timer = None
        try:
            name = self.wnck_window.get_name()
            cfg = self.config.settings.setdefault("thumbnail_positions", {})
            cfg[name] = [self._ls_x, self._ls_y]
            self.config.save()
        except Exception:
            pass
        return False  # don't repeat

    # ------------------------------------------------------------------

    def _on_realize(self, widget):
        if self._always_on_top and not self._use_ls:
            self.set_keep_above(True)

    def _on_map(self, widget):
        """Re-assert after map — KWin processes _NET_WM_STATE changes post-map."""
        if self._always_on_top and not self._use_ls:
            super().set_keep_above(True)
            gdk_win = self.get_window()
            if gdk_win:
                gdk_win.raise_()

    def bind_live(self, xid, target_w, target_h):
        try:
            display = GdkX11.X11Display.get_default()
            if not display:
                raise RuntimeError("No X11 display for GdkX11")
            self._root_xid = xid
            self._capture_xid = xid          # may be replaced by child in tick
            self.live_window = GdkX11.X11Window.foreign_new_for_display(display, xid)
            self._target_w, self._target_h = int(target_w), int(target_h)
            self._start_live_timer()
        except Exception as e:
            print("Live capture bind failed:", e)

    def _start_live_timer(self):
        if self.update_id:
            try:
                GLib.source_remove(self.update_id)
            except Exception:
                pass
            self.update_id = None
        
        # Convert FPS to milliseconds
        fps = int(self.config.settings.get("refresh_fps", 10))
        period = int(1000 / fps)  # Convert FPS to ms

        # Rate-limit _try_bind_child: at most once per second, not every tick.
        _child_bind_counter = [0]
        _child_bind_interval = max(fps, 10)  # try once per second

        # --stats: per-thumbnail counter record (None when stats are off).
        stats_rec = STATS.register(self) if STATS else None

        def _try_bind_child():
            """Wine Fixed Window renders into a child XID — find and bind it."""
            try:
                display = GdkX11.X11Display.get_default()
                children = _get_child_xids(self._root_xid)
                if DEBUG_CAPTURE:
                    print(f"[capture] XID=0x{self._root_xid:x} children={[hex(c) for c in children]}")
                for child_xid in reversed(children):   # last = topmost
                    cw = GdkX11.X11Window.foreign_new_for_display(display, child_xid)
                    if cw:
                        cw_w, cw_h = cw.get_width(), cw.get_height()
                        if DEBUG_CAPTURE:
                            print(f"[capture]   child 0x{child_xid:x} size={cw_w}x{cw_h}")
                        if cw_w > 0 and cw_h > 0:
                            self.live_window   = cw
                            self._capture_xid  = child_xid
                            return True
            except Exception as e:
                if DEBUG_CAPTURE:
                    print(f"[capture] _try_bind_child error: {e}")
            return False

        _raise_counter = [0]

        # NOTE: tick takes *_args because GLib.Source.set_callback passes
        # user_data as an extra positional argument to the callback.
        def tick(*_args):
            if not self.live_window:
                if STATS:
                    STATS.unregister(self)
                return False
            # Re-assert window stacking every ~2 s (every 20 ticks at 10 fps).
            _raise_counter[0] += 1
            if _raise_counter[0] >= 20:
                _raise_counter[0] = 0
                if self._always_on_top:
                    self.set_keep_above(True)
                    gdk_win = self.get_window()
                    if gdk_win:
                        gdk_win.raise_()
            try:
                if self.wnck_window.is_minimized():
                    self._set_icon_fallback()
                    return True
            except Exception:
                pass
            try:
                w = self.live_window.get_width()
                h = self.live_window.get_height()
                if DEBUG_CAPTURE:
                    print(f"[capture] XID=0x{self._capture_xid:x} size={w}x{h}", end="")
                if w <= 0 or h <= 0:
                    if DEBUG_CAPTURE: print(" → invalid size, fallback")
                    self._set_icon_fallback()
                    return True
                # Skip non-viewable windows to avoid Gdk-CRITICAL spam.
                if not self.live_window.is_viewable():
                    self._set_icon_fallback()
                    return True
                _t0 = time.perf_counter() if STATS else 0.0
                # Fast path: server-side scale via XComposite+XRender.
                # Returns an already-thumbnail-sized pixbuf, or None on any
                # failure — in which case the legacy path below still runs,
                # preserving the Wine child-XID rebinding behavior.
                pb = capture.grab_scaled(self._capture_xid, w, h,
                                         self._target_w, self._target_h)
                if pb is None:
                    Gdk.error_trap_push()
                    pb = Gdk.pixbuf_get_from_window(self.live_window, 0, 0, w, h)
                    if Gdk.error_trap_pop():
                        pb = None  # BadDrawable / window gone — ignore
                    if pb:
                        pb = pb.scale_simple(self._target_w, self._target_h, GdkPixbuf.InterpType.BILINEAR)
                if DEBUG_CAPTURE:
                    print(f" → pixbuf={'ok' if pb else 'None'}")
                if pb:
                    if self._use_ls and self._ls:
                        self._ls.send_frame(pb)
                    else:
                        self.image.set_from_pixbuf(pb)
                    if STATS:
                        _Stats.record(stats_rec, (time.perf_counter() - _t0) * 1000.0)
                else:
                    # Parent returned no pixels — try child windows (Wine Fixed Window)
                    # Rate-limited: once per second instead of every tick to avoid
                    # X11 round-trip storms with multiple clients.
                    _child_bind_counter[0] += 1
                    if self._capture_xid == self._root_xid and _child_bind_counter[0] >= _child_bind_interval:
                        _child_bind_counter[0] = 0
                        _try_bind_child()
                    self._set_icon_fallback()
            except Exception as e:
                if DEBUG_CAPTURE:
                    print(f"[capture] tick exception: {e}")
                self._set_icon_fallback()
            return True

        # CRITICAL: Use GLib.PRIORITY_LOW (300) for capture timers so they yield
        # to user input events (PRIORITY_DEFAULT=0) and IPC callbacks
        # (PRIORITY_HIGH=-100).  With two EVE clients, two capture timers at
        # PRIORITY_DEFAULT consume the entire main loop, starving idle_add
        # callbacks that deliver CLICK/ENTER/LEAVE from subprocesses.
        src = GLib.timeout_source_new(period)
        src.set_priority(GLib.PRIORITY_LOW)
        src.set_callback(tick)
        self.update_id = src.attach()

    def _set_icon_fallback(self):
        try:
            pixbuf = self.wnck_window.get_icon()
            if pixbuf:
                scaled = pixbuf.scale_simple(self._target_w, self._target_h, GdkPixbuf.InterpType.BILINEAR)
                self.image.set_from_pixbuf(scaled)
        except Exception:
            pass

    def _update_border_style(self):
        """Update the border color based on active state"""
        css = Gtk.CssProvider()
        if self.is_active:
            border_color = self.config.settings.get("active_border_color", "#00FF00")
            css_data = f"""
            frame {{
                border: 4px solid {border_color};
                border-radius: 4px;
            }}
            frame > border {{
                background-color: transparent;
            }}
            """.encode()
        else:
            css_data = b"""
            frame {
                border: 2px solid rgba(60, 60, 60, 0.5);
                border-radius: 2px;
            }
            frame > border {
                background-color: transparent;
            }
            """
        css.load_from_data(css_data)
        self.border_frame.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def set_active_state(self, is_active):
        """Set whether this thumbnail represents the active window"""
        if self.is_active != is_active:
            self.is_active = is_active
            if self._use_ls:
                if self._ls:
                    color = self.config.settings.get("active_border_color", "#00FF00")
                    self._ls.send_active(is_active, color)
            else:
                self._update_border_style()

    def _on_button_press(self, _w, event):
        if event.button == 1:
            if event.state & Gdk.ModifierType.CONTROL_MASK:
                try:
                    self.wnck_window.minimize()
                except Exception:
                    pass
                return True
            self._press_pos = (event.x_root, event.y_root, event.time)
            self._dragging = False
            return True
        if event.button == 3:
            if not self.pinned:
                self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            return True
        if event.button == 2:
            self.toggle_pin()
            return True
        return False

    def _on_motion(self, _w, event):
        if not self._press_pos:
            return False
        dx = abs(event.x_root - self._press_pos[0])
        dy = abs(event.y_root - self._press_pos[1])
        if self.pinned:
            return False
        if not self._dragging and (dx > self._drag_threshold or dy > self._drag_threshold):
            self._dragging = True
            self.begin_move_drag(1, int(self._press_pos[0]), int(self._press_pos[1]), int(self._press_pos[2]))
            return True
        return False

    def _on_button_release(self, _w, event):
        if event.button == 1:
            if not self._dragging:
                try:
                    if self.wnck_window.is_minimized():
                        self.wnck_window.unminimize(Gtk.get_current_event_time())
                    self.on_activate_callback(self.wnck_window)
                except Exception:
                    pass
            self._press_pos = None
            self._dragging = False
            return True
        return False

    def _on_mouse_enter(self, *_):
        if not self.config.settings.get("zoom_on_hover", True):
            return
        if not self.is_hovering:
            self.is_hovering = True
            z = self.config.settings.get("zoom_factor", 1.25)
            self.resize(int(self.original_size[0]*z), int(self.original_size[1]*z))

    def _on_mouse_leave(self, *_):
        if not self.config.settings.get("zoom_on_hover", True):
            return
        if self.is_hovering:
            self.is_hovering = False
            self.resize(*self.original_size)

    def _on_destroy(self, *_):
        if not self._use_ls:
            # Layer-shell mode saves position in destroy() and _on_ls_pos().
            try:
                x, y = self.get_position()
                name = self.wnck_window.get_name()
                cfg = self.config.settings.setdefault("thumbnail_positions", {})
                cfg[name] = [x, y]
                self.config.save()
            except Exception:
                pass
        if self.update_id:
            try:
                GLib.source_remove(self.update_id)
            except Exception:
                pass
            self.update_id = None
