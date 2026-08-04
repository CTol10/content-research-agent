# scrapers/wechat/v417/base.py
"""Base class for WeChat 4.1.7 PC scrapers — dual-window coordinate system.

4.1.7 opens articles/videos in a SEPARATE popup window.  Coordinates in
``wechat_main`` resolve against the Qt main window; ``official`` /
``channels`` coordinates resolve against the Chrome_WidgetWin_0 popup.
"""
import json
import logging
import subprocess
import time
from pathlib import Path

import pyautogui
from PIL import Image, ImageChops, ImageStat

import config
from scrapers.wechat.ocr import WechatOcr, merge_comment_fragments
from scrapers.wechat.v417.window_manager import (
    WechatWindowManagerV417,
    WindowRect,
)

logger = logging.getLogger(__name__)

pyautogui.PAUSE = 0.1
pyautogui.FAILSAFE = False

_CONFIG_417 = config.BASE_DIR / "config.wechat_pc_417.json"


class WechatPcBaseScraperV417:
    """Base class for 4.1.7 — main window + popup window coordinate resolution.

    Subclasses must set ``platform_name`` and implement ``scrape(url)``.
    """

    platform_name: str = "wechat_pc_417"

    def __init__(self):
        self.window_mgr = WechatWindowManagerV417()
        self.ocr = WechatOcr()
        self._profile: dict = {}
        self._profile_name: str = ""
        self._scrape_count: int = 0

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self) -> None:
        """Find main window, load profile."""
        import ctypes
        user32 = ctypes.windll.user32
        VK_F8, VK_ESCAPE = 0x77, 0x1B

        while not self.window_mgr.find_main():
            print("\n" + "=" * 60)
            print("  未检测到微信窗口。请打开微信 PC 客户端并登录。")
            print("  打开后保持微信在前台，按 F8 继续（按 Esc 放弃）")
            print("=" * 60)
            user32.GetAsyncKeyState(VK_F8)
            user32.GetAsyncKeyState(VK_ESCAPE)
            while True:
                if user32.GetAsyncKeyState(VK_F8) & 0x8000:
                    while user32.GetAsyncKeyState(VK_F8) & 0x8000:
                        time.sleep(0.05)
                    break
                if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
                    raise RuntimeError("用户取消：未打开微信窗口")
                time.sleep(0.05)

        self.window_mgr.move_to_primary_screen()
        main_rect = self.window_mgr.get_main_rect()
        logger.info(
            f"[{self.platform_name}] Main window: "
            f"({main_rect.left}, {main_rect.top}) "
            f"{main_rect.width}x{main_rect.height}"
        )

        self._profile = self._load_profile()
        logger.info(
            f"[{self.platform_name}] Loaded profile: {self._profile_name}"
        )

        if not self._profile or "wechat_main" not in self._profile:
            raise RuntimeError(
                "微信 4.1.7 坐标配置为空或不完整。\n"
                "请先运行坐标校准工具。"
            )

    async def stop(self) -> None:
        pass

    # ── Profile loading ────────────────────────────────────────────

    def _load_profile(self) -> dict:
        if not _CONFIG_417.exists():
            logger.warning(
                f"[{self.platform_name}] Config not found: {_CONFIG_417}"
            )
            self._profile_name = "default"
            return {}

        try:
            data = json.loads(_CONFIG_417.read_text(encoding="utf-8"))
            profile_name = data.get("active_profile", "v417_default")
            self._profile_name = profile_name
            return data.get("profiles", {}).get(profile_name, {})
        except Exception as e:
            logger.error(f"[{self.platform_name}] Failed to load profile: {e}")
            self._profile_name = "error"
            return {}

    # ── Coordinate resolution ──────────────────────────────────────

    def resolve_point(self, section: str, key: str) -> tuple[int, int]:
        """Resolve a named relative point to absolute screen coordinates.

        ``wechat_main`` → relative to main window rect.
        ``official`` / ``channels`` → relative to popup window rect.
        """
        rect = self._rect_for(section)
        point = self._profile.get(section, {}).get(key, {})
        x_ratio = point.get("x_ratio", 0.5)
        y_ratio = point.get("y_ratio", 0.5)
        abs_x = rect.left + round(rect.width * x_ratio)
        abs_y = rect.top + round(rect.height * y_ratio)
        return abs_x, abs_y

    def resolve_region(
        self, section: str, key: str
    ) -> tuple[int, int, int, int]:
        """Resolve a named relative region to absolute screen region."""
        rect = self._rect_for(section)
        region = self._profile.get(section, {}).get(key, {})
        left = rect.left + round(rect.width * region.get("left_ratio", 0))
        top = rect.top + round(rect.height * region.get("top_ratio", 0))
        width = round(rect.width * region.get("width_ratio", 1))
        height = round(rect.height * region.get("height_ratio", 1))
        return left, top, width, height

    def _rect_for(self, section: str) -> WindowRect:
        """Choose which window rect to resolve against."""
        if section == "wechat_main":
            return self.window_mgr.get_main_rect()
        else:
            # "official", "channels" → popup window
            return self.window_mgr.get_popup_rect()

    # ── Window interaction ─────────────────────────────────────────

    def click_key(self, section: str, key: str, desc: str = "") -> None:
        """Resolve and click a named relative point."""
        x, y = self.resolve_point(section, key)
        logger.debug(f"[{self.platform_name}] Click ({x}, {y}) {desc}")
        pyautogui.click(x, y)
        time.sleep(0.5)

    def click_point(self, x: int, y: int, desc: str = "") -> None:
        """Click an absolute screen point."""
        logger.debug(f"[{self.platform_name}] Click ({x}, {y}) {desc}")
        pyautogui.click(x, y)
        time.sleep(0.5)

    # ── Screenshot ─────────────────────────────────────────────────

    def screenshot_region(
        self, section: str, key: str, name: str
    ) -> Path:
        """Screenshot a named region and save to debug dir."""
        left, top, width, height = self.resolve_region(section, key)
        debug_dir = config.WECHAT_PC_DEBUG_DIR
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / f"{self.platform_name}_{name}.png"
        img = pyautogui.screenshot(region=(left, top, width, height))
        img.save(str(path))
        logger.debug(f"[{self.platform_name}] Region screenshot: {path} ({left},{top},{width},{height})")
        return path

    def screenshot_region_as_image(
        self, section: str, key: str, name: str = ""
    ) -> Image.Image:
        """Screenshot a named region, return PIL Image."""
        left, top, width, height = self.resolve_region(section, key)
        img = pyautogui.screenshot(region=(left, top, width, height))
        if name:
            debug_dir = config.WECHAT_PC_DEBUG_DIR
            debug_dir.mkdir(parents=True, exist_ok=True)
            path = debug_dir / f"{self.platform_name}_{name}.png"
            img.save(str(path))
        return img

    def take_screenshot(self, name: str = "debug") -> Path | None:
        """Full-screen debug screenshot."""
        try:
            debug_dir = config.WECHAT_PC_DEBUG_DIR
            debug_dir.mkdir(parents=True, exist_ok=True)
            path = debug_dir / f"{self.platform_name}_{name}.png"
            pyautogui.screenshot(str(path))
            logger.debug(f"[{self.platform_name}] Screenshot: {path}")
            return path
        except Exception as e:
            logger.warning(f"[{self.platform_name}] Screenshot failed: {e}")
            return None

    # ── OCR helpers ─────────────────────────────────────────────────

    def ocr_region(
        self, section: str, key: str, name: str
    ) -> list[tuple[str, str]]:
        """Screenshot a region and OCR extract comments."""
        path = self.screenshot_region(section, key, name)
        return self.ocr.extract_comments(path)

    def ocr_region_lines(
        self, section: str, key: str, name: str
    ) -> list[str]:
        """Screenshot a region and OCR extract text lines."""
        path = self.screenshot_region(section, key, name)
        return self.ocr.extract_text(path)

    def ocr_region_with_boxes(
        self, section: str, key: str, name: str
    ) -> list[dict]:
        """Screenshot a region and OCR with bounding-box positions."""
        path = self.screenshot_region(section, key, name)
        return self.ocr.extract_text_with_boxes(path)

    def click_text_via_ocr(
        self, search_text: str, region: tuple[int, int, int, int],
    ) -> bool:
        """Screenshot a region, OCR it, click first match of ``search_text``."""
        left, top, width, height = region
        img = pyautogui.screenshot(region=(left, top, width, height))
        debug_dir = config.WECHAT_PC_DEBUG_DIR
        debug_dir.mkdir(parents=True, exist_ok=True)
        tmp = debug_dir / f"{self.platform_name}_ocr_click.png"
        img.save(str(tmp))
        items = self.ocr.extract_text_with_boxes(str(tmp))

        logger.info(
            f"[{self.platform_name}] OCR found {len(items)} items: "
            f"{[it['text'][:20] for it in items[:30]]}"
        )

        for item in items:
            if search_text in item["text"]:
                screen_x = left + item["cx"]
                screen_y = top + item["cy"]
                logger.info(
                    f"[{self.platform_name}] OCR click '{item['text']}' "
                    f"at ({screen_x}, {screen_y})"
                )
                pyautogui.click(screen_x, screen_y)
                time.sleep(0.5)
                return True

        logger.warning(
            f"[{self.platform_name}] '{search_text}' not found "
            f"in {len(items)} OCR items"
        )
        return False

    # ── Expand buttons ──────────────────────────────────────────────

    def find_expand_buttons(
        self, section: str, key: str, name: str = "expand_scan"
    ) -> list[dict]:
        """Find all '展开' / 'N条回复' buttons in a region."""
        import re
        region_left, region_top, region_w, region_h = \
            self.resolve_region(section, key)
        items = self.ocr_region_with_boxes(section, key, name)

        expand_pattern = re.compile(r"展开|\d*条回复")
        buttons = []
        for item in items:
            if expand_pattern.search(item["text"]):
                buttons.append({
                    "text": item["text"],
                    "screen_x": region_left + item["cx"],
                    "screen_y": region_top + item["cy"],
                })

        buttons.sort(key=lambda b: -b["screen_y"])
        return buttons

    # ── Scrolling ──────────────────────────────────────────────────

    def _scroll_spaced(
        self, clicks: int, x: int, y: int,
        chunk: int = 3, delay: float = 0.04,
    ) -> None:
        """Send wheel clicks in spaced chunks to avoid CEF coalescing."""
        remaining = clicks
        while remaining > 0:
            n = min(chunk, remaining)
            pyautogui.scroll(-n, x=x, y=y)
            remaining -= n
            if remaining > 0:
                time.sleep(delay)

    def _measure_px_per_click(
        self, left: int, top: int, width: int, height: int,
        cx: int, cy: int, test_clicks: int = 3,
    ) -> float:
        """Measure actual pixels scrolled per wheel click.

        Scrolls a few clicks and template-matches the pre-scroll top strip
        against the post-scroll image to find how far content moved.
        Falls back to 25.0 px/click if measurement is inconclusive.
        """
        from PIL import Image
        import numpy as np

        before = pyautogui.screenshot(region=(left, top, width, height))
        before_gray = np.array(before.convert("L"), dtype=np.int16)

        self._scroll_spaced(test_clicks, cx, cy)
        time.sleep(0.5)

        after = pyautogui.screenshot(region=(left, top, width, height))
        after_gray = np.array(after.convert("L"), dtype=np.int16)

        strip_h = min(40, height // 4)
        if strip_h < 10:
            return 25.0

        template = before_gray[:strip_h, :]
        best_offset = 0
        best_diff = float("inf")
        max_search = height - strip_h
        for offset in range(0, max_search, 1):
            candidate = after_gray[offset:offset + strip_h, :]
            diff = np.sum(np.abs(template - candidate))
            if diff < best_diff:
                best_diff = diff
                best_offset = offset

        if best_offset <= 2:
            return 25.0

        px_per_click = best_offset / test_clicks
        logger.info(
            f"[{self.platform_name}] Measured scroll: {best_offset}px "
            f"over {test_clicks} clicks → {px_per_click:.1f} px/click"
        )
        return px_per_click

    @staticmethod
    def images_similar(
        img1: Image.Image, img2: Image.Image, threshold: float = 0.02,
    ) -> bool:
        """Return True when two images are nearly identical."""
        g1 = img1.convert("L")
        g2 = img2.convert("L")
        diff = ImageChops.difference(g1, g2)
        stat = ImageStat.Stat(diff)
        avg_diff = stat.mean[0]
        return avg_diff < (threshold * 255)

    # ── Input helpers ──────────────────────────────────────────────

    def clear_input(self) -> None:
        """Clear any text in the focused input field."""
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.1)
        pyautogui.press("backspace")
        time.sleep(0.1)

    def paste_and_enter(self, text: str) -> None:
        """Paste text and press Enter via keybd_event."""
        import ctypes
        user32 = ctypes.windll.user32
        VK_RETURN = 0x0D

        subprocess.run(
            ["powershell", "-Command", f"Set-Clipboard -Value '{text}'"],
            check=True, capture_output=True,
        )
        pyautogui.hotkey("ctrl", "v")
        time.sleep(2.0)

        for vk in (0x11, 0x12, 0x10):
            user32.keybd_event(vk, 0, 0x0002, 0)
        time.sleep(0.05)

        user32.keybd_event(VK_RETURN, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(VK_RETURN, 0, 0x0002, 0)
        time.sleep(1.0)
