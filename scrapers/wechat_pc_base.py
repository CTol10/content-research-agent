# scrapers/wechat_pc_base.py
"""Base class for WeChat PC desktop scrapers (relative coordinate approach).

Provides window management, relative coordinate resolution, input control,
OCR integration, and screenshot diagnostics. Subclasses implement platform-
specific scraping flows for 公众号 and 视频号.
"""
import json
import logging
import re
import subprocess
import time
from pathlib import Path

import pyautogui

import config
from scrapers.wechat_window_manager import WechatWindowManager, WindowRect
from scrapers.wechat_ocr import WechatOcr

logger = logging.getLogger(__name__)

# Disable pyautogui failsafe pause (we handle our own safety)
pyautogui.PAUSE = 0.1
pyautogui.FAILSAFE = False

# Default config file path
_WECHAT_PC_CONFIG = config.BASE_DIR / "config.wechat_pc.json"


class WechatPcBaseScraper:
    """Base class for PC-based WeChat scrapers using relative coordinates.

    Subclasses must set `platform_name` and implement `scrape(url)`.
    """

    platform_name: str = "wechat_pc_base"

    def __init__(self):
        self.window_mgr = WechatWindowManager()
        self.ocr = WechatOcr()
        self._profile: dict = {}
        self._rect: WindowRect | None = None
        self._scrape_count: int = 0

    # ── Lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Find WeChat window, load layout profile, verify ready."""
        if not self.window_mgr.find_window():
            raise RuntimeError(
                "找不到微信窗口。请确保微信 PC 客户端已启动并登录。"
            )
        # Ensure window is on the primary monitor for OCR screenshots
        self.window_mgr.move_to_primary_screen()
        self._rect = self.window_mgr.get_rect()
        logger.info(
            f"[{self.platform_name}] WeChat window: "
            f"({self._rect.left}, {self._rect.top}) "
            f"{self._rect.width}x{self._rect.height}"
        )
        self._profile = self._load_profile()
        logger.info(f"[{self.platform_name}] Loaded profile: {self._profile_name}")

        if not self._profile or "wechat_main" not in self._profile:
            raise RuntimeError(
                "微信坐标配置为空或不完整。\n"
                "请先运行坐标校准工具: python main.py --wechat-calibrate"
            )

    def stop(self) -> None:
        """No cleanup needed for PC approach."""
        pass

    def scrape(self, url: str) -> dict:
        """Scrape comments from URL. Must be implemented by subclass."""
        raise NotImplementedError

    # ── Profile loading ──────────────────────────────────────────

    def _load_profile(self) -> dict:
        """Load layout profile from config.wechat_pc.json."""
        if not _WECHAT_PC_CONFIG.exists():
            logger.warning(
                f"[{self.platform_name}] Config not found: {_WECHAT_PC_CONFIG}"
            )
            self._profile_name = "default"
            return {}

        try:
            data = json.loads(_WECHAT_PC_CONFIG.read_text(encoding="utf-8"))
            profile_name = data.get("active_profile", "wechat_pc_default_125")
            self._profile_name = profile_name
            return data.get("profiles", {}).get(profile_name, {})
        except Exception as e:
            logger.error(f"[{self.platform_name}] Failed to load profile: {e}")
            self._profile_name = "error"
            return {}

    # ── Coordinate resolution ────────────────────────────────────

    def resolve_point(self, section: str, key: str) -> tuple[int, int]:
        """Resolve a named relative point to absolute screen coordinates.

        Args:
            section: Config section (e.g. "wechat_main", "official", "channels")
            key: Point name (e.g. "search_bar", "comment_button")

        Returns:
            (abs_x, abs_y) in screen coordinates
        """
        if not self._rect:
            raise RuntimeError("Window rect not available")

        point = self._profile.get(section, {}).get(key, {})
        x_ratio = point.get("x_ratio", 0.5)
        y_ratio = point.get("y_ratio", 0.5)

        abs_x = self._rect.left + round(self._rect.width * x_ratio)
        abs_y = self._rect.top + round(self._rect.height * y_ratio)
        return abs_x, abs_y

    def resolve_region(self, section: str, key: str) -> tuple[int, int, int, int]:
        """Resolve a named relative region to absolute screen region.

        Args:
            section: Config section
            key: Region name (e.g. "comment_panel_region")

        Returns:
            (left, top, width, height) in screen coordinates
        """
        if not self._rect:
            raise RuntimeError("Window rect not available")

        region = self._profile.get(section, {}).get(key, {})
        left_ratio = region.get("left_ratio", 0.0)
        top_ratio = region.get("top_ratio", 0.0)
        width_ratio = region.get("width_ratio", 1.0)
        height_ratio = region.get("height_ratio", 1.0)

        left = self._rect.left + round(self._rect.width * left_ratio)
        top = self._rect.top + round(self._rect.height * top_ratio)
        width = round(self._rect.width * width_ratio)
        height = round(self._rect.height * height_ratio)
        return left, top, width, height

    # ── Input helpers ────────────────────────────────────────────

    def click_key(self, section: str, key: str, desc: str = "") -> None:
        """Click a named relative point."""
        x, y = self.resolve_point(section, key)
        logger.debug(f"[{self.platform_name}] Click ({x}, {y}) {desc}")
        pyautogui.click(x, y)
        time.sleep(0.5)

    def click_point(self, x: int, y: int, desc: str = "") -> None:
        """Click an absolute screen point."""
        logger.debug(f"[{self.platform_name}] Click ({x}, {y}) {desc}")
        pyautogui.click(x, y)
        time.sleep(0.5)

    def paste_and_enter(self, text: str) -> None:
        """Paste text into focused input and press Enter."""
        # Use PowerShell clipboard for reliable paste
        subprocess.run(
            ["powershell", "-Command", f"Set-Clipboard -Value '{text}'"],
            check=True, capture_output=True,
        )
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.5)
        pyautogui.press("enter")
        time.sleep(0.5)

    def clear_input(self) -> None:
        """Select all and delete in the focused input."""
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.2)
        pyautogui.press("delete")
        time.sleep(0.2)

    def scroll_at_key(
        self, section: str, key: str, clicks: int = -3
    ) -> None:
        """Scroll at a named relative point."""
        x, y = self.resolve_point(section, key)
        logger.debug(f"[{self.platform_name}] Scroll at ({x}, {y}), clicks={clicks}")
        pyautogui.scroll(clicks, x=x, y=y)
        time.sleep(0.5)

    # ── Screenshot & OCR ─────────────────────────────────────────

    def take_screenshot(self, name: str) -> str:
        """Take a full-window screenshot. Returns saved path."""
        debug_dir = config.OUTPUT_DIR / "debug" / "wechat_pc"
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / f"{self.platform_name}_{name}.png"
        img = pyautogui.screenshot()
        img.save(str(path))
        logger.debug(f"[{self.platform_name}] Screenshot: {path}")
        return str(path)

    def screenshot_region(
        self, section: str, key: str, name: str
    ) -> str:
        """Screenshot a named relative region. Returns saved path."""
        left, top, width, height = self.resolve_region(section, key)
        debug_dir = config.OUTPUT_DIR / "debug" / "wechat_pc"
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / f"{self.platform_name}_{name}.png"
        img = pyautogui.screenshot(region=(left, top, width, height))
        img.save(str(path))
        logger.debug(
            f"[{self.platform_name}] Region screenshot: {path} "
            f"({left},{top},{width},{height})"
        )
        return str(path)

    def ocr_region(
        self, section: str, key: str, name: str
    ) -> list[tuple[str, str]]:
        """Screenshot a region and OCR extract comments from it."""
        path = self.screenshot_region(section, key, name)
        return self.ocr.extract_comments(path)

    def ocr_region_lines(
        self, section: str, key: str, name: str
    ) -> list[str]:
        """Screenshot a region and OCR extract all text lines."""
        path = self.screenshot_region(section, key, name)
        return self.ocr.extract_text(path)

    def ocr_region_with_boxes(
        self, section: str, key: str, name: str
    ) -> list[dict]:
        """Screenshot a region and OCR with bounding-box positions.

        Each dict has {text, cx, cy, top, bottom, left, right}
        where cx/cy are relative to the screenshot image.
        """
        path = self.screenshot_region(section, key, name)
        return self.ocr.extract_text_with_boxes(path)

    def find_expand_buttons(
        self, section: str, key: str, name: str = "expand_scan"
    ) -> list[dict]:
        """Find all '展开' / 'N条回复' buttons in a region.

        Returns list of dicts with {text, screen_x, screen_y},
        sorted from bottom to top so clicks don't disrupt later targets.
        """
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

        # Sort bottom-to-top: higher screen_y first
        buttons.sort(key=lambda b: -b["screen_y"])
        return buttons

    def expand_all_in_region(
        self,
        section: str,
        key: str,
        max_rounds: int = 5,
        scroll_clicks: int = -2,
    ) -> int:
        """Click all '展开' buttons in a region, scrolling between rounds.

        Returns the total number of buttons clicked.
        """
        total_clicked = 0

        for round_num in range(max_rounds):
            buttons = self.find_expand_buttons(
                section, key, f"expand_scan_{round_num:02d}"
            )
            if not buttons:
                logger.debug(
                    f"[{self.platform_name}] expand: no more buttons "
                    f"after {round_num} rounds"
                )
                break

            logger.info(
                f"[{self.platform_name}] expand round {round_num+1}: "
                f"{len(buttons)} buttons"
            )
            for btn in buttons:
                logger.debug(
                    f"  click '{btn['text']}' at ({btn['screen_x']}, {btn['screen_y']})"
                )
                self.click_point(btn["screen_x"], btn["screen_y"], f"展开:{btn['text']}")
                time.sleep(0.3)

            total_clicked += len(buttons)
            time.sleep(0.8)  # Wait for expansion animation

            # Scroll to reveal more hidden buttons
            self.scroll_at_key(section, "article_scroll_start", clicks=scroll_clicks)
            time.sleep(1.0)

        logger.info(
            f"[{self.platform_name}] expand done: {total_clicked} total clicks"
        )
        return total_clicked

    # ── Window refresh ───────────────────────────────────────────

    def refresh_rect(self) -> None:
        """Re-read window position (may have moved/resized)."""
        if self.window_mgr.is_found:
            self._rect = self.window_mgr.get_rect()
