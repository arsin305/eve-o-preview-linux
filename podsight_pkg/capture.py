"""Server-side window capture: XComposite + XRender via ctypes.

Replaces the legacy per-frame path (full-window XGetImage + CPU bilinear
scale, ~8 MB transferred per client per frame) with:

  1. XCompositeNameWindowPixmap  — grab a reference to the window's
     offscreen backing pixmap (server-side, no pixel copy)
  2. XRenderComposite with a scale transform — the X server scales the
     window down to thumbnail size using its own (usually GPU) filter
  3. XGetImage on the small result — only ~250 KB crosses the socket

Falls back cleanly: if the extensions are missing or any step fails,
grab_scaled() returns None and the caller uses the legacy Gdk path.

All functions run on the GLib main loop thread; the module keeps its own
X display connection with a non-fatal error handler so a vanished window
mid-frame cannot kill the process.
"""
import ctypes
import ctypes.util

from . import platform  # noqa: F401 — env/gi setup must run first
from gi.repository import GdkPixbuf, GLib

# ---------------------------------------------------------------------------
# ctypes declarations
# ---------------------------------------------------------------------------
_Display_p = ctypes.c_void_p
_Window = ctypes.c_ulong
_Pixmap = ctypes.c_ulong
_Picture = ctypes.c_ulong

_ZPixmap = 2
_AllPlanes = ctypes.c_ulong(0xFFFFFFFFFFFFFFFF)
_LSBFirst = 0
_PictOpSrc = 1
_CompositeRedirectAutomatic = 0


class _XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int), ("y", ctypes.c_int),
        ("width", ctypes.c_int), ("height", ctypes.c_int),
        ("border_width", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p),
        ("root", _Window),
        ("c_class", ctypes.c_int),
        ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int),
        ("colormap", ctypes.c_ulong),
        ("map_installed", ctypes.c_int),
        ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long),
        ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int),
        ("screen", ctypes.c_void_p),
    ]


class _XImage(ctypes.Structure):
    pass


_DestroyImageFunc = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(_XImage))


class _XImageFuncs(ctypes.Structure):
    _fields_ = [
        ("create_image", ctypes.c_void_p),
        ("destroy_image", _DestroyImageFunc),
        ("get_pixel", ctypes.c_void_p),
        ("put_pixel", ctypes.c_void_p),
        ("sub_image", ctypes.c_void_p),
        ("add_pixel", ctypes.c_void_p),
    ]


_XImage._fields_ = [
    ("width", ctypes.c_int), ("height", ctypes.c_int),
    ("xoffset", ctypes.c_int), ("format", ctypes.c_int),
    ("data", ctypes.c_void_p),
    ("byte_order", ctypes.c_int),
    ("bitmap_unit", ctypes.c_int),
    ("bitmap_bit_order", ctypes.c_int),
    ("bitmap_pad", ctypes.c_int),
    ("depth", ctypes.c_int),
    ("bytes_per_line", ctypes.c_int),
    ("bits_per_pixel", ctypes.c_int),
    ("red_mask", ctypes.c_ulong),
    ("green_mask", ctypes.c_ulong),
    ("blue_mask", ctypes.c_ulong),
    ("obdata", ctypes.c_void_p),
    ("f", _XImageFuncs),
]


class _XTransform(ctypes.Structure):
    # 3x3 matrix of XFixed (16.16 fixed point)
    _fields_ = [("matrix", (ctypes.c_int * 3) * 3)]


def _fixed(v):
    """Convert a float to XFixed 16.16."""
    return int(v * 65536.0)


# ---------------------------------------------------------------------------
# Library / display initialization
# ---------------------------------------------------------------------------
AVAILABLE = False
_dpy = None
_x11 = None
_xcomp = None
_xrender = None
_err_flag = [False]          # set by the X error handler during a grab
_error_handler_ref = None    # keep the CFUNCTYPE object alive (GC guard)


def _load(name):
    path = ctypes.util.find_library(name)
    return ctypes.CDLL(path) if path else None


