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

            # Step 5: 滚轮滚动展开 + OCR 提取评论（视频号同款，不依赖悬浮滚动条）
            comments = self._extract_comments()
            logger.info(f"[wechat_v417] Total comments: {len(comments)}")

            # Step 7: 抓正文 —— OCR 评论后用 web 直取(裸 HTTP)替代 OCR 取正文
            # （公众号文章 HTML 服务端渲染，无需 cookie/元宝/微信客户端）
            post_content = self._fetch_post_content_online(url)

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

    # ── Comment extraction (wheel-scroll, 视频号同款) ────────────────

    def _scroll_anchor(self) -> tuple[int, int]:
        """滚动锚点：评论区打开后，弹窗右三分之一（评论列表区域）。

        评论面板是弹窗右侧的滚动列表；右三分之一远离正文/图片/链接按钮，
        滚轮事件能安全落在评论列表上。垂直取评论区域中心。
        """
        popup = self.window_mgr.get_popup_rect()
        _, region_top, _, region_h = \
            self.resolve_region("official", "comment_region")
        anchor_x = popup.left + popup.width * 5 // 6
        anchor_y = region_top + region_h // 2
        return anchor_x, anchor_y

    def _extract_comments(
        self, max_scrolls: int = 80, scroll_fraction: float = 0.67,
    ) -> list[tuple[str, str]]:
        """滚轮滚动展开 + OCR 提取评论（视频号同款单循环）。

        每轮：点展开按钮 → OCR 当前屏 → 滚轮滚 2/3 屏 → 截图 diff 判到底。
        不再依赖 CEF 悬浮滚动条（窗口失焦/未显示时会拖不动，只抓到第一屏）。
        """
        from scrapers.wechat.ocr import merge_comment_fragments

        raw: list[tuple[str, str]] = []
        no_change_count = 0
        total_expanded = 0

        region_left, region_top, region_w, region_h = \
            self.resolve_region("official", "comment_region")
        cx, cy = self._scroll_anchor()

        # 聚焦评论面板（点右上角空白处，避开链接/图片）
        pyautogui.moveTo(cx, cy, duration=0.1)
        time.sleep(0.2)
        focus_x = region_left + region_w - 10
        focus_y = region_top + 10
        pyautogui.click(focus_x, focus_y)
        time.sleep(0.3)

        # 实测每格滚轮像素，确定每步滚轮量
        px_per_click = self._measure_px_per_click(
            region_left, region_top, region_w, region_h, cx, cy,
        )
        target_px = round(region_h * scroll_fraction)
        scroll_clicks = max(1, round(target_px / px_per_click)) * 30
        logger.info(
            f"[wechat_v417] Scroll step: {scroll_clicks} clicks "
            f"(target={target_px}px, measured={px_per_click:.1f} px/click)"
        )

        prev_img = self.screenshot_region_as_image(
            "official", "comment_region", "scroll_00_before"
        )

        for i in range(max_scrolls):
            # 展开回复
            buttons = self.find_expand_buttons(
                "official", "comment_region", f"expand_{i:02d}"
            )
            if buttons:
                logger.info(
                    f"[wechat_v417] Scroll {i+1}: {len(buttons)} expand buttons"
                )
                for btn in buttons:
                    self.click_point(
                        btn["screen_x"], btn["screen_y"], f"展开:{btn['text']}"
                    )
                    time.sleep(0.3)
                total_expanded += len(buttons)
                time.sleep(0.8)
                no_change_count = 0  # 展开会新增内容，重置到底计数

            batch = self.ocr_region(
                "official", "comment_region", f"comments_{i:02d}"
            )
            raw.extend(batch)

            if len(batch) == 0:
                no_change_count += 1
                if no_change_count >= 5:
                    break
            else:
                no_change_count = 0

            # 滚轮滚动（锚点在弹窗右三分之一，评论列表内；单次发送，速度与视频号一致）
            pyautogui.scroll(-scroll_clicks, x=cx, y=cy)
            time.sleep(1.5)

            # 到底检测：滚动后截图与上一屏几乎一致 = 到底
            curr_img = self.screenshot_region_as_image(
                "official", "comment_region", f"scroll_{i+1:02d}_after"
            )
            if self.images_similar(prev_img, curr_img):
                logger.info(
                    f"[wechat_v417] Scroll {i+1}: no change — "
                    f"reached bottom ({total_expanded} expanded)"
                )
                break
            prev_img = curr_img

            if i > 0 and i % 5 == 4:
                logger.debug(
                    f"[wechat_v417] Scroll {i+1}/{max_scrolls}, "
                    f"raw pairs: {len(raw)}, expanded: {total_expanded}"
                )

        logger.info(f"[wechat_v417] Expand done: {total_expanded} total")
        return merge_comment_fragments(raw)

    def _fetch_post_content_online(self, url: str) -> str:
        """web 直取公众号正文（裸 HTTP，无需 cookie/元宝/微信客户端）。

        OCR 评论完成后调用；命中验证页/异常返回 ""。比 OCR `_extract_post_content`
        （只取评论区前 50 行）拿到的是完整干净正文。
        """
        try:
            from scrapers.wechat.official_article_fetcher import fetch_article_body
            data = fetch_article_body(url)
            if not data:
                logger.warning(f"[wechat_v417] web 直取正文为空: {url[:60]}")
                return ""
            logger.info(
                f"[wechat_v417] 正文 {len(data['body'])}字, author={data.get('author','')[:16]!r}"
            )
            return data.get("body", "")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[wechat_v417] web 直取正文失败: {e}")
            return ""

