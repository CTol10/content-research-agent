# scrapers/wechat_official_pc.py
"""WeChat Official Account (公众号) article comment scraper — PC desktop.

Uses relative coordinates and OCR to open articles in the WeChat built-in
browser, extract post content and comments (including replies).
"""
import logging
import time

import pyautogui

import config
from scrapers.wechat.v411.base import WechatPcBaseScraper

logger = logging.getLogger(__name__)


class WechatOfficialPcScraper(WechatPcBaseScraper):
    """Scrape comments from WeChat Official Account articles via PC desktop.

    Flow:
      1. Activate WeChat window
      2. Click search bar, paste article URL, press Enter
      3. Click search result to open article
      4. Scroll to load all comments
      5. Screenshot & OCR extract comments
      6. Return {"post_content": ..., "comments": [...]}
    """

    platform_name = "wechat"

    async def scrape(self, url: str) -> dict:
        """Scrape article content and comments."""
        # Delay between URLs
        if self._scrape_count > 0:
            import random
            delay = random.uniform(5, 8)
            logger.info(f"[wechat] Waiting {delay:.1f}s before next URL")
            time.sleep(delay)
        self._scrape_count += 1

        try:
            # Step 1: Activate WeChat window (must be foreground for paste/Enter)
            if not self.window_mgr.activate():
                raise RuntimeError(
                    "无法将微信窗口切换到前台。请关闭其他可能拦截焦点的窗口后重试。"
                )
            # Only re-identify window on first URL (after increment, count==1).
            # Subsequent URLs reuse the first rect to avoid drift.
            if self._scrape_count == 1:
                self.reacquire_window()
            time.sleep(0.8)  # let WeChat settle as foreground before clicking

            # Step 2: Search and open article
            self._open_article(url)

            # Step 3: Wait for article content to load
            time.sleep(5)

            # 文章打开后微信窗口会在右侧弹出侧边面板，窗口变宽，需要刷新尺寸
            self.refresh_rect()
            logger.debug(
                f"[wechat] After article open, window: "
                f"({self._rect.left}, {self._rect.top}) "
                f"{self._rect.width}x{self._rect.height}"
            )

            # Step 3.5: Click comment icon to expand comment section
            self.click_key("official", "comment_icon", "评论区图标")
            time.sleep(1.5)

            # Step 4: Scroll + expand in one pass
            self._scroll_and_expand_comments()

            # Step 5: Extract comments via OCR
            comments = self._extract_comments()
            logger.info(f"[wechat] Total comments extracted: {len(comments)}")

            # Step 6: Extract article content
            post_content = self._extract_post_content()

            # Debug screenshot
            self.take_screenshot("final")

            return {"post_content": post_content, "comments": comments}

        except Exception as e:
            logger.error(f"[wechat] Failed to scrape {url}: {e}")
            self.take_screenshot("error")
            raise

    # ── Navigation ───────────────────────────────────────────────

    def _open_article(self, url: str) -> None:
        """Open article URL via WeChat search bar.

        WeChat 4.x requires TWO clicks on the search bar to open the
        search interface.
        """
        # Two clicks needed — first click opens the search sub-page,
        # second click focuses the input field
        self.click_key("wechat_main", "search_bar", "搜索栏")
        time.sleep(0.4)
        self.click_key("wechat_main", "search_bar", "搜索栏")
        time.sleep(0.4)
        self.clear_input()

        # Paste URL and press Enter (uses keybd_event)
        self.paste_and_enter(url)
        time.sleep(2)

        # Click the "访问网页" button — use OCR to locate it because the
        # search drop-down position varies with window size/layout.
        # Search region: top-left quadrant of the window (roughly top half)
        region_left = self._rect.left
        region_top = self._rect.top
        region_w = self._rect.width
        region_h = self._rect.height // 2
        if not self.click_text_via_ocr(
            "访问网页", (region_left, region_top, region_w, region_h)
        ):
            # Fallback: try the calibrated coordinate
            logger.warning("[wechat] OCR failed to find '访问网页', using calibrated coord")
            self.click_key("wechat_main", "result_open_entry", "访问网页")
        time.sleep(5)

    # ── Scrolling + Expand ─────────────────────────────────────

    def _scroll_and_expand_comments(self, max_scrolls: int = 40) -> None:
        """Scroll through comments and click '展开/x条回复' buttons.

        Uses the scrollbar (page-down clicks on the track) to scroll one
        screen per iteration, and screenshot pixel-diff to detect bottom.
        If no scrollbar is found, just handles expand buttons on the
        first screen and returns (no scrolling needed for short pages).
        """
        region_left, region_top, region_w, region_h = \
            self.resolve_region("official", "comment_region")
        focus_x = region_left + region_w // 2
        focus_y = region_top + region_h // 2

        # One click to focus the comment panel
        pyautogui.click(focus_x, focus_y)
        time.sleep(0.5)

        # Check whether a scrollbar exists at all
        has_scrollbar = self._find_scrollbar_thumb("official", "comment_region") is not None
        if not has_scrollbar:
            logger.info("[wechat] No scrollbar detected — single-screen page")

        total_expanded = 0
        prev_img = self.screenshot_region_as_image(
            "official", "comment_region", "expand_00_before"
        )

        # Check the FIRST screen for expand buttons BEFORE any scroll
        buttons = self.find_expand_buttons(
            "official", "comment_region", "expand_first_screen"
        )
        if buttons:
            logger.info(f"[wechat] First screen: {len(buttons)} expand buttons")
            for btn in buttons:
                self.click_point(
                    btn["screen_x"], btn["screen_y"],
                    f"展开:{btn['text']}",
                )
                time.sleep(0.3)
            total_expanded += len(buttons)
            time.sleep(0.8)

        if not has_scrollbar:
            logger.info(
                f"[wechat] Single-screen page, {total_expanded} expanded"
            )
            return

        for i in range(max_scrolls):
            # Scroll ~2/3 screen via thumb drag (more overlap = less missed)
            self.scrollbar_scroll_fraction("official", "comment_region", 2/3)
            time.sleep(0.8)

            curr_img = self.screenshot_region_as_image(
                "official", "comment_region", f"expand_{i:02d}"
            )

            # Pixel-diff: has the view actually changed?
            if self.images_similar(prev_img, curr_img):
                logger.info(
                    f"[wechat] Scroll {i+1}: no visual change — "
                    f"reached bottom ({total_expanded} expanded)"
                )
                break

            # Find and click expand buttons in current viewport
            buttons = self.find_expand_buttons(
                "official", "comment_region", f"expand_{i:02d}"
            )
            if buttons:
                logger.info(
                    f"[wechat] Scroll {i+1}: {len(buttons)} expand buttons"
                )
                for btn in buttons:  # bottom-to-top (already sorted)
                    self.click_point(
                        btn["screen_x"], btn["screen_y"],
                        f"展开:{btn['text']}",
                    )
                    time.sleep(0.3)
                total_expanded += len(buttons)
                time.sleep(0.8)

            logger.debug(
                f"[wechat] Scroll {i+1}/{max_scrolls}: "
                f"expanded={total_expanded}"
            )
            prev_img = curr_img

        # After reaching bottom, wheel-scroll to trigger any
        # final lazy-loaded content
        logger.info("[wechat] Scrollbar reached bottom, wheel-scrolling 1000")
        pyautogui.moveTo(focus_x, focus_y)
        pyautogui.scroll(-1000)
        time.sleep(1.5)

        # Screenshot and check for expand buttons after wheel scroll
        buttons = self.find_expand_buttons(
            "official", "comment_region", "expand_wheel"
        )
        if buttons:
            logger.info(f"[wechat] Wheel expand: {len(buttons)} buttons")
            for btn in buttons:
                self.click_point(
                    btn["screen_x"], btn["screen_y"],
                    f"展开:{btn['text']}",
                )
                time.sleep(0.3)
            total_expanded += len(buttons)

        logger.info(
            f"[wechat] Expand pass done: {total_expanded} total expanded"
        )

    # ── Comment extraction ───────────────────────────────────────

    def _scroll_to_top(self) -> None:
        """Scroll the comment panel back to the top.

        Drags the scrollbar thumb to the very top — one action, instant.
        Falls back to nothing if the scrollbar can't be found (the
        caller's image-diff will still work).
        """
        self.scrollbar_drag_to_top("official", "comment_region")

    def _extract_comments(self) -> list[tuple[str, str]]:
        """OCR extract comments from the comment region.

        Scrolls back to the top first, then walks down screen-by-screen,
        using screenshot pixel-diff to detect when we've reached the
        bottom (no new visual content after scrolling).

        Cross-screen comment fragments (from long comments that span a
        viewport boundary) are spliced together by
        :func:`~scrapers.wechat_ocr.merge_comment_fragments` as a
        post-processing pass.
        """
        from scrapers.wechat.ocr import merge_comment_fragments

        region_left, region_top, region_w, region_h = \
            self.resolve_region("official", "comment_region")
        focus_x = region_left + region_w // 2
        focus_y = region_top + region_h // 2

        # ── Scroll back to top ──
        logger.debug("[wechat] Scrolling back to top for extraction pass")
        self._scroll_to_top()
        time.sleep(0.5)

        # ── Walk down, OCR each screen ──
        raw: list[tuple[str, str]] = []  # collect all, merge later

        # Check whether a scrollbar exists
        has_scrollbar = self._find_scrollbar_thumb("official", "comment_region") is not None

        if not has_scrollbar:
            logger.info("[wechat] No scrollbar — OCR first screen only")
            batch = self.ocr_region(
                "official", "comment_region", "extract_single"
            )
            raw.extend(batch)
            logger.info(f"[wechat] Single-screen OCR: {len(batch)} pairs")
            return merge_comment_fragments(raw)

        prev_img = self.screenshot_region_as_image(
            "official", "comment_region", "extract_00"
        )

        for i in range(40):
            # OCR the current viewport
            batch = self.ocr_region(
                "official", "comment_region", f"extract_{i:02d}"
            )
            raw.extend(batch)

            logger.debug(
                f"[wechat] Screen {i+1}: {len(batch)} OCR pairs, "
                f"{len(raw)} total raw"
            )

            # Scroll ~2/3 screen via thumb drag (more overlap = less missed)
            self.scrollbar_scroll_fraction("official", "comment_region", 2/3)
            time.sleep(0.8)

            curr_img = self.screenshot_region_as_image(
                "official", "comment_region", f"extract_{i+1:02d}"
            )

            # Pixel-diff: has the view changed?
            if self.images_similar(prev_img, curr_img):
                # At scrollbar bottom — wheel-scroll to reveal any
                # remaining lazy-loaded content
                logger.info("[wechat] OCR bottom, wheel-scrolling 1000")
                pyautogui.moveTo(focus_x, focus_y)
                pyautogui.scroll(-1000)
                time.sleep(1.5)

                # Final OCR pass
                batch = self.ocr_region(
                    "official", "comment_region", f"extract_{i+2:02d}_final"
                )
                raw.extend(batch)
                logger.info(
                    f"[wechat] Final OCR: {len(batch)} pairs, "
                    f"{len(raw)} total raw"
                )
                break

            prev_img = curr_img

        # Post-process: splice cross-screen fragments, dedup
        comments = merge_comment_fragments(raw)
        return comments

    # ── Content extraction ───────────────────────────────────────

    def _extract_post_content(self) -> str:
        """Extract article content via OCR from the content region.

        For phase 1, we OCR the upper portion of the article.
        Future: CDP DOM extraction for richer text.
        """
        try:
            lines = self.ocr_region_lines(
                "official", "comment_region", "post_content"
            )
            # Filter out common UI text
            filtered = [
                l for l in lines
                if not l.startswith("评论")
                and l not in ("回复", "视频号", "搜索")
                and len(l) > 2
            ]
            return "\n".join(filtered[:50])  # First 50 lines
        except Exception as e:
            logger.warning(f"[wechat] Content extraction failed: {e}")
            return ""