def _init():
    """Open our own display connection and bind the extensions. Runs once."""
    global AVAILABLE, _dpy, _x11, _xcomp, _xrender, _error_handler_ref

    _x11 = _load("X11")
    _xcomp = _load("Xcomposite")
    _xrender = _load("Xrender")
    if not (_x11 and _xcomp and _xrender):
        print("[podsight] XComposite/XRender libraries not found — using legacy capture.")
        return

    x = _x11
    x.XOpenDisplay.restype = _Display_p
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XGetWindowAttributes.restype = ctypes.c_int
    x.XGetWindowAttributes.argtypes = [_Display_p, _Window,
                                       ctypes.POINTER(_XWindowAttributes)]
    x.XCreatePixmap.restype = _Pixmap
    x.XCreatePixmap.argtypes = [_Display_p, _Window,
                                ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
    x.XFreePixmap.argtypes = [_Display_p, _Pixmap]
    x.XGetImage.restype = ctypes.POINTER(_XImage)
    x.XGetImage.argtypes = [_Display_p, _Pixmap, ctypes.c_int, ctypes.c_int,
                            ctypes.c_uint, ctypes.c_uint,
                            ctypes.c_ulong, ctypes.c_int]
    x.XDefaultRootWindow.restype = _Window
    x.XDefaultRootWindow.argtypes = [_Display_p]
    x.XSync.argtypes = [_Display_p, ctypes.c_int]

    c = _xcomp
    c.XCompositeQueryExtension.restype = ctypes.c_int
    c.XCompositeQueryExtension.argtypes = [_Display_p,
                                           ctypes.POINTER(ctypes.c_int),
                                           ctypes.POINTER(ctypes.c_int)]
    c.XCompositeRedirectWindow.argtypes = [_Display_p, _Window, ctypes.c_int]
    c.XCompositeNameWindowPixmap.restype = _Pixmap
    c.XCompositeNameWindowPixmap.argtypes = [_Display_p, _Window]

    r = _xrender
    r.XRenderQueryExtension.restype = ctypes.c_int
    r.XRenderQueryExtension.argtypes = [_Display_p,
                                        ctypes.POINTER(ctypes.c_int),
                                        ctypes.POINTER(ctypes.c_int)]
    r.XRenderFindVisualFormat.restype = ctypes.c_void_p
    r.XRenderFindVisualFormat.argtypes = [_Display_p, ctypes.c_void_p]
    r.XRenderFindStandardFormat.restype = ctypes.c_void_p
    r.XRenderFindStandardFormat.argtypes = [_Display_p, ctypes.c_int]
    r.XRenderCreatePicture.restype = _Picture
    r.XRenderCreatePicture.argtypes = [_Display_p, _Pixmap, ctypes.c_void_p,
                                       ctypes.c_ulong, ctypes.c_void_p]
    r.XRenderFreePicture.argtypes = [_Display_p, _Picture]
    r.XRenderSetPictureTransform.argtypes = [_Display_p, _Picture,
                                             ctypes.POINTER(_XTransform)]
    r.XRenderSetPictureFilter.argtypes = [_Display_p, _Picture,
                                          ctypes.c_char_p,
                                          ctypes.c_void_p, ctypes.c_int]
    r.XRenderComposite.argtypes = [_Display_p, ctypes.c_int,
                                   _Picture, _Picture, _Picture,
                                   ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int,
                                   ctypes.c_uint, ctypes.c_uint]

    _dpy = x.XOpenDisplay(None)
    if not _dpy:
        print("[podsight] capture: could not open X display — using legacy capture.")
        return

    # Non-fatal error handler: a window can vanish between our request and
    # the server processing it (BadWindow/BadDrawable). Flag it, never crash.
    @ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    def _on_x_error(_d, _e):
        _err_flag[0] = True
        return 0
    _error_handler_ref = _on_x_error   # prevent garbage collection
    x.XSetErrorHandler(_on_x_error)

    eb, rb = ctypes.c_int(), ctypes.c_int()
    if not c.XCompositeQueryExtension(_dpy, ctypes.byref(eb), ctypes.byref(rb)):
        print("[podsight] XComposite extension missing — using legacy capture.")
        return
    if not r.XRenderQueryExtension(_dpy, ctypes.byref(eb), ctypes.byref(rb)):
        print("[podsight] XRender extension missing — using legacy capture.")
        return

    AVAILABLE = True
    print("[podsight] Server-side capture active (XComposite + XRender).")


# ---------------------------------------------------------------------------
# Per-XID state caches
# ---------------------------------------------------------------------------
_redirected = set()          # XIDs we have redirected
_fmt_cache = {}              # xid -> XRenderPictFormat* for its visual
_dst_cache = {}              # (dst_w, dst_h) -> (dest Pixmap, dest Picture)


def _forget(xid):
    """Drop cached state for a window that failed (likely destroyed)."""
    _redirected.discard(xid)
    _fmt_cache.pop(xid, None)


def _get_dst(dst_w, dst_h):
    """Get (or create) the persistent destination pixmap+picture for a size."""
    key = (dst_w, dst_h)
    hit = _dst_cache.get(key)
    if hit:
        return hit
    root = _x11.XDefaultRootWindow(_dpy)
    pm = _x11.XCreatePixmap(_dpy, root, dst_w, dst_h, 24)
    fmt = _xrender.XRenderFindStandardFormat(_dpy, 1)  # PictStandardRGB24
    pic = _xrender.XRenderCreatePicture(_dpy, pm, fmt, 0, None)
    _dst_cache[key] = (pm, pic)
    return pm, pic


def _ximage_to_pixbuf(img_p):
    """Convert a small 32bpp ZPixmap XImage to a GdkPixbuf (RGB, no alpha)."""
    img = img_p.contents
    w, h, stride = img.width, img.height, img.bytes_per_line
    if img.bits_per_pixel != 32 or img.byte_order != _LSBFirst:
        return None
    raw = ctypes.string_at(img.data, stride * h)
    if stride != w * 4:  # uncommon; normalize row length
        raw = b"".join(raw[y * stride: y * stride + w * 4] for y in range(h))
    # Channel positions. XGetImage on a *pixmap* reports zero masks (masks
    # come from a visual, which pixmaps lack). Our destination is always
    # PictStandardRGB24 (x8r8g8b8), which on a little-endian server is BGRX
    # in memory — so that is the default; nonzero masks override it.
    if img.red_mask == 0x000000FF:
        ri, gi, bi = 0, 1, 2
    else:  # 0x00FF0000 or 0 (pixmap): x8r8g8b8 little-endian = B,G,R,X
        ri, gi, bi = 2, 1, 0
    out = bytearray(w * h * 3)
    out[0::3] = raw[ri::4]   # slice assignment runs at C speed
    out[1::3] = raw[gi::4]
    out[2::3] = raw[bi::4]
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(bytes(out)), GdkPixbuf.Colorspace.RGB,
        False, 8, w, h, w * 3)


