#!/usr/bin/env python3
# EVE-O Preview for Linux (Live) – v5 click-or-drag with Enhanced UI
# - Left-click: focus client
# - Left-drag (move > threshold): move thumbnail
# - Right-click: move thumbnail
# - Ctrl+Left: minimize client
# - Thumbnails accept focus (fixes focus when main window isn't active)
# - Live previews via GdkX11 + gdk_pixbuf_get_from_window

import os, json, warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
os.environ.setdefault("GDK_BACKEND", "x11")

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Wnck', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, Wnck, GLib, GdkX11

SELF_PID = os.getpid()
SCRIPT_BASENAME = os.path.basename(__file__)

def _proc_cmdline_contains(pid: int, needles):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read().replace(b"\x00", b" ")
        low = raw.lower()
        return any(n.encode("utf-8").lower() in low for n in needles)
    except Exception:
        return False

def is_eve_window_steamaware(wnck_window):
    try:
        if wnck_window.get_pid() == SELF_PID:
            return False
    except Exception:
        pass
    try:
        pid = wnck_window.get_pid()
        if pid and _proc_cmdline_contains(pid, ["exefile.exe"]):
            return True
    except Exception:
        pass
    try:
        name = (wnck_window.get_name() or "").lower()
        if not name:
            return False
        if SCRIPT_BASENAME.lower() in name or "eve-o preview" in name:
            return False
        if name.startswith("eve - ") and "launcher" not in name:
            return True
    except Exception:
        pass
    return False

class Config:
    def __init__(self):
        from pathlib import Path as _Path
        self.config_dir = _Path.home() / ".config" / "eve-o-preview-linux"
        self.config_file = self.config_dir / "config.json"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.default_config = {
            "thumbnail_width": 320,
            "thumbnail_height": 200,
            "opacity": 0.95,
            "always_on_top": True,
            "hide_active_client": False,
            "zoom_on_hover": True,
            "zoom_factor": 1.25,
            "show_overlay": True,
            "refresh_fps": 10,  # FPS instead of period
            "active_border_color": "#00FF00",  # Neon green default
            "thumbnail_positions": {}
        }
        self.settings = self.load()

    def load(self):
        try:
            if self.config_file.exists():
                with open(self.config_file, "r") as f:
                    data = json.load(f)
                merged = self.default_config.copy()
                merged.update(data)
                return merged
        except Exception as e:
            print("Config load error:", e)
        return self.default_config.copy()

    def save(self):
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            print("Config save error:", e)

class ThumbnailWindow(Gtk.Window):
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
        self._target_w, self._target_h = self.original_size

        # window behavior
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(True)
        self.set_decorated(False)
        if self.config.settings.get("always_on_top", True):
            self.set_keep_above(True)
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
            self.move(int(pos[0]), int(pos[1]))

    def bind_live(self, xid, target_w, target_h):
        try:
            display = GdkX11.X11Display.get_default()
            if not display:
                raise RuntimeError("No X11 display for GdkX11")
            self.live_window = GdkX11.X11Window.foreign_new_for_display(display, xid)
            self._target_w, self._target_h = int(target_w), int(target_h)
            self._start_live_timer()
        except Exception as e:
            print("Live capture bind failed:", e)

    def _start_live_timer(self):
        if self.update_id:
            GLib.source_remove(self.update_id)
        
        # Convert FPS to milliseconds
        fps = int(self.config.settings.get("refresh_fps", 10))
        period = int(1000 / fps)  # Convert FPS to ms

        def tick():
            if not self.live_window:
                return False
            try:
                if self.wnck_window.is_minimized():
                    self._set_icon_fallback()
                    return True
            except Exception:
                pass
            try:
                w = self.live_window.get_width()
                h = self.live_window.get_height()
                if w <= 0 or h <= 0:
                    self._set_icon_fallback()
                    return True
                pb = Gdk.pixbuf_get_from_window(self.live_window, 0, 0, w, h)
                if pb:
                    pb = pb.scale_simple(self._target_w, self._target_h, GdkPixbuf.InterpType.BILINEAR)
                    self.image.set_from_pixbuf(pb)
                else:
                    self._set_icon_fallback()
            except Exception:
                self._set_icon_fallback()
            return True

        self.update_id = GLib.timeout_add(period, tick)

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
            self.begin_move_drag(event.button, int(event.x_root), int(event.y_root), event.time)
            return True
        return False

    def _on_motion(self, _w, event):
        if not self._press_pos:
            return False
        dx = abs(event.x_root - self._press_pos[0])
        dy = abs(event.y_root - self._press_pos[1])
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
        try:
            x, y = self.get_position()
            name = self.wnck_window.get_name()
            cfg = self.config.settings.setdefault("thumbnail_positions", {})
            cfg[name] = [x, y]
            self.config.save()
        except Exception:
            pass
        if self.update_id:
            GLib.source_remove(self.update_id)
            self.update_id = None

