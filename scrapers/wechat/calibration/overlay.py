# scrapers/wechat/calibration/overlay.py
"""Transparent Win32 overlay window for calibration visual guidance.

Draws a crosshair (point steps) or rectangle preview (region steps)
on top of all other windows using a layered window with color-keyed
transparency.  The overlay is click-through (WS_EX_TRANSPARENT) so
it never interferes with mouse interaction.

Architecture:
  - A single WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST
    window covers the entire virtual screen.
  - A background thread runs a 20 FPS GDI draw loop, reading
    shared state (mouse position, step info, recommendation coords)
    via a threading.Lock.
  - The main calibration thread updates the shared state; the draw
    thread picks it up on the next frame.

Color key: RGB(1, 0, 1) → transparent.  All drawn elements use
different colors so they are visible against any background.
"""

import ctypes
import ctypes.wintypes as wintypes
import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Win32 constants ─────────────────────────────────────────────────

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000

LWA_COLORKEY = 0x00000001
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00

SW_SHOW = 5
SW_HIDE = 0

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

TRANSPARENT_COLOR = 0x010001  # RGB(1, 0, 1) — unlikely to appear naturally

# GDI constants
PS_SOLID = 0
PS_DASH = 1

# Virtual key codes for F5 (used by the recommendation accept flow)
VK_F5 = 0x74

# ── Win32 function prototypes ──────────────────────────────────────

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32
_kernel32 = ctypes.windll.kernel32


def _err(msg: str) -> str:
    code = _kernel32.GetLastError()
    return f"{msg} (GetLastError={code})"


# ── Shared state ───────────────────────────────────────────────────


@dataclass
class OverlayState:
    """Thread-safe shared state between calibrator and draw thread."""

    visible: bool = False
    mode: str = "point"           # "point" | "region_first" | "region_second" | "hidden"

    # Mouse position (screen absolute)
    mouse_x: int = 0
    mouse_y: int = 0

    # Recommendation point / region (screen absolute, may be None)
    rec_x: int | None = None
    rec_y: int | None = None
    rec_w: int | None = None
    rec_h: int | None = None

    # Region first corner (only in region mode after F8 press)
    corner1_x: int | None = None
    corner1_y: int | None = None

    # Text label
    label: str = ""

    # Screen dimensions
    screen_x: int = 0
    screen_y: int = 0
    screen_w: int = 1920
    screen_h: int = 1080

    lock: threading.Lock = field(default_factory=threading.Lock)


# ── GDI helper functions ────────────────────────────────────────────

def _draw_crosshair(hdc: int, cx: int, cy: int, screen_w: int, screen_h: int) -> None:
    """Draw full-screen crosshair lines centered at (cx, cy)."""
    # Horizontal line
    _gdi32.MoveToEx(hdc, 0, cy, None)
    _gdi32.LineTo(hdc, screen_w, cy)
    # Vertical line
    _gdi32.MoveToEx(hdc, cx, 0, None)
    _gdi32.LineTo(hdc, cx, screen_h)
    # Small circle at center
    r = 20
    _gdi32.Ellipse(hdc, cx - r, cy - r, cx + r, cy + r)


def _draw_rect_preview(
    hdc: int,
    x1: int, y1: int,
    x2: int, y2: int,
) -> None:
    """Draw a filled rectangle from corner (x1,y1) to (x2,y2)."""
    left = min(x1, x2)
    top = min(y1, y2)
    right = max(x1, x2)
    bottom = max(y1, y2)
    _gdi32.Rectangle(hdc, left, top, right, bottom)


def _draw_recommendation_dot(
    hdc: int, cx: int, cy: int, frame: int,
) -> None:
    """Draw a pulsing yellow dot at the recommended position.

    ``frame`` is a monotonic counter used to pulse the dot size.
    """
    import math
    pulse = abs(math.sin(frame * 0.1))
    r = int(12 + 8 * pulse)  # 12–20 px radius
    _gdi32.Ellipse(hdc, cx - r, cy - r, cx + r, cy + r)


def _draw_text_label(
    hdc: int, text: str, screen_w: int, screen_h: int,
) -> None:
    """Draw a text label bar at the top of the screen."""
    if not text:
        return
    padding = 12
    bar_h = 36
    # Semi-transparent dark bar at top
    _gdi32.Rectangle(hdc, 0, 0, screen_w, bar_h)
    # Text
    _gdi32.TextOutW(hdc, padding, 8, text, len(text))


