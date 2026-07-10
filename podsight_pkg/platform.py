"""Platform setup: session detection, GDK backend, gi init, X11 ctypes helpers.

IMPORTANT: importing this module has side effects (sets GDK_BACKEND before
gi loads). Every module that touches gi.repository imports this module first.
"""
import os, warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Wayland detection: if running under Wayland, force GDK to use the X11/XWayland
# backend. EVE Online on Linux always runs through Wine/Proton -> XWayland, so
# window capture via GdkX11 works correctly. Native Wayland capture requires
# PipeWire (Phase 2).
_WAYLAND_SESSION = bool(os.environ.get("WAYLAND_DISPLAY") or
                        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland")
if _WAYLAND_SESSION:
    os.environ["GDK_BACKEND"] = "x11"
    print("[podsight] Wayland detected — running via XWayland backend.")
    print("[podsight] EVE clients (Wine/Proton) are XWayland windows, capture works normally.")
else:
    os.environ.setdefault("GDK_BACKEND", "x11")
    print("[podsight] X11 session detected.")

# Detect gtk-layer-shell for native Wayland OVERLAY support.
# The main process stays on x11 backend (Wnck requires it); layer-shell
# thumbnails run as subprocesses with GDK_BACKEND=wayland.
_LAYER_SHELL_AVAILABLE = False
if _WAYLAND_SESSION:
    import subprocess as _sp, sys as _sys
    try:
        _r = _sp.run(
            [_sys.executable, "-c",
             "import os; os.environ['GDK_BACKEND']='wayland';"
             "import gi; gi.require_version('GtkLayerShell','0.1');"
             "from gi.repository import GtkLayerShell; print('ok')"],
            capture_output=True, timeout=5)
        _LAYER_SHELL_AVAILABLE = b'ok' in _r.stdout
    except Exception:
        pass
    del _sp, _sys
    if _LAYER_SHELL_AVAILABLE:
        print("[podsight] gtk-layer-shell detected — thumbnails will use Wayland OVERLAY (above fullscreen).")
    else:
        print("[podsight] gtk-layer-shell not found — thumbnails may go under Fixed Window EVE.")
        print("[podsight]   Fedora:  sudo dnf install gtk-layer-shell")
        print("[podsight]   Ubuntu:  sudo apt install gir1.2-gtk-layer-shell-0")

import gi
try:
    gi.require_version('Gtk', '3.0')
    gi.require_version('Wnck', '3.0')
    gi.require_version('GdkX11', '3.0')
except ValueError as _e:
    missing = str(_e)
    print(f"\n[podsight] ERROR: Missing GObject Introspection typelib — {missing}")
    print("  Install the required system packages and try again:")
    print("  Fedora:  sudo dnf install libwnck3 gtk3 python3-gobject")
    print("  Ubuntu:  sudo apt install gir1.2-wnck-3.0 gir1.2-gtk-3.0 python3-gi")
    raise SystemExit(1)
from gi.repository import Gtk, Gdk, GdkPixbuf, Wnck, GLib, GdkX11
import ctypes, ctypes.util

# CLI flags / identifiers -------------------------------------------------
DEBUG_CAPTURE = "--debug" in os.sys.argv
IPC_DEBUG = os.environ.get("EVE_PREVIEW_IPC_DEBUG", "").lower() in ("1", "true", "yes", "on")
IPC_DEBUG = IPC_DEBUG or os.environ.get("PODSIGHT_IPC_DEBUG", "").lower() in ("1", "true", "yes", "on")

SELF_PID = os.getpid()
# Basename of the launched script (argv[0]) — used to avoid capturing our
# own windows. __file__ would name this module, not the entry point.
SCRIPT_BASENAME = os.path.basename(os.sys.argv[0] or "podsight.py")

# ---------------------------------------------------------------------------
# Xlib helpers — own display connection (avoids GDK pointer casting issues)
# ---------------------------------------------------------------------------
_xlib      = None
_xlib_dpy  = None   # our own Display* for XQueryTree / child discovery

def _get_xlib():
    global _xlib
    if _xlib is None:
        path = ctypes.util.find_library("X11")
        if path:
            _xlib = ctypes.CDLL(path)
            _xlib.XOpenDisplay.restype  = ctypes.c_void_p
            _xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
            _xlib.XQueryTree.restype    = ctypes.c_int
            _xlib.XFree.restype         = ctypes.c_int
    return _xlib

def _xlib_display():
    global _xlib_dpy
    if _xlib_dpy is None:
        xlib = _get_xlib()
        if xlib:
            _xlib_dpy = xlib.XOpenDisplay(None)
    return _xlib_dpy

def _net_activate_window(xid, timestamp=0):
    """Send _NET_ACTIVE_WINDOW ClientMessage to bring an XWayland window forward.

    This is what wmctrl/xdotool do internally. We use it instead of Wnck so we
    have full control over the message fields (source=1 = direct user action,
    which KWin respects even with focus-stealing-prevention enabled).

    If the X11 window is currently unmapped (e.g. EVE's loading screen
    temporarily withdraws the XWayland surface), XMapWindow re-maps it first so
    that XSetInputFocus and the WM activation message can both succeed.
    """
    xlib = _get_xlib()
    dpy  = _xlib_display()
    if not xlib or not dpy:
        return False
    try:
        xlib.XDefaultRootWindow.restype  = ctypes.c_ulong
        xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        xlib.XInternAtom.restype  = ctypes.c_ulong
        xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        xlib.XSendEvent.restype   = ctypes.c_int
        xlib.XRaiseWindow.restype = ctypes.c_int
        xlib.XFlush.restype       = ctypes.c_int

        # Map the window in case it is currently unmapped (loading screen).
        try:
            xlib.XMapWindow.restype  = ctypes.c_int
            xlib.XMapWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            xlib.XMapWindow(ctypes.c_void_p(dpy), ctypes.c_ulong(xid))
        except Exception:
            pass

        root = xlib.XDefaultRootWindow(ctypes.c_void_p(dpy))
        atom = xlib.XInternAtom(ctypes.c_void_p(dpy), b"_NET_ACTIVE_WINDOW", False)

        # XClientMessageEvent — pad the struct to 96 bytes (XEvent union size).
        class _MsgData(ctypes.Union):
            _fields_ = [("l", ctypes.c_long * 5), ("b", ctypes.c_char * 20)]

        class _XClientMsg(ctypes.Structure):
            _fields_ = [
                ("type",         ctypes.c_int),
                ("serial",       ctypes.c_ulong),
                ("send_event",   ctypes.c_int),
                ("display",      ctypes.c_void_p),
                ("window",       ctypes.c_ulong),
                ("message_type", ctypes.c_ulong),
                ("format",       ctypes.c_int),
                ("data",         _MsgData),
                ("_pad",         ctypes.c_char * 64),  # XEvent is 96 bytes min
            ]

        ev = _XClientMsg()
        ev.type         = 33   # ClientMessage
        ev.send_event   = 1
        ev.display      = dpy
        ev.window       = xid
        ev.message_type = atom
        ev.format       = 32
        ev.data.l[0]    = 1          # source=1: direct user action (KWin honours this)
        ev.data.l[1]    = timestamp  # real X11 server timestamp avoids FSP rejection
        ev.data.l[2]    = 0          # currently active window (unknown)

        mask = 0x80000 | 0x100000  # SubstructureNotify | SubstructureRedirect
        xlib.XSendEvent(ctypes.c_void_p(dpy), root, 0, mask, ctypes.byref(ev))
        xlib.XRaiseWindow(ctypes.c_void_p(dpy), ctypes.c_ulong(xid))
        # XSetInputFocus: directly assign X11 keyboard focus — bypasses WM
        # focus-stealing-prevention (works for XWayland windows because the
        # XWayland compositor bridges X11 focus to Wayland seat focus).
        try:
            xlib.XSetInputFocus.restype  = ctypes.c_int
            xlib.XSetInputFocus.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
            xlib.XSetInputFocus(
                ctypes.c_void_p(dpy), ctypes.c_ulong(xid),
                1,          # RevertToPointerRoot
                timestamp,  # use same real timestamp
            )
        except Exception:
            pass
        # WM_TAKE_FOCUS: some Wine windows use ICCCM protocol and won't respond
        # to XSetInputFocus alone.  Send them a ClientMessage as well so they
        # know they're being asked to accept focus.
        try:
            wm_protocols  = xlib.XInternAtom(ctypes.c_void_p(dpy), b"WM_PROTOCOLS",  False)
            wm_take_focus = xlib.XInternAtom(ctypes.c_void_p(dpy), b"WM_TAKE_FOCUS", False)

            class _MsgData2(ctypes.Union):
                _fields_ = [("l", ctypes.c_long * 5), ("b", ctypes.c_char * 20)]

            class _XTakeFocus(ctypes.Structure):
                _fields_ = [
                    ("type",         ctypes.c_int),
                    ("serial",       ctypes.c_ulong),
                    ("send_event",   ctypes.c_int),
                    ("display",      ctypes.c_void_p),
                    ("window",       ctypes.c_ulong),
                    ("message_type", ctypes.c_ulong),
                    ("format",       ctypes.c_int),
                    ("data",         _MsgData2),
                    ("_pad",         ctypes.c_char * 64),
                ]

            tf = _XTakeFocus()
            tf.type         = 33   # ClientMessage
            tf.send_event   = 1
            tf.display      = dpy
            tf.window       = xid
            tf.message_type = wm_protocols
            tf.format       = 32
            tf.data.l[0]    = wm_take_focus
            tf.data.l[1]    = timestamp if timestamp else 1
            xlib.XSendEvent(ctypes.c_void_p(dpy), ctypes.c_ulong(xid), False, 0, ctypes.byref(tf))
        except Exception:
            pass

        xlib.XFlush(ctypes.c_void_p(dpy))
        return True
    except Exception as e:
        print(f"[activate] xlib error: {e}")
        return False

def _get_child_xids(parent_xid):
    """Return list of direct child XIDs (empty list if none / error)."""
    xlib = _get_xlib()
    dpy  = _xlib_display()
    if not xlib or not dpy:
        return []
    try:
        root_out   = ctypes.c_ulong(0)
        parent_out = ctypes.c_ulong(0)
        children_p = ctypes.c_void_p(0)
        n_children = ctypes.c_uint(0)
        xlib.XQueryTree(
            ctypes.c_void_p(dpy),
            ctypes.c_ulong(parent_xid),
            ctypes.byref(root_out),
            ctypes.byref(parent_out),
            ctypes.byref(children_p),
            ctypes.byref(n_children),
        )
        count = n_children.value
        if count == 0 or not children_p.value:
            return []
        xids = list((ctypes.c_ulong * count).from_address(children_p.value))
        xlib.XFree(ctypes.c_void_p(children_p.value))
        return xids
    except Exception:
        return []

