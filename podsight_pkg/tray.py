"""System tray icon via StatusNotifier (AyatanaAppIndicator).

Plasma 6 (and most modern desktops) expose tray icons through the
StatusNotifier D-Bus protocol; AyatanaAppIndicator is the maintained GTK3
binding for it. If the typelib is missing, TRAY_AVAILABLE stays False and
the app runs exactly as before — tray settings become inert.

Fedora package: libayatana-appindicator-gtk3
"""
from . import platform  # noqa: F401 — env/gi setup must run first
import gi

TRAY_AVAILABLE = False
AppIndicator = None
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator
    TRAY_AVAILABLE = True
except (ValueError, ImportError):
    pass

from gi.repository import Gtk


class Tray:
    """Tray icon with a Show/Hide toggle and Quit. Owns no app state."""

    def __init__(self, app_window):
        self.app = app_window
        self.indicator = AppIndicator.Indicator.new(
            "podsight", "podsight",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("PodSight")

        menu = Gtk.Menu()
        toggle = Gtk.MenuItem(label="Show/Hide PodSight")
        toggle.connect("activate", self._toggle)
        menu.append(toggle)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self._quit)
        menu.append(quit_item)
        menu.show_all()
        self.indicator.set_menu(menu)
        # Middle-click on the tray icon = toggle (where the desktop supports it).
        try:
            self.indicator.set_secondary_activate_target(toggle)
        except Exception:
            pass

    def _toggle(self, *_args):
        if self.app.get_visible():
            self.app.hide()
        else:
            self.app.show_all()
            self.app.present()

    def _quit(self, *_args):
        Gtk.main_quit()
