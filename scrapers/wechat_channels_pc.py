# scrapers/wechat_channels_pc.py
"""WeChat Channels (视频号) comment scraper — PC desktop.

Uses relative coordinates and OCR to open 视频号 content in WeChat,
extract comments, and optionally capture video.
"""
import logging
import time

import pyautogui

import config
from scrapers.wechat_pc_base import WechatPcBaseScraper

logger = logging.getLogger(__name__)


class WechatChannelsPcScraper(WechatPcBaseScraper):
    """Scrape comments from WeChat Channels (视频号) via PC desktop.

    Flow:
      1. Activate WeChat window
      2. Click search bar, paste video URL, press Enter
      3. Click "访问网页" to open video
      4. Pause video, click comment button
      5. OCR screenshot comment panel, scroll to load more
      6. Return {"post_content": "", "comments": [...], "video_url": None}
    """

    platform_name = "wechat_channels"

    def scrape(self, url: str) -> dict:
        """Scrape 视频号 comments."""
        # Delay between URLs
        if self._scrape_count > 0:
            import random
            delay = random.uniform(3, 6)
            logger.info(f"[wechat_channels] Waiting {delay:.1f}s before next URL")
            time.sleep(delay)
        self._scrape_count += 1

        try:
            # Step 1: Activate WeChat window
            self.window_mgr.activate()
            self.refresh_rect()
            time.sleep(0.5)

            # Step 2: Open video
            self._open_video(url)

            # Step 3: Pause video
            self._pause_video()

            # Step 4: Open comment panel
            self._open_comment_panel()

            # Step 5: Extract comments
            comments = self._extract_comments()
            logger.info(f"[wechat_channels] Total comments: {len(comments)}")

            # Debug screenshot
            self.take_screenshot("final")

            return {
                "post_content": "",
                "comments": comments,
                "video_url": None,
            }

        except Exception as e:
            logger.error(f"[wechat_channels] Failed to scrape {url}: {e}")
            self.take_screenshot("error")
            raise

    # ── Navigation ───────────────────────────────────────────────

    def _open_video(self, url: str) -> None:
        """Open 视频号 URL via WeChat search bar."""
        # Click search bar
        self.click_key("wechat_main", "search_bar", "搜索栏")
        self.clear_input()

        # Paste URL and search
        self.paste_and_enter(url)
        time.sleep(3)

        # Click search result entry
        self.click_key("wechat_main", "result_open_entry", "搜索结果")
        time.sleep(5)

    def _pause_video(self) -> None:
        """Click the video area to pause playback."""
        self.click_key("channels", "pause_video", "暂停视频")
        time.sleep(1)

    def _open_comment_panel(self) -> None:
        """Click the comment button to open the comment panel."""
        self.click_key("channels", "comment_button", "评论按钮")
        time.sleep(2)

    # ── Comment extraction ───────────────────────────────────────

    def _extract_comments(self, max_scrolls: int = 20) -> list[tuple[str, str]]:
        """OCR extract comments from the comment panel. Scrolls to load more."""
        all_comments = []
        seen = set()
        prev_count = 0
        no_change_count = 0

        for i in range(max_scrolls):
            # Screenshot & OCR the comment panel region
            batch = self.ocr_region(
                "channels", "comment_panel_region", f"comments_{i:02d}"
            )
            for nick, content in batch:
                key = (nick, content[:30])
                if key not in seen:
                    seen.add(key)
                    all_comments.append((nick, content))

            # Check if new comments appeared
            if len(all_comments) == prev_count:
                no_change_count += 1
                if no_change_count >= 3:
                    logger.debug("[wechat_channels] No new comments after 3 scrolls")
                    break
            else:
                no_change_count = 0
            prev_count = len(all_comments)

            # Scroll down in comment panel
            self.scroll_at_key("channels", "scroll_anchor", clicks=-3)
            time.sleep(1.5)

            if i > 0 and i % 5 == 4:
                logger.debug(
                    f"[wechat_channels] Scroll {i+1}/{max_scrolls}, "
                    f"comments: {len(all_comments)}"
                )

        return all_comments
