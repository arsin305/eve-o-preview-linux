"""Client-switching hotkeys via XGrabKey on the X/XWayland server.

Default bindings (fixed in v1, toggle in Settings):
    Ctrl+Alt+Right / Ctrl+Alt+Left   cycle to next / previous EVE client
    Ctrl+Alt+1 .. Ctrl+Alt+9         focus client N (management-window order)

Scope note: grabs live on the X server, so hotkeys fire whenever any
X11/XWayland window has focus — which includes every EVE client (Wine).
They do not fire while a native Wayland window is focused; that upgrade
would go through the XDG GlobalShortcuts portal later.

SCOPE: These hotkeys ONLY change which client window has focus. They never
send input to clients — input broadcasting/automation violates CCP's EULA
and is out of scope for PodSight by design.
"""
import ctypes
import ctypes.util
import time

from . import platform  # noqa: F401 — env/gi setup must run first
from gi.repository import GLib

# X11 constants
_KeyPress = 2
_GrabModeAsync = 1
_ControlMask = 1 << 2
_Mod1Mask = 1 << 3      # Alt
_LockMask = 1 << 1      # CapsLock
_Mod2Mask = 1 << 4      # NumLock
_XK_Left, _XK_Right = 0xFF51, 0xFF53
_XK_1 = 0x31            # '1'..'9' are 0x31..0x39

_MODS = _ControlMask | _Mod1Mask
# Grab each key under every Lock/NumLock combination, or the grab silently
# misses when either lock is engaged.
_LOCK_VARIANTS = (0, _LockMask, _Mod2Mask, _LockMask | _Mod2Mask)


class _XKeyEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("root", ctypes.c_ulong),
        ("subwindow", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("x", ctypes.c_int), ("y", ctypes.c_int),
        ("x_root", ctypes.c_int), ("y_root", ctypes.c_int),
        ("state", ctypes.c_uint),
        ("keycode", ctypes.c_uint),
        ("same_screen", ctypes.c_int),
    ]


class HotkeyManager:
    """Grabs the client-switching keys and dispatches to the app.

    `app` must provide focus_client_by_offset(step) and
    focus_client_by_index(idx). All dispatch happens on the GLib main loop
    (we are woken by an fd watch on the X connection).
    """

    def __init__(self, app):
        self.app = app
        self.active = False
        self._err = [False]
        self._handler_ref = None       # GC guard for the error handler
        self._keymap = {}              # keycode -> action tuple
        self._last_fire = 0.0          # debounce for key auto-repeat

        path = ctypes.util.find_library("X11")
        if not path:
            print("[podsight] hotkeys: libX11 not found — hotkeys disabled.")
            return
        x = self._x = ctypes.CDLL(path)
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XGrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint,
                               ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int]
        x.XUngrabKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint,
                                 ctypes.c_ulong]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XPending.restype = ctypes.c_int
        x.XPending.argtypes = [ctypes.c_void_p]
        x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        x.XConnectionNumber.restype = ctypes.c_int
        x.XConnectionNumber.argtypes = [ctypes.c_void_p]

        self._dpy = x.XOpenDisplay(None)
        if not self._dpy:
            print("[podsight] hotkeys: cannot open X display — hotkeys disabled.")
            return

        # Non-fatal error handler: a colliding grab raises BadAccess; we flag
        # it and report which combo lost, instead of crashing.
        @ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
        def _on_err(_d, _e):
            self._err[0] = True
            return 0
        self._handler_ref = _on_err
        x.XSetErrorHandler(_on_err)

        self._root = x.XDefaultRootWindow(self._dpy)

        # Build the binding table: keysym -> (action, arg, label)
        bindings = [
            (_XK_Right, ("cycle", +1), "Ctrl+Alt+Right"),
            (_XK_Left, ("cycle", -1), "Ctrl+Alt+Left"),
        ]
        for i in range(9):
            bindings.append((_XK_1 + i, ("index", i), f"Ctrl+Alt+{i + 1}"))

        grabbed = 0
        for keysym, action, label in bindings:
            keycode = x.XKeysymToKeycode(self._dpy, keysym)
            if not keycode:
                continue
            self._err[0] = False
            for variant in _LOCK_VARIANTS:
                x.XGrabKey(self._dpy, keycode, _MODS | variant, self._root,
                           0, _GrabModeAsync, _GrabModeAsync)
            x.XSync(self._dpy, 0)
            if self._err[0]:
                print(f"[podsight] hotkeys: {label} is grabbed by another app "
                      f"(KDE shortcut?) — that combo won't work.")
            else:
                self._keymap[keycode] = action
                grabbed += 1

        if not self._keymap:
            print("[podsight] hotkeys: no keys could be grabbed — disabled.")
            return

        # Wake on X events via the connection's file descriptor.
        fd = x.XConnectionNumber(self._dpy)
        self._watch = GLib.io_add_watch(fd, GLib.PRIORITY_HIGH,
                                        GLib.IOCondition.IN, self._on_x_ready)
        self.active = True
        print(f"[podsight] Hotkeys active ({grabbed} bindings): "
              f"Ctrl+Alt+\u2190/\u2192 cycle clients, Ctrl+Alt+1-9 direct.")

    # -- event pump ---------------------------------------------------------
    def _on_x_ready(self, _fd, _cond):
        x = self._x
        buf = (ctypes.c_long * 24)()   # XEvent is a union of 24 longs
        while x.XPending(self._dpy):
            x.XNextEvent(self._dpy, ctypes.byref(buf))
            ev = ctypes.cast(buf, ctypes.POINTER(_XKeyEvent)).contents
            if ev.type != _KeyPress:
                continue
            action = self._keymap.get(ev.keycode)
            if action is None or (ev.state & _MODS) != _MODS:
                continue
            # Auto-repeat guard: holding the key fires repeats ~30/s; one
            # switch per 250 ms is what a human wants.
            now = time.monotonic()
            if now - self._last_fire < 0.25:
                continue
            self._last_fire = now
            kind, arg = action
            if kind == "cycle":
                self.app.focus_client_by_offset(arg)
            else:
                self.app.focus_client_by_index(arg)
        return True   # keep the watch alive

    # -- teardown -----------------------------------------------------------
    def shutdown(self):
        if not getattr(self, "_dpy", None):
            return
        try:
            for keycode in self._keymap:
                for variant in _LOCK_VARIANTS:
                    self._x.XUngrabKey(self._dpy, keycode, _MODS | variant,
                                       self._root)
            self._x.XSync(self._dpy, 0)
        except Exception:
            pass
        self.active = False
