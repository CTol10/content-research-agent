# scrapers/wechat/__init__.py
"""WeChat PC scraper factory — auto-detects WeChat version at runtime.

WeChat 4.1.11: single window, content opens as right-side panel.
    → ``scrapers.wechat.v411.*``

WeChat 4.1.7:  dual window, content opens as separate popup.
    → ``scrapers.wechat.v417.*``

Detection: scans visible WeChat windows for class names.
  - ``Qt51514QWindowIcon`` → 4.1.7
  - ``WeChatMainWndForPC`` / ``WeixinMainWndForPC`` → 4.1.11
"""
import ctypes
import ctypes.wintypes
import logging
import subprocess

from scrapers.wechat.v411 import (
    WechatChannelsPcScraper as ChannelsV411,
    WechatOfficialPcScraper as OfficialV411,
    WechatWindowManager as WindowManagerV411,
)
from scrapers.wechat.v417 import (
    WechatChannelsPcScraperV417 as ChannelsV417,
    WechatOfficialPcScraperV417 as OfficialV417,
    WechatWindowManagerV417 as WindowManagerV417,
)

logger = logging.getLogger(__name__)


def detect_version() -> str:
    """Detect the running WeChat version.

    Returns:
        "4.1.7" or "4.1.11"
    """
    user32 = ctypes.windll.user32

    # Check for 4.1.7: Qt-based main window
    hwnd = user32.FindWindowW("Qt51514QWindowIcon", None)
    if hwnd and user32.IsWindowVisible(hwnd):
        logger.info("[wechat] Detected 4.1.7 (Qt main window)")
        return "4.1.7"

    # Also check for other Qt versions
    for qt_class in ["Qt51514QWindowIcon", "Qt514QWindowIcon", "Qt5QWindowIcon"]:
        hwnd = user32.FindWindowW(qt_class, None)
        if hwnd and user32.IsWindowVisible(hwnd):
            logger.info(f"[wechat] Detected 4.1.7 ({qt_class})")
            return "4.1.7"

    # Check for 4.1.11: WeChatMainWndForPC / WeixinMainWndForPC
    for class_name in [
        "WeChatMainWndForPC", "WeChatLoginWndForPC",
        "WeixinMainWndForPC", "WeixinLoginWndForPC",
    ]:
        hwnd = user32.FindWindowW(class_name, None)
        if hwnd and user32.IsWindowVisible(hwnd):
            logger.info(f"[wechat] Detected 4.1.11 ({class_name})")
            return "4.1.11"

    # Fallback: check by process name → scan all windows by title
    wechat_pids = _find_wechat_pids()
    if wechat_pids:
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM,
        )
        result = {"version": "4.1.11"}  # default

        def callback(hwnd, _lparam):
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in wechat_pids:
                return True
            if not user32.IsWindowVisible(hwnd):
                return True
            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buf, 256)
            cls = class_buf.value
            if "Qt" in cls:
                result["version"] = "4.1.7"
                return False  # stop enumeration
            return True

        user32.EnumWindows(EnumWindowsProc(callback), 0)
        version = result["version"]
        logger.info(f"[wechat] Detected {version} (fallback via process)")
        return version

    logger.warning("[wechat] Could not detect WeChat version, defaulting to 4.1.11")
    return "4.1.11"


def _find_wechat_pids() -> set[int]:
    """Find PIDs of WeChat/Weixin/WeChatAppEx processes."""
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-Process WeChat,Weixin,WeChatAppEx -ErrorAction SilentlyContinue "
             "| Select-Object -ExpandProperty Id"],
            capture_output=True, text=True, timeout=5,
        )
        pids = set()
        for line in result.stdout.strip().splitlines():
            if line.strip().isdigit():
                pids.add(int(line.strip()))
        return pids
    except Exception:
        return set()


def get_official_scraper():
    """Return the appropriate 公众号 scraper for the detected version."""
    version = detect_version()
    if version == "4.1.7":
        return OfficialV417()
    return OfficialV411()


def get_channels_scraper():
    """Return the appropriate 视频号 scraper for the detected version."""
    version = detect_version()
    if version == "4.1.7":
        return ChannelsV417()
    return ChannelsV411()


__all__ = [
    "detect_version",
    "get_official_scraper",
    "get_channels_scraper",
    "OfficialV411", "ChannelsV411", "WindowManagerV411",
    "OfficialV417", "ChannelsV417", "WindowManagerV417",
]
