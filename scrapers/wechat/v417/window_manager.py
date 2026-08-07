# scrapers/wechat/v417/window_manager.py
"""WeChat 4.1.7 dual-window manager.

4.1.7 opens article/video content in a SEPARATE popup window (class
``Chrome_WidgetWin_0``), not as a side panel within the main window.
This manager handles discovery, activation, and rect query for both.
"""
import ctypes
import ctypes.wintypes
import logging
import subprocess
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Win32 constants
SW_RESTORE = 9
GW_OWNER = 4

# Minimum plausible size
_MIN_MAIN_W = 400
_MIN_MAIN_H = 300
_MIN_POPUP_W = 300
_MIN_POPUP_H = 400

user32 = ctypes.windll.user32


@dataclass
class WindowRect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


class WechatWindowManagerV417:
    """Manage WeChat 4.1.7 main window + article/video popup window."""

    def __init__(self):
        self._main_hwnd: int | None = None
        self._popup_hwnd: int | None = None

    # ── Public API ─────────────────────────────────────────────────

    @property
    def is_found(self) -> bool:
        return self._main_hwnd is not None

    @property
    def has_popup(self) -> bool:
        return self._popup_hwnd is not None

    def find_main(self) -> bool:
        """Find the WeChat main window (Qt class, title '微信').

        Returns True if found.
        """
        # Strategy 1: By class name (Qt-based main window)
        hwnd = user32.FindWindowW("Qt51514QWindowIcon", None)
        if hwnd and self._accept_main(hwnd):
            self._main_hwnd = hwnd
            logger.info(
                f"[window-v417] Main window: "
                f"{self.get_main_rect().width}x{self.get_main_rect().height}"
            )
            return True

        self._main_hwnd = None
        logger.warning("[window-v417] Main window not found")
        return False

    def wait_for_popup(self, timeout: float = 15.0) -> bool:
        """Poll until an article/video popup window appears.

        The popup is a ``Chrome_WidgetWin_0`` window with title '微信'
        that is NOT the main window.
        """
        logger.info("[window-v417] Waiting for article/video popup...")
        deadline = time.time() + timeout

        while time.time() < deadline:
            hwnd = self._find_popup()
            if hwnd:
                self._popup_hwnd = hwnd
                rect = self.get_popup_rect()
                logger.info(
                    f"[window-v417] Popup appeared: "
                    f"({rect.left},{rect.top}) {rect.width}x{rect.height}"
                )
                return True
            time.sleep(0.5)

        logger.warning(
            f"[window-v417] Popup not found after {timeout}s"
        )
        return False

    def activate(self) -> bool:
        """Bring main window to foreground."""
        return self._activate(self._main_hwnd)

    def activate_popup(self) -> bool:
        """Bring popup window to foreground."""
        return self._activate(self._popup_hwnd)

    def get_main_rect(self) -> WindowRect:
        """Get main window rect. Raises if not found."""
        if not self._main_hwnd:
            raise RuntimeError("Main window not found")
        return self._get_rect(self._main_hwnd)

    def get_popup_rect(self) -> WindowRect:
        """Get popup window rect. Raises if not found."""
        if not self._popup_hwnd:
            raise RuntimeError("Popup window not found")
        return self._get_rect(self._popup_hwnd)

    def close_popup(self) -> bool:
        """Close the popup window."""
        if not self._popup_hwnd:
            return False
        user32.PostMessageW(self._popup_hwnd, 0x0010, 0, 0)  # WM_CLOSE
        self._popup_hwnd = None
        return True

    def move_to_primary_screen(self) -> None:
        """Move the main window onto the primary monitor if off-screen."""
        self._move_window_to_primary(self._main_hwnd)

    # ── Internal ───────────────────────────────────────────────────

    def _accept_main(self, hwnd: int) -> bool:
        """Check if hwnd is a valid, visible WeChat main window."""
        if not user32.IsWindowVisible(hwnd):
            return False
        rect = self._get_rect_safe(hwnd)
        if rect is None:
            return False
        if rect.width < _MIN_MAIN_W or rect.height < _MIN_MAIN_H:
            return False
        # Verify title contains "微信"
        title = self._get_window_text(hwnd)
        if "微信" not in title and "WeChat" not in title:
            return False
        return True

    def _find_popup(self) -> int | None:
        """Scan all visible Chrome_WidgetWin_0 windows for the article popup.

        The popup has title '微信', is NOT owned by the main window
        (4.1.11 popups are owned), and is reasonably sized.
        """
        wechat_pids = self._find_wechat_pids()
        candidates = []

        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM,
        )

        def callback(hwnd, _lparam):
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in wechat_pids:
                return True
            if not user32.IsWindowVisible(hwnd):
                return True

            # Check class name
            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buf, 256)
            if "Chrome_WidgetWin_0" not in class_buf.value:
                return True

            # Check title
            title = self._get_window_text(hwnd)
            if "微信" not in title:
                return True

            # Must NOT be the main window
            if hwnd == self._main_hwnd:
                return True

            rect = self._get_rect_safe(hwnd)
            if rect is None:
                return True
            if rect.width < _MIN_POPUP_W or rect.height < _MIN_POPUP_H:
                return True

            candidates.append((hwnd, rect))
            return True

        user32.EnumWindows(EnumWindowsProc(callback), 0)

        if not candidates:
            return None

        # The popup is typically the largest Chrome_WidgetWin_0
        candidates.sort(key=lambda x: -(x[1].width * x[1].height))
        return candidates[0][0]

    def _activate(self, hwnd: int | None) -> bool:
        """Bring a window to foreground."""
        if not hwnd:
            return False
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
                time.sleep(0.3)
            VK_MENU = 0x12
            for attempt in range(5):
                user32.keybd_event(VK_MENU, 0, 0, 0)
                user32.keybd_event(VK_MENU, 0, 0x0002, 0)
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.15)
                if user32.GetForegroundWindow() == hwnd:
                    return True
            return False
        except Exception:
            return False

    def _get_rect(self, hwnd: int) -> WindowRect:
        """Get window rect. Raises on failure."""
        rect = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise RuntimeError("GetWindowRect failed")
        return WindowRect(
            left=rect.left,
            top=rect.top,
            width=rect.right - rect.left,
            height=rect.bottom - rect.top,
        )

    def _get_rect_safe(self, hwnd: int) -> WindowRect | None:
        """Get window rect, return None on failure."""
        try:
            return self._get_rect(hwnd)
        except Exception:
            return None

    @staticmethod
    def _get_window_text(hwnd: int) -> str:
        """Get window title text."""
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    @staticmethod
    def _find_wechat_pids() -> set[int]:
        """Find PIDs of WeChat/Weixin/WeChatAppEx processes."""
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-Process WeChat,Weixin,WeChatAppEx -ErrorAction SilentlyContinue "
                 "| Select-Object -ExpandProperty Id"],
                capture_output=True, text=True, timeout=5, errors="replace",
            )
            pids = set()
            for line in result.stdout.strip().splitlines():
                if line.strip().isdigit():
                    pids.add(int(line.strip()))
            return pids
        except Exception:
            return set()

    def _move_window_to_primary(self, hwnd: int | None) -> None:
        """Move a window to primary monitor if off-screen."""
        if not hwnd:
            return
        try:
            SPI_GETWORKAREA = 0x0030

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            work_area = RECT()
            ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETWORKAREA, 0, ctypes.byref(work_area), 0
            )
            rect = self._get_rect_safe(hwnd)
            if rect is None:
                return
            if work_area.left <= rect.left < work_area.right:
                return  # already on primary

            user32.SetWindowPos(
                hwnd, None,
                work_area.left + 50, work_area.top + 50,
                rect.width, rect.height, 0x0000,
            )
        except Exception:
            pass
