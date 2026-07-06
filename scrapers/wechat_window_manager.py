# scrapers/wechat_window_manager.py
"""WeChat PC window discovery and activation.

Uses Windows API (ctypes) to find and control the WeChat main window.
No external dependencies beyond Python stdlib.
"""
import ctypes
import ctypes.wintypes
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Win32 constants
SW_RESTORE = 9
SW_SHOW = 5
GW_OWNER = 4
WM_CLOSE = 0x0010

user32 = ctypes.windll.user32

# Callback type for EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(
    ctypes.c_bool,
    ctypes.wintypes.HWND,
    ctypes.wintypes.LPARAM,
)


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


class WechatWindowManager:
    """Find, activate, and query the WeChat main window on Windows."""

    def __init__(self):
        self._hwnd: int | None = None

    def find_window(self) -> bool:
        """Try to find the WeChat main window. Returns True if found."""
        # Strategy 1: Find classic desktop version by class name
        for class_name in ["WeChatMainWndForPC", "WeChatLoginWndForPC"]:
            hwnd = user32.FindWindowW(class_name, None)
            if hwnd and user32.IsWindowVisible(hwnd):
                self._hwnd = hwnd
                logger.info(f"[window] Found WeChat window: class={class_name}")
                return True

        # Strategy 2: Find by WeChat/Weixin process + MainWindowTitle
        wechat_pids = self._find_wechat_pids()
        if wechat_pids:
            result = {"hwnd": None}

            def by_pid(hwnd, _lparam):
                pid = ctypes.wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value not in wechat_pids:
                    return True
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                result["hwnd"] = hwnd
                return False

            user32.EnumWindows(EnumWindowsProc(by_pid), 0)
            if result["hwnd"]:
                self._hwnd = result["hwnd"]
                logger.info("[window] Found WeChat window by process")
                return True

        # Strategy 3: Enumerate all visible windows, match by title
        result = {"hwnd": None}

        def by_title(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
            if "微信" in title or "WeChat" in title:
                result["hwnd"] = hwnd
                return False
            return True

        user32.EnumWindows(EnumWindowsProc(by_title), 0)
        if result["hwnd"]:
            self._hwnd = result["hwnd"]
            logger.info("[window] Found WeChat window by title")
            return True

        logger.warning("[window] WeChat window not found")
        return False

    def _find_wechat_pids(self) -> set:
        """Find PIDs of WeChat/Weixin processes."""
        import subprocess
        try:
            result = subprocess.run(
                ["powershell", "-Command",
                 "Get-Process WeChat,Weixin -ErrorAction SilentlyContinue "
                 "| Select-Object -ExpandProperty Id"],
                capture_output=True, text=True, timeout=5,
            )
            pids = set()
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.add(int(line))
            return pids
        except Exception:
            return set()

    def get_rect(self) -> WindowRect:
        """Get the WeChat window rectangle. Raises if not found."""
        if not self._hwnd:
            raise RuntimeError("WeChat 窗口未找到，请先运行 find_window()")

        rect = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(self._hwnd, ctypes.byref(rect)):
            raise RuntimeError("获取窗口位置失败: GetWindowRect failed")

        return WindowRect(
            left=rect.left,
            top=rect.top,
            width=rect.right - rect.left,
            height=rect.bottom - rect.top,
        )

    def activate(self) -> bool:
        """Bring WeChat window to the foreground. Returns True on success."""
        if not self._hwnd:
            logger.warning("[window] No window to activate")
            return False

        try:
            # Restore if minimized
            if user32.IsIconic(self._hwnd):
                user32.ShowWindow(self._hwnd, SW_RESTORE)

            # Bring to foreground
            user32.SetForegroundWindow(self._hwnd)
            logger.debug("[window] WeChat window activated")
            return True
        except Exception as e:
            logger.warning(f"[window] Failed to activate: {e}")
            return False

    def normalize_size(self, min_width: int = 900, min_height: int = 800) -> None:
        """Ensure the window is at least min_width x min_height."""
        if not self._hwnd:
            return

        try:
            if user32.IsIconic(self._hwnd):
                user32.ShowWindow(self._hwnd, SW_RESTORE)

            rect = self.get_rect()
            if rect.width < min_width or rect.height < min_height:
                user32.SetWindowPos(
                    self._hwnd, None,
                    0, 0, min_width, min_height,
                    0x0004,  # SWP_NOMOVE
                )
                logger.info(f"[window] Resized to {min_width}x{min_height}")
        except Exception as e:
            logger.warning(f"[window] Failed to normalize size: {e}")

    def move_to_primary_screen(self) -> None:
        """Move the WeChat window onto the primary monitor if it is off-screen.

        This is necessary because pyautogui.screenshot(region=...) only
        captures the primary monitor on Windows. If the WeChat window is
        on a secondary monitor, OCR screenshots return black pixels.
        """
        if not self._hwnd:
            logger.warning("[window] No window to move")
            return

        try:
            import ctypes

            # Get primary monitor work area
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

            rect = self.get_rect()

            # Check if window is significantly off the primary monitor
            if rect.left >= work_area.left and rect.left < work_area.right:
                logger.debug("[window] Window already on primary monitor")
                return

            # Move window to top-left of primary monitor (with small offset)
            new_left = work_area.left + 50
            new_top = work_area.top + 50
            user32.SetWindowPos(
                self._hwnd, None,
                new_left, new_top, rect.width, rect.height,
                0x0000,  # No special flags
            )
            logger.info(
                f"[window] Moved window from ({rect.left},{rect.top}) "
                f"to primary monitor ({new_left},{new_top})"
            )
        except Exception as e:
            logger.warning(f"[window] Failed to move to primary screen: {e}")

    @property
    def is_found(self) -> bool:
        return self._hwnd is not None