def grab_scaled(xid, src_w, src_h, dst_w, dst_h):
    """Capture window `xid` scaled to dst_w x dst_h. Returns GdkPixbuf or None.

    None means "this frame failed" — callers fall back to the legacy path
    (which also drives the existing Wine child-XID rebinding logic).
    """
    if not AVAILABLE or src_w <= 0 or src_h <= 0 or dst_w <= 0 or dst_h <= 0:
        return None
    src_pic = 0
    named_pm = 0
    img_p = None
    try:
        _err_flag[0] = False

        if xid not in _redirected:
            _xcomp.XCompositeRedirectWindow(_dpy, xid, _CompositeRedirectAutomatic)
            _redirected.add(xid)

        fmt = _fmt_cache.get(xid)
        if fmt is None:
            attrs = _XWindowAttributes()
            if not _x11.XGetWindowAttributes(_dpy, xid, ctypes.byref(attrs)):
                _forget(xid)
                return None
            fmt = _xrender.XRenderFindVisualFormat(_dpy, attrs.visual)
            if not fmt:
                return None
            _fmt_cache[xid] = fmt

        named_pm = _xcomp.XCompositeNameWindowPixmap(_dpy, xid)
        if not named_pm:
            _forget(xid)
            return None

        src_pic = _xrender.XRenderCreatePicture(_dpy, named_pm, fmt, 0, None)

        # Transform maps destination coords back to source: scale factors
        # are src/dst in 16.16 fixed point.
        t = _XTransform()
        t.matrix[0][0] = _fixed(src_w / dst_w)
        t.matrix[1][1] = _fixed(src_h / dst_h)
        t.matrix[2][2] = _fixed(1.0)
        _xrender.XRenderSetPictureTransform(_dpy, src_pic, ctypes.byref(t))
        _xrender.XRenderSetPictureFilter(_dpy, src_pic, b"good", None, 0)

        dst_pm, dst_pic = _get_dst(dst_w, dst_h)
        _xrender.XRenderComposite(_dpy, _PictOpSrc, src_pic, 0, dst_pic,
                                  0, 0, 0, 0, 0, 0, dst_w, dst_h)

        # XGetImage is the only round trip — and only thumbnail-sized.
        img_p = _x11.XGetImage(_dpy, dst_pm, 0, 0, dst_w, dst_h,
                               _AllPlanes, _ZPixmap)
        if _err_flag[0]:
            _forget(xid)
            return None
        if not img_p:
            return None
        return _ximage_to_pixbuf(img_p)
    except Exception as e:
        print(f"[podsight] capture error for xid=0x{xid:x}: {e}")
        _forget(xid)
        return None
    finally:
        # Free per-frame server resources; the dest pixmap/picture persist.
        if img_p:
            try:
                img_p.contents.f.destroy_image(img_p)
            except Exception:
                pass
        if src_pic:
            _xrender.XRenderFreePicture(_dpy, src_pic)
        if named_pm:
            _x11.XFreePixmap(_dpy, named_pm)


_init()
