#!/usr/bin/env python3
"""PodSight — live thumbnail previews for multiboxing EVE Online on Linux.

Flags:
  --debug   per-frame capture diagnostics
  --stats   performance counters every 5s on stderr
"""
import os
# platform import MUST come first: it sets GDK_BACKEND before gi loads.
from podsight_pkg import platform
from podsight_pkg import __version__
from podsight_pkg.app import EVEOPreview
from podsight_pkg.stats import STATS
from podsight_pkg.platform import _WAYLAND_SESSION
from podsight_pkg import tray
from gi.repository import Gtk, Gdk, Wnck, GLib


def main():
    # Identify ourselves to the window manager: WM_CLASS becomes "podsight"
    # and every window advertises the themed icon, so KDE's taskbar and
    # alt-tab show our icon instead of a generic one.
    GLib.set_prgname("podsight")
    Gtk.Window.set_default_icon_name("podsight")

    screen = Wnck.Screen.get_default()
    if screen is None:
        if _WAYLAND_SESSION:
            print("[podsight] ERROR: Wnck could not connect via XWayland.")
            print("  Make sure XWayland is installed and running:")
            print("  Fedora: sudo dnf install xorg-x11-server-Xwayland")
        else:
            print("[podsight] ERROR: Wnck failed to get a default screen. Ensure X11/XWayland is available.")
        return
    if STATS:
        print("[stats] performance counters enabled — reporting every 5s to stderr",
              file=os.sys.stderr)
        STATS.start()
    app = EVEOPreview()

    # System tray (optional): Show/Hide + Quit menu. When the "close to
    # tray" setting is on, closing the window hides it instead of quitting.
    app_tray = None
    if tray.TRAY_AVAILABLE:
        app_tray = tray.Tray(app)
    else:
        print("[podsight] Tray backend not found — tray options disabled.")
        print("[podsight]   Fedora: sudo dnf install libayatana-appindicator-gtk3")

    def _on_delete(widget, _event):
        if app_tray and widget.config.settings.get("close_to_tray", False):
            widget.hide()
            return True   # stop the default destroy
        return False
    app.connect("delete-event", _on_delete)
    app.connect("destroy", Gtk.main_quit)

    # Re-assert keep-above whenever the management window is (re-)mapped —
    # e.g. after un-minimising from the taskbar.  This ensures it appears
    # above any XWayland fullscreen EVE surface.
    def _on_app_map(widget):
        widget.set_keep_above(True)
        gdk_win = widget.get_window()
        if gdk_win:
            gdk_win.raise_()
    app.connect("map", _on_app_map)

    # Periodically re-assert keep-above so that if KDE strips the ABOVE flag
    # (e.g. when a fullscreen XWayland EVE window takes focus), the management
    # window eventually floats back.  A window-state-event handler would cause
    # an infinite feedback loop (set_keep_above → fires window-state-event →
    # set_keep_above → ...) that freezes the UI on alt-tab, so we use a timer.
    def _keep_app_above():
        try:
            gdk_win = app.get_window()
            if gdk_win and not (gdk_win.get_state() & Gdk.WindowState.ICONIFIED):
                app.set_keep_above(True)
        except Exception:
            pass
        return True  # keep repeating
    GLib.timeout_add(4000, _keep_app_above)

    if app_tray and app.config.settings.get("start_in_tray", False):
        print("[podsight] Starting hidden in tray.")
    else:
        app.show_all()
    Gtk.main()

if __name__ == "__main__":
    main()
