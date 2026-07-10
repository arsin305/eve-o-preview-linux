"""Wayland layer-shell OVERLAY support.

Contains the helper script each thumbnail runs as a GDK_BACKEND=wayland
subprocess, plus _LayerShellDisplay which manages one such subprocess.
"""
import os, time
from . import platform  # noqa: F401 — env/gi setup must run first
from .platform import IPC_DEBUG
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib

# ---------------------------------------------------------------------------
# gtk-layer-shell subprocess helper script
# Each thumbnail spawns one instance of this script with GDK_BACKEND=wayland.
# It creates a GtkLayerShell OVERLAY window — guaranteed above every fullscreen
# surface by the Wayland compositor — and communicates via stdin/stdout pipes.
# ---------------------------------------------------------------------------
_LAYER_SHELL_HELPER = r"""
import sys, os, base64, threading, time as _time, ctypes as _ct
os.environ.setdefault("GDK_BACKEND", "wayland")

# ---------------------------------------------------------------------------
# XQueryPointer — true screen-absolute pointer position via X11/XWayland.
# GDK3 Wayland derives "root" coordinates as window_origin + local_x, but
# for layer-shell windows it uses origin=(0,0), making "root" == window-local.
# XQueryPointer bypasses GDK and queries XWayland directly, giving the real
# screen-absolute position regardless of where the layer-shell surface sits.
# ---------------------------------------------------------------------------
class _X11Ptr:
    def __init__(self):
        self._dpy = None
        self._root = None
        try:
            _xlib = _ct.cdll.LoadLibrary("libX11.so.6")
            _xlib.XOpenDisplay.restype  = _ct.c_void_p
            _xlib.XOpenDisplay.argtypes = [_ct.c_char_p]
            _xlib.XDefaultRootWindow.restype  = _ct.c_ulong
            _xlib.XDefaultRootWindow.argtypes = [_ct.c_void_p]
            _xlib.XQueryPointer.restype  = _ct.c_int
            dpy = _xlib.XOpenDisplay(None)
            if dpy:
                self._xlib = _xlib
                self._dpy  = _ct.c_void_p(dpy)
                self._root = _xlib.XDefaultRootWindow(self._dpy)
        except Exception:
            pass

    def get_pos(self):
        if not self._dpy:
            return None, None
        try:
            rx, ry = _ct.c_int(), _ct.c_int()
            wx, wy = _ct.c_int(), _ct.c_int()
            mask   = _ct.c_uint()
            r_ret  = _ct.c_ulong()
            child  = _ct.c_ulong()
            self._xlib.XQueryPointer(
                self._dpy, self._root,
                _ct.byref(r_ret), _ct.byref(child),
                _ct.byref(rx), _ct.byref(ry),
                _ct.byref(wx), _ct.byref(wy),
                _ct.byref(mask))
            return rx.value, ry.value
        except Exception:
            return None, None

_x11ptr = _X11Ptr()
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
from gi.repository import Gtk, Gdk, GdkPixbuf, GtkLayerShell, GLib

class _Thumb(Gtk.Window):
    def __init__(self):
        super().__init__()
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        self.set_decorated(False)

        # Overlay: image + character name label
        self._ov = Gtk.Overlay()
        self._img = Gtk.Image()
        self._ov.add(self._img)

        self._lbl = Gtk.Label()
        self._lbl.set_halign(Gtk.Align.CENTER)
        self._lbl.set_valign(Gtk.Align.START)
        self._lbl.set_margin_top(4)
        _lbl_css = Gtk.CssProvider()
        _lbl_css.load_from_data(
            b"label { background: rgba(0,0,0,0.7); color: white;"
            b"  padding: 4px 8px; border-radius: 3px; font-size: 11px; }")
        self._lbl.get_style_context().add_provider(
            _lbl_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._lbl.set_no_show_all(True)
        self._ov.add_overlay(self._lbl)

        self.add(self._ov)

        # Border drawn via Cairo.
        # IMPORTANT: connect_after so our draw runs AFTER children paint —
        # connect() fires before the default handler (SIGNAL_RUN_LAST) which
        # draws children, so a plain connect() puts the border UNDER the image.
        self._border_color = None
        self.connect_after("draw", self._draw_border)

        # Drag / click state
        self._mx = self._my = 0
        self._last_drag_x = self._last_drag_y = 0.0
        self._drag_dist = 0.0
        self._drag = False
        self._ctrl = False
        self._btn_down = False
        self._last_click_emit = 0.0   # debounce: ignore rapid repeat clicks

        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.BUTTON_RELEASE_MASK |
            Gdk.EventMask.POINTER_MOTION_MASK |
            Gdk.EventMask.ENTER_NOTIFY_MASK |
            Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.connect("button-press-event", self._on_press)
        self.connect("button-release-event", self._on_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)

    def set_pos(self, x, y):
        self._mx, self._my = x, y
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, x)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, y)

    def set_frame(self, w, h, rs, data):
        # new_from_bytes() wraps data in a GLib.Bytes that the pixbuf keeps
        # alive — unlike new_from_data(destroy_fn=None) which holds a raw
        # pointer that Python's GC can free while the pixbuf still uses it.
        try:
            pb = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(data),
                GdkPixbuf.Colorspace.RGB, True, 8, w, h, rs)
            self._img.set_from_pixbuf(pb)
        except Exception as e:
            sys.stderr.write(f"frame: {e}\n")

    def set_active(self, is_active, color):
        self._border_color = color if is_active else None
        self.queue_draw()

    def set_title(self, title):
        if title:
            self._lbl.set_markup(f"<b>{title}</b>")
            self._lbl.show()
        else:
            self._lbl.hide()

    def _draw_border(self, w, cr):
        if not self._border_color:
            return False
        try:
            h = self._border_color.lstrip('#')
            r, g, b = (int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))
        except Exception:
            return False
        alloc = w.get_allocation()
        cr.set_source_rgba(r, g, b, 1.0)
        cr.set_line_width(4)
        cr.rectangle(2, 2, alloc.width - 4, alloc.height - 4)
        cr.stroke()
        return False  # allow default rendering

    def _emit(self, msg):
        try:
            sys.stdout.write(msg + "\n")
            sys.stdout.flush()
        except Exception:
            pass

    def _on_press(self, w, ev):
        if ev.button in (1, 3):
            self._ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
            # Seed the relative-motion reference with the surface-local press
            # position.  All drag movement is tracked as deltas between
            # consecutive motion events — no absolute coordinates needed.
            self._last_drag_x = ev.x
            self._last_drag_y = ev.y
            self._drag_dist = 0.0
            self._drag = False
            self._btn_down = True
            return True
        return False

    def _on_motion(self, w, ev):
        if not self._btn_down:
            return False
        dx = ev.x - self._last_drag_x
        dy = ev.y - self._last_drag_y
        self._last_drag_x = ev.x
        self._last_drag_y = ev.y
        self._drag_dist += abs(dx) + abs(dy)
        if self._drag_dist > 4:
            self._drag = True
        if self._drag:
            nx = max(0, self._mx + int(dx))
            ny = max(0, self._my + int(dy))
            if nx != self._mx or ny != self._my:
                self.set_pos(nx, ny)
                self._emit(f"POS {nx} {ny}")
        return True

    def _on_release(self, w, ev):
        if ev.button in (1, 3):
            was_drag = self._drag
            if not was_drag and ev.button == 1:
                # Debounce: GTK generates PRESS+RELEASE twice for a double-click
                # event, which would send CLICK twice in rapid succession and
                # trigger multiple wmctrl/focus calls.  Suppress any CLICK that
                # arrives within 400 ms of the previous one.
                now = _time.monotonic()
                if now - self._last_click_emit >= 0.4:
                    self._last_click_emit = now
                    self._emit("CTRL_CLICK" if self._ctrl else "CLICK")
            self._drag = False
            self._btn_down = False
            # If drag just ended and cursor is still on window, fire ENTER
            # so the main process zoom debounce restarts cleanly.
            if was_drag:
                self._emit("ENTER")
            return True
        return False

    def _on_enter(self, w, ev):
        if self._btn_down:
            # The compositor sends LEAVE+ENTER when our set_pos() moves the
            # window under the cursor.  ENTER fires AFTER the compositor has
            # applied the new margin, so ev.x is already in the new surface-
            # local frame.  Re-anchor the drag reference here so the next
            # motion event's delta is relative to the correct origin.
            self._last_drag_x = ev.x
            self._last_drag_y = ev.y
            # Do NOT emit ENTER during a drag — prevents zoom thrash as the
            # window moves through LEAVE+ENTER cycles on every set_pos().
            return False
        self._emit("ENTER")
        return False

    def _on_leave(self, w, ev):
        if self._btn_down:
            return False   # suppress zoom-out during drag
        self._emit("LEAVE")
        return False

win = _Thumb()
win.show_all()

def _reader():
    for raw in sys.stdin:
        line = raw.rstrip("\n")
        if not line:
            continue
        parts = line.split(" ", 4)
        cmd = parts[0]
        try:
            if cmd == "FRAME" and len(parts) == 5:
                w, h, rs = int(parts[1]), int(parts[2]), int(parts[3])
                data = base64.b64decode(parts[4])
                GLib.idle_add(win.set_frame, w, h, rs, data)
            elif cmd == "POS" and len(parts) == 3:
                GLib.idle_add(win.set_pos, int(parts[1]), int(parts[2]))
            elif cmd == "SIZE" and len(parts) == 3:
                GLib.idle_add(win.resize, int(parts[1]), int(parts[2]))
            elif cmd == "ACTIVE" and len(parts) == 3:
                GLib.idle_add(win.set_active, parts[1] == "1", parts[2])
            elif cmd == "TITLE":
                GLib.idle_add(win.set_title, " ".join(parts[1:]))
            elif cmd == "SHOW":
                GLib.idle_add(win.show)
            elif cmd == "HIDE":
                GLib.idle_add(win.hide)
            elif cmd == "QUIT":
                GLib.idle_add(Gtk.main_quit)
                return
        except Exception as e:
            sys.stderr.write(f"helper [{cmd}]: {e}\n")
    GLib.idle_add(Gtk.main_quit)

threading.Thread(target=_reader, daemon=True).start()
Gtk.main()
"""

