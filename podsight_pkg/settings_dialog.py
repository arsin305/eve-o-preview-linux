"""Settings dialog for the management window."""
from . import platform  # noqa: F401 — env/gi setup must run first
from gi.repository import Gtk, Gdk

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

        # Tray options — disabled (greyed out) when the tray backend is absent.
        from .tray import TRAY_AVAILABLE
        self.close_to_tray = Gtk.CheckButton(label="Close to tray instead of quitting")
        self.close_to_tray.set_active(self.config.settings.get("close_to_tray", False))
        behavior_box.pack_start(self.close_to_tray, False, False, 0)

        self.start_in_tray = Gtk.CheckButton(label="Start hidden in tray")
        self.start_in_tray.set_active(self.config.settings.get("start_in_tray", False))
        behavior_box.pack_start(self.start_in_tray, False, False, 0)

        self.hotkeys_enabled = Gtk.CheckButton(
            label="Client-switching hotkeys (Ctrl+Alt+←/→, Ctrl+Alt+1-9)")
        self.hotkeys_enabled.set_active(self.config.settings.get("hotkeys_enabled", True))
        self.hotkeys_enabled.set_tooltip_text(
            "Cycle or jump focus between EVE clients from anywhere in-game.\n"
            "Focus switching only — never sends input to clients.\n"
            "Takes effect after restarting PodSight.")
        behavior_box.pack_start(self.hotkeys_enabled, False, False, 0)

        self.show_hotkey_overlay = Gtk.CheckButton(
            label="Show hotkey label at the bottom of thumbnails")
        self.show_hotkey_overlay.set_active(
            self.config.settings.get("show_hotkey_overlay", True))
        behavior_box.pack_start(self.show_hotkey_overlay, False, False, 0)

        self.snap_to_grid = Gtk.CheckButton(label="Snap thumbnails to a 32 px grid while dragging")
        self.snap_to_grid.set_active(self.config.settings.get("snap_to_grid", False))
        behavior_box.pack_start(self.snap_to_grid, False, False, 0)

        self.edge_snap = Gtk.CheckButton(label="Snap thumbnails flush against each other on release")
        self.edge_snap.set_active(self.config.settings.get("edge_snap", False))
        behavior_box.pack_start(self.edge_snap, False, False, 0)

        if not TRAY_AVAILABLE:
            for cb in (self.close_to_tray, self.start_in_tray):
                cb.set_sensitive(False)
                cb.set_tooltip_text(
                    "Requires the system tray backend:\n"
                    "sudo dnf install libayatana-appindicator-gtk3")

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

        self.fps_30 = Gtk.RadioButton.new_with_label_from_widget(self.fps_10, "30 FPS")
        self.fps_30.set_tooltip_text("30 FPS — higher CPU usage, best with 1-2 clients")
        fps_box.pack_start(self.fps_30, False, False, 0)

        # Set current FPS selection
        current_fps = self.config.settings.get("refresh_fps", 10)
        if current_fps == 10:
            self.fps_10.set_active(True)
        elif current_fps == 15:
            self.fps_15.set_active(True)
        elif current_fps == 25:
            self.fps_25.set_active(True)
        elif current_fps == 30:
            self.fps_30.set_active(True)
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
        self.config.settings["close_to_tray"] = self.close_to_tray.get_active()
        self.config.settings["start_in_tray"] = self.start_in_tray.get_active()
        self.config.settings["hotkeys_enabled"] = self.hotkeys_enabled.get_active()
        self.config.settings["show_hotkey_overlay"] = self.show_hotkey_overlay.get_active()
        self.config.settings["snap_to_grid"] = self.snap_to_grid.get_active()
        self.config.settings["edge_snap"] = self.edge_snap.get_active()
        
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
        elif self.fps_30.get_active():
            self.config.settings["refresh_fps"] = 30

        self.config.save()
