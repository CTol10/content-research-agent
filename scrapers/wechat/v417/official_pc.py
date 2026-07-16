# scrapers/wechat/v417/official_pc.py
"""WeChat 4.1.7 Official Account (公众号) scraper — dual-window.

4.1.7 opens search results + article content in a SEPARATE
Chrome_WidgetWin_0 popup window, not as a side panel.  The main
Qt window is used only for the search bar; everything else
(search results "访问网页" + article comments) targets the popup.
"""
import logging
import time

import pyautogui

import config
from scrapers.wechat.v417.base import WechatPcBaseScraperV417

logger = logging.getLogger(__name__)


class WechatOfficialPcScraperV417(WechatPcBaseScraperV417):
    """Scrape 公众号 comments via 4.1.7 dual-window flow."""

    platform_name = "wechat_v417"

    async def scrape(self, url: str) -> dict:
        if self._scrape_count > 0:
            import random
            delay = random.uniform(5, 8)
            logger.info(f"[wechat_v417] Waiting {delay:.1f}s before next URL")
            time.sleep(delay)
        self._scrape_count += 1

        try:
            # Step 1: Activate main window
            if not self.window_mgr.activate():
                raise RuntimeError("无法激活微信主窗口")
            if self._scrape_count == 1:
                # re-find on first URL
                self.window_mgr.find_main()
            time.sleep(0.8)

            # Step 2: Search + open article in main window
            self._open_article(url)

            # Step 3: Wait for popup to appear
            if not self.window_mgr.wait_for_popup(timeout=15):
                raise RuntimeError("文章弹窗未出现（4.1.7 预期 Chrome_WidgetWin_0 弹窗）")

            # Step 4: Activate popup and click comment icon
            self.window_mgr.activate_popup()
            time.sleep(1.0)
            self.click_key("official", "comment_icon", "评论区图标")
            time.sleep(1.5)

            # Step 5: Scroll + expand comments (same logic as 4.1.11)
            self._scroll_and_expand_comments()

            # Step 6: Extract comments via OCR
            comments = self._extract_comments()
            logger.info(f"[wechat_v417] Total comments: {len(comments)}")

            # Step 7: Extract post content
            post_content = self._extract_post_content()

            self.take_screenshot("final")

            return {"post_content": post_content, "comments": comments}

        except Exception as e:
            logger.error(f"[wechat_v417] Failed to scrape {url}: {e}")
            self.take_screenshot("error")
            raise

    # ── Navigation ─────────────────────────────────────────────────

    def _open_article(self, url: str) -> None:
        """Open article URL via main window search bar (4.1.7 style).

        4.1.7 flow: search in main window → results popup appears →
        "访问网页" is IN the popup → click → article loads in same popup.
        """
        # Two clicks on search bar (main window)
        self.click_key("wechat_main", "search_bar", "搜索栏")
        time.sleep(0.4)
        self.click_key("wechat_main", "search_bar", "搜索栏")
        time.sleep(0.4)
        self.clear_input()

        self.paste_and_enter(url)
        time.sleep(2)

        # Search results popup appears — wait for it
        if not self.window_mgr.wait_for_popup(timeout=10):
            raise RuntimeError("搜索结果弹窗未出现")

        # Click "访问网页" IN THE POPUP — OCR first, fallback coord
        popup_rect = self.window_mgr.get_popup_rect()
        region = (
            popup_rect.left, popup_rect.top,
            popup_rect.width, popup_rect.height // 2,
        )
        if not self.click_text_via_ocr("访问网页", region):
            logger.warning("[wechat_v417] OCR missed '访问网页', trying coord")
            self.click_key("official", "result_open_entry", "访问网页(兜底)")

        # Article loads in the same popup — wait a bit
        time.sleep(2)

    # ── Scrolling + expand ─────────────────────────────────────────

    def _scroll_and_expand_comments(self, max_scrolls: int = 40) -> None:
        """Scroll through comments in the popup, clicking expand buttons."""
        region_left, region_top, region_w, region_h = \
            self.resolve_region("official", "comment_region")
        # Click at very top-right corner to avoid hitting links/images
        # in the comment content area (focus transfer only)
        focus_x = region_left + region_w - 10
        focus_y = region_top + 10

        pyautogui.click(focus_x, focus_y)
        time.sleep(0.5)

        # Check scrollbar
        has_scrollbar = (
            self._find_scrollbar_thumb("official", "comment_region") is not None
        )
        if not has_scrollbar:
            logger.info("[wechat_v417] No scrollbar — single-screen page")

        total_expanded = 0
        prev_img = self.screenshot_region_as_image(
            "official", "comment_region", "expand_00_before"
        )

        # First screen expand buttons before any scroll
        buttons = self.find_expand_buttons(
            "official", "comment_region", "expand_first_screen"
        )
        if buttons:
            logger.info(f"[wechat_v417] First screen: {len(buttons)} expand buttons")
            for btn in buttons:
                self.click_point(btn["screen_x"], btn["screen_y"], f"展开:{btn['text']}")
                time.sleep(0.3)
            total_expanded += len(buttons)
            time.sleep(0.8)

        if not has_scrollbar:
            logger.info(f"[wechat_v417] Single-screen, {total_expanded} expanded")
            return

        for i in range(max_scrolls):
            self.scrollbar_scroll_fraction("official", "comment_region", 2 / 3)
            time.sleep(0.8)

            curr_img = self.screenshot_region_as_image(
                "official", "comment_region", f"expand_{i:02d}"
            )

            if self.images_similar(prev_img, curr_img):
                logger.info(
                    f"[wechat_v417] Scroll {i+1}: no change — "
                    f"reached bottom ({total_expanded} expanded)"
                )
                break

            buttons = self.find_expand_buttons(
                "official", "comment_region", f"expand_{i:02d}"
            )
            if buttons:
                logger.info(f"[wechat_v417] Scroll {i+1}: {len(buttons)} expand buttons")
                for btn in buttons:
                    self.click_point(btn["screen_x"], btn["screen_y"], f"展开:{btn['text']}")
                    time.sleep(0.3)
                total_expanded += len(buttons)
                time.sleep(0.8)

            prev_img = curr_img

        # Wheel-scroll at bottom to trigger final lazy content
        logger.info("[wechat_v417] Bottom reached, wheel-scrolling 1000")
        pyautogui.moveTo(focus_x, focus_y)
        pyautogui.scroll(-1000)
        time.sleep(1.5)

        buttons = self.find_expand_buttons(
            "official", "comment_region", "expand_wheel"
        )
        if buttons:
            logger.info(f"[wechat_v417] Wheel expand: {len(buttons)} buttons")
            for btn in buttons:
                self.click_point(btn["screen_x"], btn["screen_y"], f"展开:{btn['text']}")
                time.sleep(0.3)
            total_expanded += len(buttons)

        logger.info(f"[wechat_v417] Expand done: {total_expanded} total")

    # ── Comment extraction ─────────────────────────────────────────

    def _extract_comments(self) -> list[tuple[str, str]]:
        """OCR extract comments, scroll back to top first."""
        from scrapers.wechat.ocr import merge_comment_fragments

        region_left, region_top, region_w, region_h = \
            self.resolve_region("official", "comment_region")
        focus_x = region_left + region_w // 2
        focus_y = region_top + region_h // 2

        # Scroll to top
        logger.debug("[wechat_v417] Scrolling to top for extraction")
        self.scrollbar_drag_to_top("official", "comment_region")
        time.sleep(0.5)

        raw: list[tuple[str, str]] = []
        has_scrollbar = (
            self._find_scrollbar_thumb("official", "comment_region") is not None
        )

        if not has_scrollbar:
            batch = self.ocr_region("official", "comment_region", "extract_single")
            raw.extend(batch)
            logger.info(f"[wechat_v417] Single-screen OCR: {len(batch)} pairs")
            return merge_comment_fragments(raw)

        prev_img = self.screenshot_region_as_image(
            "official", "comment_region", "extract_00"
        )

        for i in range(40):
            batch = self.ocr_region("official", "comment_region", f"extract_{i:02d}")
            raw.extend(batch)
            logger.debug(
                f"[wechat_v417] Screen {i+1}: {len(batch)} OCR pairs, "
                f"{len(raw)} total raw"
            )

            self.scrollbar_scroll_fraction("official", "comment_region", 2 / 3)
            time.sleep(0.8)

            curr_img = self.screenshot_region_as_image(
                "official", "comment_region", f"extract_{i+1:02d}"
            )

            if self.images_similar(prev_img, curr_img):
                logger.info("[wechat_v417] OCR bottom, wheel-scrolling 1000")
                pyautogui.moveTo(focus_x, focus_y)
                pyautogui.scroll(-1000)
                time.sleep(1.5)
                batch = self.ocr_region(
                    "official", "comment_region", f"extract_{i+2:02d}_final"
                )
                raw.extend(batch)
                logger.info(f"[wechat_v417] Final OCR: {len(batch)} pairs")
                break

            prev_img = curr_img

        return merge_comment_fragments(raw)

    def _extract_post_content(self) -> str:
        """Extract article content via OCR from the popup."""
        try:
            lines = self.ocr_region_lines("official", "comment_region", "post_content")
            filtered = [
                l for l in lines
                if not l.startswith("评论")
                and l not in ("回复", "视频号", "搜索")
                and len(l) > 2
            ]
            return "\n".join(filtered[:50])
        except Exception as e:
            logger.warning(f"[wechat_v417] Content extraction failed: {e}")
            return ""

    # ── Scrollbar helpers (aligned with 4.1.11 base) ────────────────

    def _find_scrollbar_thumb(self, section: str, key: str):
        """Locate scrollbar thumb in the region's rightmost 24px.

        Hovers first to reveal CEF overlay scrollbars that auto-hide.
        Returns (sb_cx, thumb_top, thumb_bottom, track_top, track_bottom)
        in screen coords, or None.
        """
        import numpy as np

        left, top, width, height = self.resolve_region(section, key)

        # Sample rightmost 24 px of the region
        scan_w = min(24, width)
        scan_left = left + width - scan_w

        # Hover to reveal overlay scrollbar (CEF auto-hides it)
        pyautogui.moveTo(scan_left + scan_w // 2, top + height // 2, duration=0.1)
        time.sleep(0.25)

        img = pyautogui.screenshot(region=(scan_left, top, scan_w, height))
        gray = np.array(img.convert("L"))

        # Save debug strip for visual verification
        debug_dir = config.OUTPUT_DIR / "debug" / "wechat_pc"
        debug_dir.mkdir(parents=True, exist_ok=True)
        from PIL import Image as PILImage
        PILImage.fromarray(gray).save(
            str(debug_dir / f"{self.platform_name}_sb_strip.png"))

        col_min = gray.min(axis=1)
        dark_mask = col_min < 200
        if not dark_mask.any():
            return None

        # Find longest continuous dark run (the thumb)
        runs = []
        start = None
        for y in range(len(dark_mask)):
            if dark_mask[y] and start is None:
                start = y
            elif not dark_mask[y] and start is not None:
                runs.append((start, y - 1))
                start = None
        if start is not None:
            runs.append((start, len(dark_mask) - 1))

        if not runs:
            return None

        best = max(runs, key=lambda r: r[1] - r[0])
        thumb_top_rel, thumb_bottom_rel = best
        track_top = top
        track_bottom = top + height
        sb_cx = scan_left + scan_w // 2
        thumb_top_abs = top + thumb_top_rel
        thumb_bottom_abs = top + thumb_bottom_rel

        return (sb_cx, thumb_top_abs, thumb_bottom_abs, track_top, track_bottom)

    def scrollbar_scroll_fraction(self, section: str, key: str, fraction: float):
        """Drag scrollbar thumb down by ``fraction`` of thumb height."""
        found = self._find_scrollbar_thumb(section, key)
        if not found:
            logger.debug("[wechat_v417] no scrollbar thumb for drag")
            return
        sb_cx, thumb_top, thumb_bottom, track_top, track_bottom = found
        thumb_h = thumb_bottom - thumb_top
        drag_px = max(1, int(thumb_h * fraction))
        target_y = min(
            (thumb_top + thumb_bottom) // 2 + drag_px,
            track_bottom - thumb_h // 2 - 1,
        )
        if target_y <= (thumb_top + thumb_bottom) // 2 + 2:
            return  # too close to bottom

        pyautogui.moveTo(sb_cx, (thumb_top + thumb_bottom) // 2)
        pyautogui.mouseDown()
        time.sleep(0.15)
        pyautogui.moveTo(sb_cx, target_y, duration=0.2)
        time.sleep(0.1)
        pyautogui.mouseUp()
        time.sleep(0.5)

    def scrollbar_drag_to_top(self, section: str, key: str):
        """Drag scrollbar thumb to the very top of the track."""
        found = self._find_scrollbar_thumb(section, key)
        if not found:
            return
        sb_cx, thumb_top, thumb_bottom, track_top, _track_bottom = found
        thumb_cy = (thumb_top + thumb_bottom) // 2
        pyautogui.moveTo(sb_cx, thumb_cy)
        pyautogui.mouseDown()
        time.sleep(0.2)
        pyautogui.moveTo(sb_cx, track_top + 3, duration=0.4)
        pyautogui.mouseUp()
        time.sleep(0.6)
