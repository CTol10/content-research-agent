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
from PIL import Image, ImageChops, ImageStat

import config
from scrapers.wechat.v411.window_manager import WechatWindowManager, WindowRect
from scrapers.wechat.ocr import WechatOcr

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

    async def start(self) -> None:
        """Find WeChat window, load layout profile, verify ready.

        If the WeChat window is not visible (not opened, or minimized to
        the tray), prompt the user to open it and press **F8** to continue.

        F8 is polled via ``GetAsyncKeyState`` (a global hotkey) rather
        than ``input()`` so the user keeps WeChat as the foreground
        window. Typing Enter in the console would steal focus, and the
        subsequent ``SetForegroundWindow`` call can fail to reclaim it
        (Windows' foreground lock), causing paste/Enter to land in the
        wrong window. With F8, WeChat stays foreground throughout.
        """
        import ctypes
        user32 = ctypes.windll.user32
        VK_F8, VK_ESCAPE = 0x77, 0x1B

        while not self.window_mgr.find_window():
            print("\n" + "=" * 60)
            print("  未检测到微信窗口。请打开微信 PC 客户端并登录。")
            print("  （从托盘点开、或从开始菜单启动均可）")
            print("  打开后保持微信在前台，按 F8 继续（按 Esc 放弃）")
            print("=" * 60)
            # Clear pending key state
            user32.GetAsyncKeyState(VK_F8)
            user32.GetAsyncKeyState(VK_ESCAPE)
            while True:
                if user32.GetAsyncKeyState(VK_F8) & 0x8000:
                    # wait for release to avoid double-trigger
                    while user32.GetAsyncKeyState(VK_F8) & 0x8000:
                        time.sleep(0.05)
                    break
                if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
                    raise RuntimeError("用户取消：未打开微信窗口")
                time.sleep(0.05)
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

    async def stop(self) -> None:
        """No cleanup needed for PC approach."""
        pass

    async def wait_for_login(self, timeout: int = 300) -> bool:
        """Check if WeChat PC window is open and ready.

        For WeChat platforms there is no browser-based login — the user just
        needs the WeChat PC client running.  Returns True if the window is
        found, False if the user aborts.
        """
        import ctypes
        user32 = ctypes.windll.user32
        VK_F8, VK_ESCAPE = 0x77, 0x1B

        if self.window_mgr.find_window():
            print(f"[{self.platform_name}] 微信窗口已检测到，无需额外登录操作。")
            return True

        print("\n" + "=" * 60)
        print(f"  [{self.platform_name}] 未检测到微信窗口。")
        print("  请打开微信 PC 客户端并登录。")
        print("  打开后保持微信在前台，按 F8 继续（按 Esc 放弃）")
        print("=" * 60)
        user32.GetAsyncKeyState(VK_F8)
        user32.GetAsyncKeyState(VK_ESCAPE)
        while True:
            if user32.GetAsyncKeyState(VK_F8) & 0x8000:
                while user32.GetAsyncKeyState(VK_F8) & 0x8000:
                    time.sleep(0.05)
                if self.window_mgr.find_window():
                    print(f"[{self.platform_name}] 微信窗口已就绪。")
                    return True
                print("  [提示] 仍未检测到微信窗口，请确认窗口已打开后按 F8 重试")
                continue
            if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
                while user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
                    time.sleep(0.05)
                print(f"[{self.platform_name}] 用户取消，微信平台将被跳过。")
                return False
            time.sleep(0.05)

    async def login_interactive(self, wait_seconds: int = 300):
        """WeChat PC login — delegate to wait_for_login window check."""
        success = await self.wait_for_login(timeout=wait_seconds)
        if not success:
            raise RuntimeError(f"[{self.platform_name}] 微信窗口未就绪，登录取消。")

    async def scrape(self, url: str) -> dict:
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
        """Paste text into focused input and press Enter.

        Uses Windows keybd_event for Enter (pyautogui.press("enter") can
        behave differently — in WeChat 4.x it sometimes dismisses the
        search page instead of submitting).
        """
        import ctypes
        user32 = ctypes.windll.user32
        VK_RETURN = 0x0D

        # Use PowerShell clipboard for reliable paste
        subprocess.run(
            ["powershell", "-Command", f"Set-Clipboard -Value '{text}'"],
            check=True, capture_output=True,
        )
        pyautogui.hotkey("ctrl", "v")
        time.sleep(2.0)  # let the search bar register the pasted text / dropdown settle

        # Release Ctrl/Alt/Shift in case hotkey left a modifier down
        for vk in (0x11, 0x12, 0x10):
            user32.keybd_event(vk, 0, 0x0002, 0)
        time.sleep(0.05)

        # Send Enter via keybd_event (more reliable than pyautogui.press)
        user32.keybd_event(VK_RETURN, 0, 0, 0)
        time.sleep(0.05)
        user32.keybd_event(VK_RETURN, 0, 0x0002, 0)
        time.sleep(1.0)

    def click_text_via_ocr(
        self, search_text: str, region: tuple[int, int, int, int],
    ) -> bool:
        """Screenshot a region, OCR it, and click the first match of ``search_text``.

        In WeChat 4.x, pressing Enter in the search bar performs a text
        search rather than opening a URL.  The user must click the
        "访问网页" suggestion button to navigate to the article.

        Saves a debug screenshot before running OCR so failures can be
        diagnosed from the output directory.

        Args:
            search_text: Text to locate (e.g. "访问网页").
            region: (left, top, width, height) in screen coordinates.

        Returns:
            True if the text was found and clicked, False otherwise.
        """
        left, top, width, height = region
        img = pyautogui.screenshot(region=(left, top, width, height))

        # Save debug screenshot so we can diagnose OCR failures
        debug_dir = config.OUTPUT_DIR / "debug" / "wechat_pc"
        debug_dir.mkdir(parents=True, exist_ok=True)
        debug_path = debug_dir / f"{self.platform_name}_click_ocr.png"
        img.save(str(debug_path))

        # Use file-based OCR (verified reliable) rather than numpy-array
        # path which can produce different results
        boxes = self.ocr.extract_text_with_boxes(str(debug_path))
        if not boxes:
            logger.warning(
                f"[{self.platform_name}] OCR found no text in region "
                f"({left},{top},{width},{height})"
            )
            return False

        # Log all detected text for debugging
        texts = [b["text"] for b in boxes]
        logger.info(
            f"[{self.platform_name}] OCR found {len(boxes)} items: {texts[:30]}"
        )

        # Find boxes containing the search text
        matches = [b for b in boxes if search_text in b["text"]]
        if not matches:
            logger.warning(
                f"[{self.platform_name}] '{search_text}' not found "
                f"in {len(boxes)} OCR items"
            )
            return False

        # Click the first match
        best = matches[0]
        click_x = left + best["cx"]
        click_y = top + best["cy"]
        logger.info(
            f"[{self.platform_name}] OCR click '{best['text']}' "
            f"at ({click_x}, {click_y})"
        )
        pyautogui.click(click_x, click_y)
        time.sleep(0.5)
        return True

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

    def scroll_one_screen(
        self, section: str, key: str, px_per_click: float = 65.0,
    ) -> None:
        """Scroll one screen-height via spaced wheel events at region center.

        Uses a fixed px-per-click estimate (~65 px/click in WeChat CEF).
        WeChat coalesces rapid wheel events, so we use :meth:`_scroll_spaced`
        to chunk the clicks.
        """
        left, top, width, height = self.resolve_region(section, key)
        cx = left + width // 2
        cy = top + height // 2
        clicks = max(1, round(height / px_per_click))
        logger.info(
            f"[{self.platform_name}] Scroll one screen: {clicks} clicks "
            f"(region={height}px, {px_per_click:.0f} px/click)"
        )
        self._scroll_spaced(clicks, cx, cy)

    def _scroll_spaced(
        self, clicks: int, x: int, y: int,
        chunk: int = 3, delay: float = 0.04,
    ) -> None:
        """Send wheel clicks in spaced chunks.

        pyautogui.scroll(N) fires N events near-instantaneously; Windows
        /CEF coalesce them into one gesture whose distance is capped
        (~one page), so 50 clicks and 200 clicks move the same ~150px.
        Breaking the scroll into small chunks with a tiny delay between
        each makes every chunk a separate gesture, restoring linear
        pixel-vs-click behaviour.
        """
        remaining = clicks
        while remaining > 0:
            n = min(chunk, remaining)
            pyautogui.scroll(-n, x=x, y=y)
            remaining -= n
            if remaining > 0:
                time.sleep(delay)

    # ── Scrollbar manipulation ───────────────────────────────────

    def _find_scrollbar_thumb(
        self, section: str, key: str = "comment_region"
    ) -> tuple[int, int, int, int, int] | None:
        """Locate the scrollbar thumb in the comment region.

        Dynamically detects the scrollbar column band in the rightmost
        24 px of the region by looking for columns whose min pixel value
        drops below 200 (the grey thumb, ~127, vs pure white track, 255).
        Once the scrollbar band is identified, finds the longest
        contiguous dark run (the thumb) within that band.

        Returns (sb_cx, thumb_top, thumb_bottom, track_top, track_bottom)
        in screen coordinates, or None if no thumb is found.
        """
        left, top, width, height = self.resolve_region(section, key)

        # Sample the rightmost 24 px — enough for any DPI / version
        scan_w = min(24, width)
        scan_left = left + width - scan_w

        # Hover to reveal the overlay scrollbar (CEF may auto-hide it)
        pyautogui.moveTo(scan_left + scan_w // 2, top + height // 2, duration=0.1)
        time.sleep(0.25)

        img = pyautogui.screenshot(region=(scan_left, top, scan_w, height))
        gray = img.convert("L")

        # Save a debug strip so we can verify the detection visually
        debug_dir = config.OUTPUT_DIR / "debug" / "wechat_pc"
        debug_dir.mkdir(parents=True, exist_ok=True)
        gray.save(str(debug_dir / f"{self.platform_name}_sb_strip.png"))

        pixels = list(gray.getdata())  # row-major: pixels[y * scan_w + x]

        # ── Step 1: find the scrollbar column band ──
        sb_col_local: list[int] = []
        for x in range(scan_w):
            col_min = min(
                pixels[y * scan_w + x]
                for y in range(0, height, 2)  # sample every other row
            )
            if col_min < 200:  # dark thumb present → this is a scrollbar column
                sb_col_local.append(x)

        if not sb_col_local:
            logger.debug(
                f"[{self.platform_name}] no scrollbar columns found "
                f"(all columns min >= 200)"
            )
            return None

        # True centre of the scrollbar in screen coords
        sb_cx = scan_left + (sb_col_local[0] + sb_col_local[-1]) // 2
        sb_band_w = sb_col_local[-1] - sb_col_local[0] + 1

        # ── Step 2: find the thumb as the longest dark run ──
        # Only consider pixels inside the detected scrollbar band
        row_means = [
            sum(
                pixels[y * scan_w + x]
                for x in range(sb_col_local[0], sb_col_local[-1] + 1)
            ) / sb_band_w
            for y in range(height)
        ]

        best_run = None  # (start, length)
        cur_start = None
        for y, m in enumerate(row_means):
            if m < 200:  # thumb pixel (grey ~127 vs white track ~255)
                if cur_start is None:
                    cur_start = y
            else:
                if cur_start is not None:
                    run_len = y - cur_start
                    if best_run is None or run_len > best_run[1]:
                        best_run = (cur_start, run_len)
                    cur_start = None
        if cur_start is not None:
            run_len = height - cur_start
            if best_run is None or run_len > best_run[1]:
                best_run = (cur_start, run_len)

        if not best_run or best_run[1] < 5:
            logger.debug(
                f"[{self.platform_name}] no scrollbar thumb found "
                f"(no dark run >= 5 px)"
            )
            return None

        thumb_top = top + best_run[0]
        thumb_bottom = thumb_top + best_run[1]
        logger.debug(
            f"[{self.platform_name}] sb_band={sb_col_local[0]}..{sb_col_local[-1]} "
            f"(local), sb_cx={sb_cx}, thumb={best_run[0]}-{best_run[0]+best_run[1]}"
        )
        return sb_cx, thumb_top, thumb_bottom, top, top + height

    def scrollbar_page_down(
        self, section: str, key: str = "comment_region"
    ) -> bool:
        """Page down one screen by clicking the track below the thumb."""
        found = self._find_scrollbar_thumb(section, key)
        if not found:
            logger.debug(f"[{self.platform_name}] no scrollbar thumb found")
            return False
        sb_cx, _thumb_top, thumb_bottom, _track_top, track_bottom = found
        if thumb_bottom + 12 >= track_bottom:
            return False
        pyautogui.click(sb_cx, thumb_bottom + 8)
        time.sleep(0.5)
        return True

    def scrollbar_scroll_fraction(
        self, section: str, key: str = "comment_region",
        fraction: float = 0.67,
    ) -> bool:
        """Drag the scrollbar thumb down by ``fraction`` of its height.

        More controllable than track-clicking (which always scrolls a
        full page).  A fraction of 0.67 scrolls ~⅔ of a viewport.
        """
        found = self._find_scrollbar_thumb(section, key)
        if not found:
            logger.debug(f"[{self.platform_name}] no scrollbar thumb for drag")
            return False
        sb_cx, thumb_top, thumb_bottom, track_top, track_bottom = found
        thumb_h = thumb_bottom - thumb_top
        drag_px = max(1, int(thumb_h * fraction))
        # Cap so the drag doesn't push the thumb past track_bottom
        target_y = min(
            (thumb_top + thumb_bottom) // 2 + drag_px,
            track_bottom - thumb_h // 2 - 1,
        )
        if target_y <= (thumb_top + thumb_bottom) // 2 + 2:
            return False  # too close to bottom, essentially at end
        pyautogui.moveTo(sb_cx, (thumb_top + thumb_bottom) // 2)
        pyautogui.mouseDown()
        time.sleep(0.15)
        pyautogui.moveTo(sb_cx, target_y, duration=0.2)
        time.sleep(0.1)
        pyautogui.mouseUp()
        time.sleep(0.5)
        return True

    def scrollbar_drag_to_top(
        self, section: str, key: str = "comment_region"
    ) -> bool:
        """Drag the scrollbar thumb to the very top — instant scroll-to-top."""
        found = self._find_scrollbar_thumb(section, key)
        if not found:
            return False
        sb_cx, thumb_top, thumb_bottom, track_top, _track_bottom = found
        thumb_cy = (thumb_top + thumb_bottom) // 2
        pyautogui.moveTo(sb_cx, thumb_cy)
        pyautogui.mouseDown()
        time.sleep(0.2)
        pyautogui.moveTo(sb_cx, track_top + 3, duration=0.4)
        pyautogui.mouseUp()
        time.sleep(0.6)
        return True

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

    def screenshot_region_as_image(
        self, section: str, key: str, name: str = ""
    ) -> Image.Image:
        """Screenshot a named relative region, return PIL Image in memory.

        Also saves a debug copy when ``name`` is non-empty.
        """
        left, top, width, height = self.resolve_region(section, key)
        img = pyautogui.screenshot(region=(left, top, width, height))
        if name:
            debug_dir = config.OUTPUT_DIR / "debug" / "wechat_pc"
            debug_dir.mkdir(parents=True, exist_ok=True)
            path = debug_dir / f"{self.platform_name}_{name}.png"
            img.save(str(path))
        return img

    @staticmethod
    def images_similar(
        img1: Image.Image,
        img2: Image.Image,
        threshold: float = 0.02,
    ) -> bool:
        """Return True when two images are nearly identical.

        Converts both to grayscale, computes pixel-wise absolute
        difference, then checks whether the mean difference is below
        ``threshold`` (fraction of 255, default 2% → ~5.1 levels).
        """
        g1 = img1.convert("L")
        g2 = img2.convert("L")
        diff = ImageChops.difference(g1, g2)
        stat = ImageStat.Stat(diff)
        avg_diff = stat.mean[0]  # 0 … 255
        return avg_diff < (threshold * 255)

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

            # Scroll to reveal more hidden buttons (wheel at region center)
            region_left, region_top, region_w, region_h = \
                self.resolve_region(section, key)
            cx = region_left + region_w // 2
            cy = region_top + region_h // 2
            self._scroll_spaced(abs(scroll_clicks), cx, cy)
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

    def reacquire_window(self) -> None:
        """Re-identify the WeChat window from scratch.

        Called before each URL to ensure coordinates are computed against
        the correct window handle and position.  Between URLs the window
        may have been resized (article side-panel open/close), moved, or
        its HWND may have changed (WeChat recycles windows internally).

        Resets the cached HWND, calls ``find_window()`` to re-acquire,
        moves the window onto the primary monitor, and reads the current
        rect.  Does NOT force-resize the window — that would disrupt
        WeChat's internal layout and cause coordinate drift.
        """
        self.window_mgr._hwnd = None
        if not self.window_mgr.find_window():
            raise RuntimeError(
                "微信窗口丢失。请确认微信 PC 客户端仍在运行。"
            )
        self.window_mgr.move_to_primary_screen()
        self._rect = self.window_mgr.get_rect()
        logger.info(
            f"[{self.platform_name}] Re-acquired window: "
            f"({self._rect.left}, {self._rect.top}) "
            f"{self._rect.width}x{self._rect.height}"
        )