# ── Overlay class ───────────────────────────────────────────────────


class CalibrationOverlay:
    """Transparent screen overlay for calibration visual guidance.

    Usage::

        overlay = CalibrationOverlay()
        overlay.start()

        # Point mode — show crosshair + recommended dot
        overlay.show_point_mode(
            mouse_x, mouse_y,
            rec_x, rec_y,
            label="[3/8] 公众号 - 评论区图标"
        )

        # Region mode — first corner recorded, preview rectangle
        overlay.show_region_second(
            corner1_x, corner1_y,
            mouse_x, mouse_y,
            rec_x, rec_y, rec_w, rec_h,
            label="[4/8] 右下角 → F8"
        )

        overlay.stop()
    """

    def __init__(self):
        self._state = OverlayState()
        self._hwnd: int | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._wnd_class_atom: int | None = None
        self._frame = 0

    # ── Public API ──────────────────────────────────────────────

    def start(self) -> bool:
        """Create the overlay window and start the draw thread.

        Returns False if overlay creation fails (e.g. headless /
        no display), in which case the caller should proceed in
        text-only mode.
        """
        if self._running:
            return True

        try:
            self._create_window()
        except Exception as e:
            logger.warning(f"Overlay creation failed: {e}")
            logger.warning("Calibration will continue in text-only mode.")
            return False

        self._running = True
        self._thread = threading.Thread(
            target=self._draw_loop,
            name="calibration-overlay",
            daemon=True,
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """Hide the overlay and stop the draw thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._destroy_window()
        self._hwnd = None
        self._wnd_class_atom = None

    def show_point_mode(
        self,
        mouse_x: int,
        mouse_y: int,
        rec_x: int | None = None,
        rec_y: int | None = None,
        label: str = "",
    ) -> None:
        """Show crosshair overlay for a point calibration step.

        ``rec_x``, ``rec_y``: recommended position in screen coords
        (drawn as a pulsing yellow dot).  Pass None to hide.
        """
        with self._state.lock:
            self._state.visible = True
            self._state.mode = "point"
            self._state.mouse_x = mouse_x
            self._state.mouse_y = mouse_y
            self._state.rec_x = rec_x
            self._state.rec_y = rec_y
            self._state.rec_w = None
            self._state.rec_h = None
            self._state.corner1_x = None
            self._state.corner1_y = None
            self._state.label = label

    def show_region_first(
        self,
        mouse_x: int,
        mouse_y: int,
        rec_x: int | None = None,
        rec_y: int | None = None,
        rec_w: int | None = None,
        rec_h: int | None = None,
        label: str = "",
    ) -> None:
        """Show overlay for region step — first corner not yet recorded."""
        with self._state.lock:
            self._state.visible = True
            self._state.mode = "region_first"
            self._state.mouse_x = mouse_x
            self._state.mouse_y = mouse_y
            self._state.rec_x = rec_x
            self._state.rec_y = rec_y
            self._state.rec_w = rec_w
            self._state.rec_h = rec_h
            self._state.corner1_x = None
            self._state.corner1_y = None
            self._state.label = label

    def show_region_second(
        self,
        corner1_x: int,
        corner1_y: int,
        mouse_x: int,
        mouse_y: int,
        rec_x: int | None = None,
        rec_y: int | None = None,
        rec_w: int | None = None,
        rec_h: int | None = None,
        label: str = "",
    ) -> None:
        """Show overlay for region step — first corner recorded,
        preview rectangle from corner1 to current mouse position.
        """
        with self._state.lock:
            self._state.visible = True
            self._state.mode = "region_second"
            self._state.corner1_x = corner1_x
            self._state.corner1_y = corner1_y
            self._state.mouse_x = mouse_x
            self._state.mouse_y = mouse_y
            self._state.rec_x = rec_x
            self._state.rec_y = rec_y
            self._state.rec_w = rec_w
            self._state.rec_h = rec_h
            self._state.label = label

    def hide(self) -> None:
        """Hide the overlay (without destroying the window)."""
        with self._state.lock:
            self._state.visible = False

    # ── Internal: window creation ───────────────────────────────

    def _create_window(self) -> None:
        """Create the layered overlay window covering the virtual screen."""
        # Get virtual screen dimensions (all monitors)
        screen_x = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        screen_y = _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        screen_w = _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        screen_h = _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)

        with self._state.lock:
            self._state.screen_x = screen_x
            self._state.screen_y = screen_y
            self._state.screen_w = screen_w
            self._state.screen_h = screen_h

        hinst = _kernel32.GetModuleHandleW(None)

        # Window class
        class_name = "ClaudeCalibrationOverlay"
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_long, ctypes.c_uint,
            ctypes.c_long, ctypes.c_long,
        )

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == 0x0084:  # WM_NCHITTEST
                return -1       # HTTRANSPARENT — click through
            if msg == 0x0002:  # WM_DESTROY
                return 0
            if msg == 0x000F:  # WM_PAINT
                # We handle drawing ourselves in the thread loop
                return 0
            if msg == 0x0113:  # WM_TIMER
                # Timer-driven redraw handled in draw thread
                return 0
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_ref = WNDPROC(_wnd_proc)

        wnd_class = ctypes.create_string_buffer(56)  # WNDCLASSEXW size
        ctypes.memmove(wnd_class, ctypes.c_int(56), 4)  # cbSize
        ctypes.memmove(
            wnd_class[12:],
            ctypes.c_void_p(0x0003),  # CS_HREDRAW | CS_VREDRAW
            4,
        )
        ctypes.memmove(wnd_class[16:], ctypes.c_void_p(0), 8)  # lpfnWndProc placeholder

        # Use a simpler approach: RegisterClassExW via ctypes struct
        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("style", ctypes.c_uint),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p),
                ("hIconSm", ctypes.c_void_p),
            ]

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0x0003  # CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = ctypes.cast(self._wnd_proc_ref, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.hCursor = _user32.LoadCursorW(None, 32512)  # IDC_ARROW
        # Background brush: the transparent color key
        wc.hbrBackground = _gdi32.CreateSolidBrush(TRANSPARENT_COLOR)
        wc.lpszClassName = class_name

        atom = _user32.RegisterClassExW(ctypes.byref(wc))
        if not atom:
            raise OSError(_err("RegisterClassExW failed"))
        self._wnd_class_atom = atom

        # Create the layered, transparent, topmost popup window
        ex_style = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
        hwnd = _user32.CreateWindowExW(
            ex_style,
            class_name,
            "",
            WS_POPUP,
            screen_x, screen_y,
            screen_w, screen_h,
            None,   # hWndParent
            None,   # hMenu
            hinst,
            None,   # lpParam
        )
        if not hwnd:
            raise OSError(_err("CreateWindowExW failed"))

        # Make the TRANSPARENT_COLOR pixels fully transparent
        _user32.SetLayeredWindowAttributes(
            hwnd, TRANSPARENT_COLOR, 0, LWA_COLORKEY,
        )

        # Show but don't activate (don't steal focus)
        _user32.ShowWindow(hwnd, 8)  # SW_SHOWNA

        self._hwnd = hwnd
        logger.debug(
            f"Overlay window created: hwnd={hwnd:#x}, "
            f"screen=({screen_x},{screen_y}) {screen_w}x{screen_h}"
        )

    def _destroy_window(self) -> None:
        """Destroy the overlay window and unregister the class."""
        hwnd = self._hwnd
        if hwnd:
            _user32.DestroyWindow(hwnd)
        atom = self._wnd_class_atom
        if atom:
            hinst = _kernel32.GetModuleHandleW(None)
            _user32.UnregisterClassW(atom, hinst)

    # ── Internal: draw loop ─────────────────────────────────────

    def _draw_loop(self) -> None:
        """Background thread: poll state and redraw at ~20 FPS."""
        # Pre-create GDI objects (reused each frame)
        green_pen = _gdi32.CreatePen(PS_SOLID, 1, 0x0000FF00)   # BGR green
        yellow_pen = _gdi32.CreatePen(PS_SOLID, 2, 0x0000FFFF)  # BGR yellow
        blue_pen = _gdi32.CreatePen(PS_SOLID, 2, 0x00FF0000)    # BGR blue
        blue_brush = _gdi32.CreateSolidBrush(0x00FF0000)          # BGR blue
        yellow_brush = _gdi32.CreateSolidBrush(0x0000FFFF)        # BGR yellow
        bg_brush = _gdi32.CreateSolidBrush(TRANSPARENT_COLOR)
        bar_brush = _gdi32.CreateSolidBrush(0x00333333)           # BGR dark gray

        while self._running:
            # Read shared state
            with self._state.lock:
                if not self._state.visible or not self._hwnd:
                    # Hide window when not needed
                    pass
                visible = self._state.visible
                mode = self._state.mode
                mx = self._state.mouse_x
                my = self._state.mouse_y
                rec_x = self._state.rec_x
                rec_y = self._state.rec_y
                rec_w = self._state.rec_w
                rec_h = self._state.rec_h
                cx1 = self._state.corner1_x
                cy1 = self._state.corner1_y
                label = self._state.label
                screen_x = self._state.screen_x
                screen_y = self._state.screen_y
                screen_w = self._state.screen_w
                screen_h = self._state.screen_h

            hwnd = self._hwnd
            if not hwnd:
                break

            if visible:
                # Show window if hidden
                _user32.ShowWindow(hwnd, 8)  # SW_SHOWNA

                # Get DC and draw
                hdc = _user32.GetDC(hwnd)
                if hdc:
                    try:
                        # Fill background with transparent color key
                        _gdi32.SelectObject(hdc, bg_brush)
                        _gdi32.SelectObject(hdc, green_pen)

                        # Adjust coordinates to be relative to overlay window
                        ox = -screen_x
                        oy = -screen_y

                        if mode == "point":
                            _draw_crosshair(
                                hdc,
                                mx + ox, my + oy,
                                screen_w, screen_h,
                            )

                        elif mode == "region_first":
                            # Just a crosshair at mouse position
                            _draw_crosshair(
                                hdc,
                                mx + ox, my + oy,
                                screen_w, screen_h,
                            )

                        elif mode == "region_second" and cx1 is not None and cy1 is not None:
                            # Draw crosshair at corner1
                            _gdi32.SelectObject(hdc, blue_pen)
                            r = 8
                            _gdi32.MoveToEx(hdc, cx1 + ox - r, cy1 + oy, None)
                            _gdi32.LineTo(hdc, cx1 + ox + r, cy1 + oy)
                            _gdi32.MoveToEx(hdc, cx1 + ox, cy1 + oy - r, None)
                            _gdi32.LineTo(hdc, cx1 + ox, cy1 + oy + r)

                            # Draw preview rectangle from corner1 to mouse
                            _gdi32.SelectObject(hdc, blue_pen)
                            _gdi32.SelectObject(hdc, blue_brush)
                            _draw_rect_preview(
                                hdc,
                                cx1 + ox, cy1 + oy,
                                mx + ox, my + oy,
                            )

                            # Crosshair at current mouse
                            _gdi32.SelectObject(hdc, green_pen)
                            _draw_crosshair(
                                hdc,
                                mx + ox, my + oy,
                                screen_w, screen_h,
                            )

                        # ── Recommended position indicator ──
                        if mode == "region_second" and rec_x is not None and rec_y is not None \
                                and rec_w is not None and rec_h is not None:
                            # Draw recommended region as dashed outline
                            _gdi32.SelectObject(hdc, yellow_pen)
                            _gdi32.SelectObject(hdc, yellow_brush)
                            # Just the outline (use NULL brush for hollow)
                            null_brush = _gdi32.GetStockObject(5)  # NULL_BRUSH
                            _gdi32.SelectObject(hdc, null_brush)
                            _draw_rect_preview(
                                hdc,
                                rec_x + ox, rec_y + oy,
                                rec_x + rec_w + ox, rec_y + rec_h + oy,
                            )
                        elif rec_x is not None and rec_y is not None:
                            # Draw pulsing yellow dot at recommended position
                            _gdi32.SelectObject(hdc, yellow_pen)
                            _gdi32.SelectObject(hdc, yellow_brush)
                            _draw_recommendation_dot(
                                hdc,
                                rec_x + ox, rec_y + oy,
                                self._frame,
                            )

                        # ── Label bar ──
                        if label:
                            _gdi32.SelectObject(hdc, bar_brush)
                            _gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
                            _gdi32.SetTextColor(hdc, 0x00FFFFFF)  # white
                            _draw_text_label(hdc, label, screen_w, screen_h)

                    finally:
                        _user32.ReleaseDC(hwnd, hdc)
            else:
                _user32.ShowWindow(hwnd, SW_HIDE)

            self._frame += 1
            # ~20 FPS
            _kernel32.Sleep(50)

        # Cleanup GDI objects
        for obj in [green_pen, yellow_pen, blue_pen, blue_brush, yellow_brush, bg_brush, bar_brush]:
            _gdi32.DeleteObject(obj)