import base64 as _b64mod, subprocess as _subproc, threading as _threading
import queue as _queue_mod

class _LayerShellDisplay:
    """Manages a layer-shell subprocess OVERLAY window for one thumbnail."""

    def __init__(self, x, y, w, h, click_cb, ctrl_click_cb, pos_cb,
                 enter_cb=None, leave_cb=None):
        import sys as _sys
        self._x, self._y = x, y
        self._click_cb = click_cb
        self._ctrl_click_cb = ctrl_click_cb
        self._pos_cb = pos_cb
        self._enter_cb = enter_cb
        self._leave_cb = leave_cb
        # Two queues funnelled through one background writer thread so the
        # GTK main thread NEVER writes to stdin and can never block.
        #
        # _ctrl_queue: unlimited size — SIZE, POS, ACTIVE, TITLE, SHOW, HIDE,
        #   QUIT messages; always delivered in order, never dropped.
        # _frame_queue: size=1 — large FRAME payloads; stale frames are
        #   silently replaced so the subprocess always gets the newest image.
        self._ctrl_queue  = _queue_mod.Queue()          # unbounded
        self._frame_queue = _queue_mod.Queue(maxsize=1) # drop-old
        env = os.environ.copy()
        env["GDK_BACKEND"] = "wayland"
        self._proc = _subproc.Popen(
            [_sys.executable, "-c", _LAYER_SHELL_HELPER],
            stdin=_subproc.PIPE,
            stdout=_subproc.PIPE,
            stderr=None,   # inherit terminal so subprocess errors are visible
            env=env,
        )
        _threading.Thread(target=self._reader, daemon=True).start()
        _threading.Thread(target=self._writer, daemon=True).start()
        self._ctrl_send(f"SIZE {w} {h}")
        self._ctrl_send(f"POS {x} {y}")

    # ------------------------------------------------------------------
    # Internal helpers — only called from the background _writer thread
    # or at startup before concurrent access begins.

    def _ctrl_send(self, msg):
        """Enqueue a small control message (non-blocking, never dropped)."""
        self._ctrl_queue.put((msg + "\n").encode())

    def _writer(self):
        """Single background thread that owns all stdin writes.

        Drains all pending control messages before sending each frame, so
        that ACTIVE/POS/SIZE updates are never delayed behind a large FRAME
        payload.  When no frame is available the loop blocks briefly on the
        control queue so we don't spin-loop consuming CPU.
        """
        stdin = self._proc.stdin
        while True:
            try:
                # 1. Flush all queued control messages first (non-blocking).
                while True:
                    try:
                        data = self._ctrl_queue.get_nowait()
                        if data is None:
                            return   # QUIT sentinel
                        stdin.write(data)
                    except _queue_mod.Empty:
                        break

                # 2. Try to send one frame.
                try:
                    data = self._frame_queue.get_nowait()
                    stdin.write(data)
                    stdin.flush()
                    continue
                except _queue_mod.Empty:
                    pass

                # 3. Nothing to write — wait for next control message,
                #    flushing any partial writes first.
                try:
                    stdin.flush()
                except Exception:
                    pass
                try:
                    data = self._ctrl_queue.get(timeout=0.05)
                    if data is None:
                        return
                    stdin.write(data)
                except _queue_mod.Empty:
                    pass

            except Exception:
                break

    def _reader(self):
        try:
            for line_b in iter(self._proc.stdout.readline, b""):
                line = line_b.decode("utf-8", errors="replace").strip()
                if IPC_DEBUG and line and not line.startswith("FRAME"):
                    print(f"[ipc] {line}", flush=True)
                # CRITICAL: Use GLib.idle_add with GLib.PRIORITY_HIGH (-100)
                # instead of default idle priority (200).  With two clients the
                # capture timers (priority 0) consume most main-loop time, and
                # default-priority idle callbacks are starved indefinitely — this
                # is the root cause of click/zoom/hover death on second client.
                _P = GLib.PRIORITY_HIGH
                if line == "CLICK" and self._click_cb:
                    GLib.idle_add(self._click_cb, priority=_P)
                elif line == "CTRL_CLICK" and self._ctrl_click_cb:
                    GLib.idle_add(self._ctrl_click_cb, priority=_P)
                elif line.startswith("POS ") and self._pos_cb:
                    p = line.split()
                    if len(p) == 3:
                        GLib.idle_add(self._pos_cb, int(p[1]), int(p[2]), priority=_P)
                elif line == "ENTER" and self._enter_cb:
                    GLib.idle_add(self._enter_cb, priority=_P)
                elif line == "LEAVE" and self._leave_cb:
                    GLib.idle_add(self._leave_cb, priority=_P)
        except Exception:
            pass

    def send_frame(self, pixbuf):
        if not pixbuf:
            return
        if not pixbuf.get_has_alpha():
            pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
        w, h, rs = pixbuf.get_width(), pixbuf.get_height(), pixbuf.get_rowstride()
        b64 = _b64mod.b64encode(bytes(pixbuf.get_pixels())).decode("ascii")
        data = f"FRAME {w} {h} {rs} {b64}\n".encode()
        # Replace stale frame with the newest one; never block.
        try:
            self._frame_queue.put_nowait(data)
        except _queue_mod.Full:
            try:
                self._frame_queue.get_nowait()
            except _queue_mod.Empty:
                pass
            try:
                self._frame_queue.put_nowait(data)
            except _queue_mod.Full:
                pass

    def set_pos(self, x, y):
        self._x, self._y = x, y
        self._ctrl_send(f"POS {x} {y}")

    def set_size(self, w, h):
        self._ctrl_send(f"SIZE {w} {h}")

    def send_active(self, is_active, color):
        self._ctrl_send(f"ACTIVE {'1' if is_active else '0'} {color}")

    def send_title(self, title):
        self._ctrl_send(f"TITLE {title}")

    def show(self):
        self._ctrl_send("SHOW")

    def hide(self):
        self._ctrl_send("HIDE")

    def destroy(self):
        self._ctrl_send("QUIT")
        try:
            self._proc.stdin.close()
        except Exception:
            pass