class EVEOPreview(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.config = Config()
        self.thumbnails = {}
        self.screen = Wnck.Screen.get_default()
        self.screen.force_update()

        self.set_title("EVE-O Preview")
        self.set_default_size(500, 400)
        self.set_position(Gtk.WindowPosition.CENTER)

        # Apply modern styling
        self._apply_styles()

        # Header bar
        headerbar = Gtk.HeaderBar()
        headerbar.set_show_close_button(True)
        headerbar.set_title("EVE-O Preview")
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

        self._scan_existing()

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

    def _check_and_add(self, window):
        if is_eve_window_steamaware(window):
            xid = window.get_xid()
            if xid not in self.thumbnails:
                self._add_thumb(window)

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
        thumb.show_all()
        self.thumbnails[xid] = thumb
        thumb.bind_live(xid, self.config.settings["thumbnail_width"], self.config.settings["thumbnail_height"])

        name = window.get_name()
        pos = self.config.settings.get("thumbnail_positions", {}).get(name)
        if pos:
            thumb.move(int(pos[0]), int(pos[1]))
        else:
            self._place_thumb(thumb)

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
        
        self._update_status()

    def _remove_thumb(self, xid):
        t = self.thumbnails.pop(xid, None)
        if t: t.destroy()
        self._update_status()

    def _on_window_opened(self, _screen, window):
        self._check_and_add(window)

    def _on_window_closed(self, _screen, window):
        self._remove_thumb(window.get_xid())

    def _on_active_changed(self, _screen, _prev):
        active = self.screen.get_active_window()
        if not active: return
        active_xid = active.get_xid()
        
        for xid, t in self.thumbnails.items():
            is_active = (xid == active_xid)
            
            # Update border color
            t.set_active_state(is_active)
            
            # Hide/show based on setting
            if self.config.settings.get("hide_active_client", False):
                t.hide() if is_active else t.show()
            
            # Update opacity
            try:
                t.set_opacity(1.0 if is_active else self.config.settings.get("opacity", 0.95))
            except Exception:
                pass

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
                try:
                    t.set_opacity(self.config.settings.get("opacity", 0.95))
                except Exception:
                    pass
                # Update overlay visibility
                if hasattr(t, 'label'):
                    if self.config.settings.get("show_overlay", True):
                        t.label.show()
                    else:
                        t.label.hide()
                # Restart timer with new FPS
                if t.live_window:
                    t._start_live_timer()
                # Update border colors
                t._update_border_style()
        dialog.destroy()

    def _update_status(self):
        count = len(self.thumbnails)
        if count == 0:
            self.status_label.set_text("No EVE clients detected")
            self.status_icon.set_from_icon_name("dialog-warning", Gtk.IconSize.MENU)
        else:
            self.status_label.set_text(f"Monitoring {count} EVE client{'s' if count != 1 else ''}")
            self.status_icon.set_from_icon_name("emblem-default", Gtk.IconSize.MENU)

class SettingsDialog(Gtk.Dialog):
    def __init__(self, parent, config):
        super().__init__(title="Settings", parent=parent, flags=0)
        self.config = config
        self.set_default_size(480, 520)
        self.set_resizable(False)

        # Header bar for dialog
        headerbar = Gtk.HeaderBar()
        headerbar.set_show_close_button(False)
        headerbar.set_title("Settings")
        self.set_titlebar(headerbar)

        # Buttons in header
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda x: self.response(Gtk.ResponseType.CANCEL))
        headerbar.pack_start(cancel_btn)

        apply_btn = Gtk.Button(label="Apply")
        apply_btn.get_style_context().add_class("suggested-action")
        apply_btn.connect("clicked", lambda x: self.response(Gtk.ResponseType.OK))
        headerbar.pack_end(apply_btn)

        box = self.get_content_area()
        box.set_spacing(0)

        # Apply styles
        self._apply_styles()

        # Create notebook for categories
        notebook = Gtk.Notebook()
        notebook.set_margin_start(0)
        notebook.set_margin_end(0)
        notebook.set_margin_top(0)
        notebook.set_margin_bottom(0)
        box.add(notebook)

        # Display settings page
        display_page = self._create_display_page()
        notebook.append_page(display_page, Gtk.Label(label="Display"))

        # Behavior settings page
        behavior_page = self._create_behavior_page()
        notebook.append_page(behavior_page, Gtk.Label(label="Behavior"))

        self.show_all()

    def _apply_styles(self):
        css_provider = Gtk.CssProvider()
        css = b"""
        .section-title {
            font-weight: bold;
            margin-bottom: 8px;
        }
        """
        css_provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _create_display_page(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_margin_start(15)
        vbox.set_margin_end(15)
        vbox.set_margin_top(15)
        vbox.set_margin_bottom(15)

        # Dimensions section
        dim_label = Gtk.Label(label="Thumbnail Dimensions")
        dim_label.set_halign(Gtk.Align.START)
        dim_label.get_style_context().add_class("section-title")
        vbox.pack_start(dim_label, False, False, 0)

        dim_grid = Gtk.Grid()
        dim_grid.set_column_spacing(10)
        dim_grid.set_row_spacing(8)
        dim_grid.set_margin_start(8)
        dim_grid.set_margin_bottom(15)

        # Width
        width_label = Gtk.Label(label="Width:")
        width_label.set_halign(Gtk.Align.END)
        dim_grid.attach(width_label, 0, 0, 1, 1)
        
        self.w_spin = Gtk.SpinButton()
        self.w_spin.set_range(100, 800)
        self.w_spin.set_increments(10, 50)
        self.w_spin.set_value(self.config.settings["thumbnail_width"])
        self.w_spin.set_hexpand(True)
        dim_grid.attach(self.w_spin, 1, 0, 1, 1)
        
        width_px = Gtk.Label(label="px")
        dim_grid.attach(width_px, 2, 0, 1, 1)

        # Height
        height_label = Gtk.Label(label="Height:")
        height_label.set_halign(Gtk.Align.END)
        dim_grid.attach(height_label, 0, 1, 1, 1)
        
        self.h_spin = Gtk.SpinButton()
        self.h_spin.set_range(80, 600)
        self.h_spin.set_increments(10, 50)
        self.h_spin.set_value(self.config.settings["thumbnail_height"])
        self.h_spin.set_hexpand(True)
        dim_grid.attach(self.h_spin, 1, 1, 1, 1)
        
        height_px = Gtk.Label(label="px")
        dim_grid.attach(height_px, 2, 1, 1, 1)

        vbox.pack_start(dim_grid, False, False, 0)

        # Appearance section
        appear_label = Gtk.Label(label="Appearance")
        appear_label.set_halign(Gtk.Align.START)
        appear_label.get_style_context().add_class("section-title")
        vbox.pack_start(appear_label, False, False, 0)

        appear_grid = Gtk.Grid()
        appear_grid.set_column_spacing(10)
        appear_grid.set_row_spacing(8)
        appear_grid.set_margin_start(8)

        # Opacity
        opacity_label = Gtk.Label(label="Opacity:")
        opacity_label.set_halign(Gtk.Align.END)
        appear_grid.attach(opacity_label, 0, 0, 1, 1)
        
        opacity_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.opacity = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.2, 1.0, 0.05)
        self.opacity.set_value(self.config.settings["opacity"])
        self.opacity.set_hexpand(True)
        self.opacity.set_value_pos(Gtk.PositionType.RIGHT)
        self.opacity.set_digits(2)
        opacity_box.pack_start(self.opacity, True, True, 0)
        appear_grid.attach(opacity_box, 1, 0, 2, 1)

        vbox.pack_start(appear_grid, False, False, 0)

        # Active border color
        border_label = Gtk.Label(label="Active Border Color")
        border_label.set_halign(Gtk.Align.START)
        border_label.get_style_context().add_class("section-title")
        border_label.set_margin_top(10)
        vbox.pack_start(border_label, False, False, 0)

        border_grid = Gtk.Grid()
        border_grid.set_column_spacing(10)
        border_grid.set_row_spacing(8)
        border_grid.set_margin_start(8)

        color_label = Gtk.Label(label="Color (Hex):")
        color_label.set_halign(Gtk.Align.END)
        border_grid.attach(color_label, 0, 0, 1, 1)

        # Color entry box
        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        
        self.color_entry = Gtk.Entry()
        self.color_entry.set_text(self.config.settings.get("active_border_color", "#00FF00"))
        self.color_entry.set_max_length(7)
        self.color_entry.set_width_chars(10)
        self.color_entry.set_placeholder_text("#00FF00")
        color_box.pack_start(self.color_entry, False, False, 0)
        
        # Color button for visual picker
        current_color = self.config.settings.get("active_border_color", "#00FF00")
        rgba = Gdk.RGBA()
        rgba.parse(current_color)
        
        self.color_button = Gtk.ColorButton()
        self.color_button.set_rgba(rgba)
        self.color_button.set_title("Choose Border Color")
        self.color_button.connect("color-set", self._on_color_picked)
        color_box.pack_start(self.color_button, False, False, 0)
        
        # Preview box
        self.color_preview = Gtk.DrawingArea()
        self.color_preview.set_size_request(30, 30)
        self.color_preview.connect("draw", self._draw_color_preview)
        color_box.pack_start(self.color_preview, False, False, 0)
        
        border_grid.attach(color_box, 1, 0, 2, 1)
        
        # Preset colors
        preset_label = Gtk.Label(label="Presets:")
        preset_label.set_halign(Gtk.Align.END)
        border_grid.attach(preset_label, 0, 1, 1, 1)
        
        preset_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        
        presets = [
            ("#00FF00", "Neon Green"),
            ("#00FFFF", "Cyan"),
            ("#FF00FF", "Magenta"),
            ("#FFFF00", "Yellow"),
            ("#FF0000", "Red"),
            ("#0080FF", "Blue")
        ]
        
        for color, tooltip in presets:
            btn = Gtk.Button()
            btn.set_size_request(25, 25)
            btn.set_tooltip_text(tooltip)
            btn.connect("clicked", self._on_preset_clicked, color)
            
            # Style the button with the color
            css = Gtk.CssProvider()
            css.load_from_data(f"button {{ background: {color}; min-width: 25px; min-height: 25px; border-radius: 3px; }}".encode())
            btn.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            
            preset_box.pack_start(btn, False, False, 0)
        
        border_grid.attach(preset_box, 1, 1, 2, 1)
        
        vbox.pack_start(border_grid, False, False, 0)

        return vbox

    def _on_color_picked(self, color_button):
        """When color is picked from ColorButton, update entry"""
        rgba = color_button.get_rgba()
        hex_color = "#{:02x}{:02x}{:02x}".format(
            int(rgba.red * 255),
            int(rgba.green * 255),
            int(rgba.blue * 255)
        ).upper()
        self.color_entry.set_text(hex_color)
        self.color_preview.queue_draw()

    def _on_preset_clicked(self, button, color):
        """When preset color is clicked"""
        self.color_entry.set_text(color)
        rgba = Gdk.RGBA()
        rgba.parse(color)
        self.color_button.set_rgba(rgba)
        self.color_preview.queue_draw()

    def _draw_color_preview(self, widget, cr):
        """Draw the color preview box"""
        try:
            color_text = self.color_entry.get_text()
            if not color_text.startswith("#") or len(color_text) != 7:
                color_text = "#00FF00"
            
            rgba = Gdk.RGBA()
            if rgba.parse(color_text):
                cr.set_source_rgb(rgba.red, rgba.green, rgba.blue)
                cr.rectangle(0, 0, widget.get_allocated_width(), widget.get_allocated_height())
                cr.fill()
                
                # Draw border
                cr.set_source_rgb(0, 0, 0)
                cr.set_line_width(1)
                cr.rectangle(0, 0, widget.get_allocated_width(), widget.get_allocated_height())
                cr.stroke()
        except Exception:
            pass
        return False

    def _create_behavior_page(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_margin_start(15)
        vbox.set_margin_end(15)
        vbox.set_margin_top(15)
        vbox.set_margin_bottom(15)

        # Window behavior section
        behavior_label = Gtk.Label(label="Window Behavior")
        behavior_label.set_halign(Gtk.Align.START)
        behavior_label.get_style_context().add_class("section-title")
        vbox.pack_start(behavior_label, False, False, 0)

        behavior_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        behavior_box.set_margin_start(8)
        behavior_box.set_margin_bottom(15)

        self.always_on_top = Gtk.CheckButton(label="Keep thumbnails always on top")
        self.always_on_top.set_active(self.config.settings["always_on_top"])
        behavior_box.pack_start(self.always_on_top, False, False, 0)

        self.hide_active = Gtk.CheckButton(label="Hide active client thumbnail")
        self.hide_active.set_active(self.config.settings["hide_active_client"])
        behavior_box.pack_start(self.hide_active, False, False, 0)

        self.show_overlay = Gtk.CheckButton(label="Show character name overlay")
        self.show_overlay.set_active(self.config.settings["show_overlay"])
        behavior_box.pack_start(self.show_overlay, False, False, 0)

        vbox.pack_start(behavior_box, False, False, 0)

        # Interaction section
        interact_label = Gtk.Label(label="Interaction")
        interact_label.set_halign(Gtk.Align.START)
        interact_label.get_style_context().add_class("section-title")
        vbox.pack_start(interact_label, False, False, 0)

        interact_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        interact_box.set_margin_start(8)
        interact_box.set_margin_bottom(15)

        self.zoom_hover = Gtk.CheckButton(label="Zoom thumbnail on mouse hover")
        self.zoom_hover.set_active(self.config.settings["zoom_on_hover"])
        interact_box.pack_start(self.zoom_hover, False, False, 0)

        # Zoom factor
        zoom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        zoom_box.set_margin_start(20)
        zoom_label = Gtk.Label(label="Zoom factor:")
        zoom_box.pack_start(zoom_label, False, False, 0)
        
        self.zoom_factor = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1.1, 2.0, 0.05)
        self.zoom_factor.set_value(self.config.settings.get("zoom_factor", 1.25))
        self.zoom_factor.set_hexpand(True)
        self.zoom_factor.set_value_pos(Gtk.PositionType.RIGHT)
        self.zoom_factor.set_digits(2)
        zoom_box.pack_start(self.zoom_factor, True, True, 0)
        
        interact_box.pack_start(zoom_box, False, False, 0)

        vbox.pack_start(interact_box, False, False, 0)

        # Performance section
        perf_label = Gtk.Label(label="Performance")
        perf_label.set_halign(Gtk.Align.START)
        perf_label.get_style_context().add_class("section-title")
        vbox.pack_start(perf_label, False, False, 0)

        perf_grid = Gtk.Grid()
        perf_grid.set_column_spacing(10)
        perf_grid.set_row_spacing(8)
        perf_grid.set_margin_start(8)

        refresh_label = Gtk.Label(label="Refresh rate:")
        refresh_label.set_halign(Gtk.Align.END)
        refresh_label.set_tooltip_text("Higher FPS = smoother but more CPU usage")
        perf_grid.attach(refresh_label, 0, 0, 1, 1)

        # Create radio buttons for FPS options
        fps_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=15)
        
        self.fps_10 = Gtk.RadioButton.new_with_label_from_widget(None, "10 FPS")
        fps_box.pack_start(self.fps_10, False, False, 0)
        
        self.fps_15 = Gtk.RadioButton.new_with_label_from_widget(self.fps_10, "15 FPS")
        fps_box.pack_start(self.fps_15, False, False, 0)
        
        self.fps_25 = Gtk.RadioButton.new_with_label_from_widget(self.fps_10, "25 FPS")
        fps_box.pack_start(self.fps_25, False, False, 0)
        
        # Set current FPS selection
        current_fps = self.config.settings.get("refresh_fps", 10)
        if current_fps == 10:
            self.fps_10.set_active(True)
        elif current_fps == 15:
            self.fps_15.set_active(True)
        elif current_fps == 25:
            self.fps_25.set_active(True)
        else:
            self.fps_10.set_active(True)
        
        perf_grid.attach(fps_box, 1, 0, 2, 1)

        vbox.pack_start(perf_grid, False, False, 0)

        # Info section at bottom
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        info_box.set_margin_top(20)
        
        info_label = Gtk.Label(label="Tip: Left-click to focus • Left-drag to move\nRight-drag to reposition • Ctrl+Click to minimize")
        info_label.set_line_wrap(True)
        info_label.set_justify(Gtk.Justification.CENTER)
        info_label.set_halign(Gtk.Align.CENTER)
        info_box.pack_start(info_label, False, False, 0)
        
        vbox.pack_end(info_box, False, False, 0)

        return vbox

    def save_settings(self):
        self.config.settings["thumbnail_width"] = int(self.w_spin.get_value())
        self.config.settings["thumbnail_height"] = int(self.h_spin.get_value())
        self.config.settings["opacity"] = float(self.opacity.get_value())
        self.config.settings["always_on_top"] = self.always_on_top.get_active()
        self.config.settings["hide_active_client"] = self.hide_active.get_active()
        self.config.settings["zoom_on_hover"] = self.zoom_hover.get_active()
        self.config.settings["zoom_factor"] = float(self.zoom_factor.get_value())
        self.config.settings["show_overlay"] = self.show_overlay.get_active()
        
        # Save border color
        color_text = self.color_entry.get_text()
        if color_text.startswith("#") and len(color_text) == 7:
            self.config.settings["active_border_color"] = color_text.upper()
        
        # Save FPS selection
        if self.fps_10.get_active():
            self.config.settings["refresh_fps"] = 10
        elif self.fps_15.get_active():
            self.config.settings["refresh_fps"] = 15
        elif self.fps_25.get_active():
            self.config.settings["refresh_fps"] = 25
        
        self.config.save()

def main():
    screen = Wnck.Screen.get_default()
    if screen is None:
        print("Wnck failed to get a default screen. Ensure X11 backend.")
        return
    app = EVEOPreview()
    app.connect("destroy", Gtk.main_quit)
    app.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()