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
import shutil
import subprocess
import sys
from pathlib import Path

import config

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


_VERSION_CACHE: str | None = None


def _infer_version_by_config() -> str:
    """检测不到微信窗口时，按已校准的配置文件推断版本。

    dist 里有 config.wechat_pc_417.json → 4.1.7；否则看 config.wechat_pc.json。
    避免 4.1.7 用户在 Qt 主窗口不可见（最小化/未前台）时被误判 4.1.11 →
    误走 v411 → 找 config.wechat_pc.json（没有）→ 空配置启动失败。
    """
    try:
        if (config.BASE_DIR / "config.wechat_pc_417.json").exists():
            return "4.1.7"
        if (config.BASE_DIR / "config.wechat_pc.json").exists():
            return "4.1.11"
    except Exception:  # noqa: BLE001
        pass
    return "4.1.11"


def ensure_default_wechat_config() -> None:
    """冻结运行时，把内置默认的 4.1.7 坐标配置落地到 exe 同目录。

    首次运行（exe 同目录无 config.wechat_pc_417.json）时从打包的
    _MEIPASS 拷出默认配置，开箱即用。用户重新校准后会生成同目录文件，
    已存在则不再覆盖——用户配置优先。
    """
    if not getattr(sys, "frozen", False):
        return
    target = config.BASE_DIR / "config.wechat_pc_417.json"
    if target.exists():
        return
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "config.wechat_pc_417.json"
    if not bundled.exists():
        return
    try:
        shutil.copyfile(bundled, target)
        print(f"已使用内置默认坐标配置（微信 4.1.7），已写入: {target}")
        print("如果你的微信窗口布局不同，可运行 --wechat-calibrate 重新校准。")
        logger.info(f"[wechat] Materialized default config to {target}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[wechat] 落地默认配置失败: {e}")


def detect_version() -> str:
    """Detect the running WeChat version. Returns "4.1.7" or "4.1.11".

    结果进程级缓存——一次检测、全局复用。之前公众号/视频号 scraper 各自
    实例化时各调一次 detect_version，窗口可见性飘忽会让两次返回不一致
    （一次 4.1.7、一次 4.1.11），scraper 选错版本 → 找不到对应 config。
    缓存后全局一致，符合"一开始检测一次、之后复用"。检测不到窗口时按
    config 文件推断，不再硬默认 4.1.11。
    """
    global _VERSION_CACHE
    if _VERSION_CACHE is not None:
        return _VERSION_CACHE

    user32 = ctypes.windll.user32

    # Check for 4.1.7: Qt-based main window
    hwnd = user32.FindWindowW("Qt51514QWindowIcon", None)
    if hwnd and user32.IsWindowVisible(hwnd):
        _VERSION_CACHE = "4.1.7"
        logger.info("[wechat] Detected 4.1.7 (Qt main window)")
        return _VERSION_CACHE

    # Also check for other Qt versions
    for qt_class in ["Qt51514QWindowIcon", "Qt514QWindowIcon", "Qt5QWindowIcon"]:
        hwnd = user32.FindWindowW(qt_class, None)
        if hwnd and user32.IsWindowVisible(hwnd):
            _VERSION_CACHE = "4.1.7"
            logger.info(f"[wechat] Detected 4.1.7 ({qt_class})")
            return _VERSION_CACHE

    # Check for 4.1.11: WeChatMainWndForPC / WeixinMainWndForPC
    for class_name in [
        "WeChatMainWndForPC", "WeChatLoginWndForPC",
        "WeixinMainWndForPC", "WeixinLoginWndForPC",
    ]:
        hwnd = user32.FindWindowW(class_name, None)
        if hwnd and user32.IsWindowVisible(hwnd):
            _VERSION_CACHE = "4.1.11"
            logger.info(f"[wechat] Detected 4.1.11 ({class_name})")
            return _VERSION_CACHE

    # Fallback: 扫描 WeChat 进程的可见窗口找 Qt 类；仍找不到 Qt 窗口则按 config 推断
    wechat_pids = _find_wechat_pids()
    if wechat_pids:
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM,
        )
        found = {"version": None}

        def callback(hwnd, _lparam):
            pid = ctypes.wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in wechat_pids:
                return True
            if not user32.IsWindowVisible(hwnd):
                return True
            class_buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buf, 256)
            if "Qt" in class_buf.value:
                found["version"] = "4.1.7"
                return False  # stop enumeration
            return True

        user32.EnumWindows(EnumWindowsProc(callback), 0)
        if found["version"]:
            _VERSION_CACHE = found["version"]
            logger.info(f"[wechat] Detected {_VERSION_CACHE} (fallback via process)")
            return _VERSION_CACHE

    # 窗口检测不到：按已校准 config 文件推断版本（不再硬默认 4.1.11）
    _VERSION_CACHE = _infer_version_by_config()
    logger.warning(
        f"[wechat] 未检测到微信窗口，按 config 文件推断版本为 {_VERSION_CACHE}"
    )
    return _VERSION_CACHE


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
    "ensure_default_wechat_config",
    "get_official_scraper",
    "get_channels_scraper",
    "OfficialV411", "ChannelsV411", "WindowManagerV411",
    "OfficialV417", "ChannelsV417", "WindowManagerV417",
]
