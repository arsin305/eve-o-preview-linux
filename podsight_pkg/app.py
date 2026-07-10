"""EVE client discovery and the PodSight management window."""
import os, time
from . import platform  # noqa: F401 — env/gi setup must run first
from .platform import SELF_PID, SCRIPT_BASENAME, _WAYLAND_SESSION, _LAYER_SHELL_AVAILABLE
from .config import Config
from .thumbnail import ThumbnailWindow
from .settings_dialog import SettingsDialog
from gi.repository import Gtk, Gdk, Wnck, GLib


def _proc_cmdline_contains(pid: int, needles):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read().replace(b"\x00", b" ")
        low = raw.lower()
        return any(n.encode("utf-8").lower() in low for n in needles)
    except Exception:
        return False

def _get_window_name(wnck_window):
    try:
        return (wnck_window.get_name() or "").strip()
    except Exception:
        return ""

def _looks_like_launcher(name, pid=0):
    low = (name or "").lower()
    if any(k in low for k in ("launcher", "eve launcher")):
        return True
    # NOTE: "qml" was removed from this list. It is only 3 characters and
    # produced false positives on real EVE client cmdlines whose ssoToken
    # values, paths, or other arguments happened to contain that substring.
    # "qtwebengine" alone is a specific enough launcher signature.
    if pid and _proc_cmdline_contains(pid, ["evelauncher", "launcher.exe", "qtwebengine"]):
        return True
    return False

def _is_real_eve_client_process(pid):
    if not pid:
        return False
    return _proc_cmdline_contains(pid, ["exefile.exe", "eve.exe", "steam_app_8500", "c_program_files_ccp_eve"])

def is_eve_window_steamaware(wnck_window):
    try:
        if wnck_window.get_pid() == SELF_PID:
            return False
    except Exception:
        pass
    pid = 0
    try:
        pid = wnck_window.get_pid() or 0
    except Exception:
        pid = 0
    name = _get_window_name(wnck_window)
    low = name.lower()
    if SCRIPT_BASENAME.lower() in low or "eve-o preview" in low or "podsight" in low:
        return False
    if not name:
        return False
    # Stable/fully resolved EVE client title — accept BEFORE any heuristic
    # rejection. "EVE - <CharacterName>" is unambiguous; the launcher never
    # uses this title, so this short-circuit is safe and protects real
    # clients from false-positive cmdline matches in _looks_like_launcher.
    if low.startswith("eve - ") and "launcher" not in low:
        return True
    if _looks_like_launcher(name, pid):
        return False
    if low in ("untitled window", "wine desktop"):
        return False
    # Do NOT add generic EVE windows yet. They frequently represent transient
    # startup/helper surfaces and are the source of the multi-client hang.
    # Let the pending name-changed watcher add them only after the title
    # resolves to the actual character name.
    if low in ("eve", "eve online"):
        return False
    # Last-resort process-based accept only when title is already non-generic.
    if _is_real_eve_client_process(pid):
        return True
    return False

