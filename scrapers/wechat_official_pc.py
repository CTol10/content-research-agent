# scrapers/wechat_official_pc.py
"""WeChat Official Account (公众号) article comment scraper — PC desktop.

Uses relative coordinates and OCR to open articles in the WeChat built-in
browser, extract post content and comments (including replies).
"""
import logging
import time

import pyautogui

import config
from scrapers.wechat_pc_base import WechatPcBaseScraper

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

    def scrape(self, url: str) -> dict:
        """Scrape article content and comments."""
        # Delay between URLs
        if self._scrape_count > 0:
            import random
            delay = random.uniform(5, 8)
            logger.info(f"[wechat] Waiting {delay:.1f}s before next URL")
            time.sleep(delay)
        self._scrape_count += 1

        try:
            # Step 1: Activate WeChat window
            self.window_mgr.activate()
            self.refresh_rect()
            time.sleep(0.5)

            # Step 2: Search and open article
            self._open_article(url)

            # Step 3: Wait for article content to load
            time.sleep(5)

            # Step 4: Scroll to load comments
            self._scroll_to_load_comments()

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
        """Open article URL via WeChat search bar."""
        # Click search bar
        self.click_key("wechat_main", "search_bar", "搜索栏")
        self.clear_input()

        # Paste URL and search
        self.paste_and_enter(url)
        time.sleep(3)

        # Click search result entry (访问网页 or similar)
        self.click_key("wechat_main", "result_open_entry", "搜索结果")
        time.sleep(5)

    # ── Scrolling ────────────────────────────────────────────────

    def _scroll_to_load_comments(self, max_scrolls: int = 20) -> None:
        """Scroll down to load lazy-loaded comments."""
        prev_comment_count = 0
        no_change_count = 0

        for i in range(max_scrolls):
            # Scroll down
            self.scroll_at_key("official", "article_scroll_start", clicks=-3)
            time.sleep(1.5)

            # Screenshot comment region to check progress
            try:
                lines = self.ocr_region_lines(
                    "official", "comment_region", f"scroll_check_{i:02d}"
                )
                current_count = len(lines)
            except Exception:
                current_count = 0

            if current_count == prev_comment_count:
                no_change_count += 1
                if no_change_count >= 3:
                    logger.debug(
                        f"[wechat] No new content after {no_change_count} scrolls"
                    )
                    break
            else:
                no_change_count = 0
            prev_comment_count = current_count

            logger.debug(
                f"[wechat] Scroll {i+1}/{max_scrolls}, OCR lines: {current_count}"
            )

    # ── Comment extraction ───────────────────────────────────────

    def _extract_comments(self) -> list[tuple[str, str]]:
        """OCR extract comments from the comment region."""
        comments = []
        seen = set()

        # Multiple scroll + OCR cycles to capture all comments
        prev_count = 0
        no_change_count = 0

        for i in range(15):
            batch = self.ocr_region(
                "official", "comment_region", f"comments_{i:02d}"
            )
            for nick, content in batch:
                key = (nick, content[:30])
                if key not in seen:
                    seen.add(key)
                    comments.append((nick, content))

            if len(comments) == prev_count:
                no_change_count += 1
                if no_change_count >= 3:
                    break
            else:
                no_change_count = 0
            prev_count = len(comments)

            # Scroll to load more
            self.scroll_at_key("official", "article_scroll_start", clicks=-3)
            time.sleep(1.5)

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