class EVEOPreview(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.config = Config()
        self.thumbnails = {}
        self.client_rows = {}        # xid → Gtk.ListBoxRow in the management window
        self._pending_watches = {}   # xid → handler_id for name-changed watchers
        self.screen = Wnck.Screen.get_default()
        self.screen.force_update()

        self.set_title("PodSight")
        self.set_default_size(500, 400)
        self.set_position(Gtk.WindowPosition.CENTER)

        # Apply modern styling
        self._apply_styles()

        # Header bar
        headerbar = Gtk.HeaderBar()
        headerbar.set_show_close_button(True)
        headerbar.set_title("PodSight")
        headerbar.set_subtitle("Linux Edition")
        self.set_titlebar(headerbar)

        # Settings button in header
        settings_btn = Gtk.Button()
        settings_icon = Gtk.Image.new_from_icon_name("preferences-system", Gtk.IconSize.BUTTON)
        settings_btn.set_image(settings_icon)
        settings_btn.set_tooltip_text("Settings")
        settings_btn.connect("clicked", self._show_settings)
        headerbar.pack_end(settings_btn)

        # Main container
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        # Info bar
        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        info_box.set_margin_start(12)
        info_box.set_margin_end(12)
        info_box.set_margin_top(10)
        info_box.set_margin_bottom(10)
        
        session = os.environ.get("XDG_SESSION_TYPE", "unknown")
        backend = os.environ.get("GDK_BACKEND", "unknown")
        
        session_label = Gtk.Label(label=f"Session: {session}")
        info_box.pack_start(session_label, False, False, 0)
        
        separator1 = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        info_box.pack_start(separator1, False, False, 0)
        
        backend_label = Gtk.Label(label=f"Backend: {backend}")
        info_box.pack_start(backend_label, False, False, 0)
        
        vbox.pack_start(info_box, False, False, 0)

        # Separator
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        vbox.pack_start(sep, False, False, 0)

        # Status bar
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status_box.set_margin_start(12)
        status_box.set_margin_end(12)
        status_box.set_margin_top(8)
        status_box.set_margin_bottom(8)
        
        self.status_icon = Gtk.Image.new_from_icon_name("emblem-system", Gtk.IconSize.MENU)
        status_box.pack_start(self.status_icon, False, False, 0)
        
        self.status_label = Gtk.Label(label="Scanning for EVE clients...")
        self.status_label.set_halign(Gtk.Align.START)
        status_box.pack_start(self.status_label, True, True, 0)
        
        vbox.pack_start(status_box, False, False, 0)

        # Client list with frame
        frame = Gtk.Frame()
        frame.set_margin_start(12)
        frame.set_margin_end(12)
        frame.set_margin_bottom(12)
        frame.set_shadow_type(Gtk.ShadowType.IN)
        
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(150)
        
        self.client_list = Gtk.ListBox()
        self.client_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.add(self.client_list)
        frame.add(scrolled)
        
        vbox.pack_start(frame, True, True, 0)

        self.screen.connect("window-opened", self._on_window_opened)
        self.screen.connect("window-closed", self._on_window_closed)
        self.screen.connect("active-window-changed", self._on_active_changed)

        # Periodic safety-net poll: KDE Plasma 6 sometimes doesn't fire
        # active-window-changed after alt-tab between XWayland windows.
        self._last_polled_active_xid = None
        GLib.timeout_add(1000, self._periodic_active_poll)

        self._scan_existing()
        GLib.timeout_add(2000, self._periodic_client_scan)

    def _apply_styles(self):
        css_provider = Gtk.CssProvider()
        css = b"""
        .client-row {
            padding: 8px;
        }
        .client-name {
            font-weight: bold;
        }
        """
        css_provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _scan_existing(self):
        for w in self.screen.get_windows():
            self._check_and_add(w)
        self._update_status()

        # Apply active-client state immediately after startup discovery.
        # Without this, "Hide active client thumbnail" may not take effect
        # until the first focus/window switch signal is received.
        self.screen.force_update()
        active = self.screen.get_active_window()
        if active:
            active_xid = active.get_xid()
            self._apply_active_borders(active_xid)
            self._last_polled_active_xid = active_xid

        # KDE/Wayland startup timing can briefly report no or stale active
        # XWayland window. Reuse the existing short retry path as a safety net.
        self._poll_retries = [0]
        GLib.timeout_add(150, self._poll_active_border)

    def _check_and_add(self, window):
        if is_eve_window_steamaware(window):
            xid = window.get_xid()
            if xid not in self.thumbnails:
                self._add_thumb(window)
            return True
        return False

    def _place_thumb(self, thumb):
        screen = Gdk.Screen.get_default()
        if screen is not None:
            try:
                mon_idx = screen.get_primary_monitor()
            except Exception:
                mon_idx = 0
            geo = screen.get_monitor_geometry(mon_idx)
            sx, sy, sw, sh = geo.x, geo.y, geo.width, geo.height
        else:
            sx, sy, sw, sh = 0, 0, 1920, 1080

        tw = self.config.settings["thumbnail_width"]
        th = self.config.settings["thumbnail_height"]
        margin = 12
        cols = max(1, (sw - margin) // (tw + margin))
        idx = len(self.thumbnails) - 1
        x = sx + margin + (idx % cols) * (tw + margin)
        y = sy + margin + (idx // cols) * (th + margin)
        thumb.move(x, y)

    def _add_thumb(self, window):
        xid = window.get_xid()
        thumb = ThumbnailWindow(window, self.config, self._activate_window)
        self.thumbnails[xid] = thumb
        thumb.bind_live(xid, self.config.settings["thumbnail_width"], self.config.settings["thumbnail_height"])

        name = window.get_name()
        pos = self.config.settings.get("thumbnail_positions", {}).get(name)
        if pos:
            thumb.move(int(pos[0]), int(pos[1]))
        else:
            self._place_thumb(thumb)
        # show_all() after positioning so the layer-shell subprocess receives POS
        # before the first frame, preventing a visible jump from (0,0).
        thumb.show_all()

        # Create styled list row
        row = Gtk.ListBoxRow()
        row.get_style_context().add_class("client-row")
        
        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row_box.set_margin_start(8)
        row_box.set_margin_end(8)
        
        icon = Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DND)
        row_box.pack_start(icon, False, False, 0)
        
        label = Gtk.Label(label=name)
        label.get_style_context().add_class("client-name")
        label.set_halign(Gtk.Align.START)
        label.set_ellipsize(3)  # Ellipsize at end
        row_box.pack_start(label, True, True, 0)
        
        row.add(row_box)
        self.client_list.add(row)
        row.show_all()
        self.client_rows[xid] = row

        def _refresh_row_label(*_args):
            raw = window.get_name() or "EVE"
            display = raw
            if " - " in raw:
                display = raw.split(" - ", 1)[1].split("[")[0].strip()
            label.set_text(display)
        window.connect("name-changed", _refresh_row_label)
        _refresh_row_label()

        self._update_status()

    def _remove_thumb(self, xid):
        t = self.thumbnails.pop(xid, None)
        if t:
            t.destroy()

        row = self.client_rows.pop(xid, None)
        if row:
            row.destroy()

        self._update_status()

    def _on_window_opened(self, _screen, window):
        if not self._check_and_add(window):
            # Only watch windows that might be EVE (by PID) — not every
            # random window on the desktop.  This prevents accumulating
            # name-changed handlers on hundreds of unrelated windows.
            xid = window.get_xid()
            if xid in self._pending_watches:
                return  # already watching
            pid = 0
            try:
                pid = window.get_pid() or 0
            except Exception:
                pass
            if pid and _is_real_eve_client_process(pid):
                hid = window.connect("name-changed", self._on_pending_window_name_changed)
                self._pending_watches[xid] = hid

    def _on_pending_window_name_changed(self, window):
        if self._check_and_add(window):
            # Successfully added — disconnect the watcher so it stops
            # running is_eve_window_steamaware + proc reads on every title change.
            xid = window.get_xid()
            hid = self._pending_watches.pop(xid, None)
            if hid:
                try:
                    window.disconnect(hid)
                except Exception:
                    pass

    def _on_window_closed(self, _screen, window):
        xid = window.get_xid()
        self._pending_watches.pop(xid, None)
        self._remove_thumb(xid)

    def _periodic_client_scan(self):
        """Safety-net scan for missed Wnck open/close signals.

        KDE Plasma / XWayland / Wine can occasionally miss a window-opened or
        window-closed transition while EVE is changing from launcher/loading
        windows into the final character client window. This keeps the client
        list and thumbnail set in sync without requiring an app restart.
        """
        try:
            self.screen.force_update()
            windows = list(self.screen.get_windows())
            live_xids = {w.get_xid() for w in windows}

            for w in windows:
                self._check_and_add(w)

            for xid in list(self.thumbnails.keys()):
                if xid not in live_xids:
                    self._remove_thumb(xid)

            active = self.screen.get_active_window()
            if active:
                self._apply_active_borders(active.get_xid())
        except Exception as e:
            print(f"[scan] periodic client scan error: {e}")
        return True

    def _on_active_changed(self, _screen, _prev):
        active = self.screen.get_active_window()
        # active can be None when a Wayland-native surface (e.g. alt-tab
        # switcher, layer-shell overlay, app launcher) takes focus — the X11
        # _NET_ACTIVE_WINDOW is momentarily cleared. On KDE Plasma 6 the signal
        # may not fire again when the XWayland window actually receives focus, so
        # schedule a delayed re-check instead of returning early.
        if not active:
            self._poll_retries = [0]
            GLib.timeout_add(150, self._poll_active_border)
            return
        self._apply_active_borders(active.get_xid())

    def _poll_active_border(self):
        """Retry active-border update after a short delay (alt-tab recovery).
        Retries up to 6 times (≈900ms total) to handle KDE Plasma 6 where
        _NET_ACTIVE_WINDOW may settle slowly after the compositor switch.
        """
        self.screen.force_update()  # flush stale Wnck X11 cache
        active = self.screen.get_active_window()
        if active:
            self._apply_active_borders(active.get_xid())
            return False  # done
        self._poll_retries[0] += 1
        if self._poll_retries[0] < 6:
            return True  # retry (GLib.timeout_add repeats while True)
        return False  # give up

    def _periodic_active_poll(self):
        """Safety-net: re-sync borders in case signal was missed.
        Avoids calling force_update() when unnecessary — it's expensive because
        it processes ALL pending X events synchronously, which with two Wine
        clients can be a significant burst.
        """
        if not self.thumbnails:
            return True  # nothing to sync
        # Try without force_update first — the signal path handles most cases.
        active = self.screen.get_active_window()
        if not active:
            # KDE Plasma 6 edge case: _NET_ACTIVE_WINDOW not yet updated.
            # Only now pay the force_update() cost.
            self.screen.force_update()
            active = self.screen.get_active_window()
        if active:
            xid = active.get_xid()
            if xid != self._last_polled_active_xid:
                self._last_polled_active_xid = xid
                self._apply_active_borders(xid)
            else:
                # XID unchanged — just re-send active state without the raise
                # timer, in case a subprocess border drifted out of sync.
                for t_xid, t in self.thumbnails.items():
                    t.set_active_state(t_xid == xid)
        return True  # keep repeating

    def _apply_active_borders(self, active_xid):
        for xid, t in self.thumbnails.items():
            is_active = (xid == active_xid)

            # Update border color
            t.set_active_state(is_active)

            # Hide/show based on setting
            if self.config.settings.get("hide_active_client", False):
                t.hide() if is_active else t.show()

            # Update opacity (layer-shell subprocess doesn't support GTK opacity)
            if not t._use_ls:
                try:
                    t.set_opacity(1.0 if is_active else self.config.settings.get("opacity", 0.95))
                except Exception:
                    pass

        # Defer re-raise for XWayland thumbnails only (layer-shell is always on top).
        if self.config.settings.get("always_on_top", True):
            GLib.timeout_add(100, self._raise_all_thumbnails)

    def _raise_all_thumbnails(self):
        for t in self.thumbnails.values():
            if t._use_ls:
                continue  # Layer-shell OVERLAY needs no raising
            try:
                t.set_keep_above(True)
                gdkwin = t.get_window()
                if gdkwin:
                    gdkwin.raise_()
            except Exception:
                pass
        return False   # don't repeat

    def _activate_window(self, window):
        try:
            if window.is_minimized():
                window.unminimize(Gtk.get_current_event_time())
            window.activate(Gtk.get_current_event_time())
        except Exception:
            pass

    def _show_settings(self, _btn):
        dialog = SettingsDialog(self, self.config)
        if dialog.run() == Gtk.ResponseType.OK:
            dialog.save_settings()
            for t in self.thumbnails.values():
                t.original_size = (self.config.settings["thumbnail_width"],
                                   self.config.settings["thumbnail_height"])
                t.resize(*t.original_size)
                t._target_w, t._target_h = t.original_size
                if not t._use_ls:
                    try:
                        t.set_opacity(self.config.settings.get("opacity", 0.95))
                    except Exception:
                        pass
                    # Update overlay visibility (only applies to GTK-rendered thumbnails)
                    if hasattr(t, 'label'):
                        if self.config.settings.get("show_overlay", True):
                            t.label.show()
                        else:
                            t.label.hide()
                    # Update border colors
                    t._update_border_style()
                # Restart capture timer with new FPS (applies to both modes)
                if t.live_window:
                    t._start_live_timer()
        dialog.destroy()

    def _update_status(self):
        count = len(self.thumbnails)
        if count == 0:
            self.status_label.set_text("No EVE clients detected")
            self.status_icon.set_from_icon_name("dialog-warning", Gtk.IconSize.MENU)
        else:
            self.status_label.set_text(f"Monitoring {count} EVE client{'s' if count != 1 else ''}")
            self.status_icon.set_from_icon_name("emblem-default", Gtk.IconSize.MENU)
