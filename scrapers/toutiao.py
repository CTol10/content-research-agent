# scrapers/toutiao.py
import asyncio
import logging
from urllib.parse import urlparse

import config
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class ToutiaoScraper(BaseScraper):
    platform_name = "toutiao"
    CDP_PORT = 9236

    def _get_login_url(self) -> str:
        return "https://www.toutiao.com"

    async def scrape(self, url: str) -> dict:
        """Scrape comments from a Toutiao article/video URL.

        Returns a flat list of (nickname, comment_text) tuples.
        """
        context = await self.new_context()
        page = await context.new_page()

        try:
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            self._setup_video_capture(page)
            await self._install_media_pause_guard(page)
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await self._pause_video(page)
            await asyncio.sleep(3)

            await self._check_video_element(page)
            await self._try_extract_video_from_dom(page)

            # Extract post content
            post_content = await self._extract_post_content(page)

            # Click comment button to open comment panel
            await self._click_comment_tab(page)
            await asyncio.sleep(2)

            # Step 1: Load all top-level comments by clicking "load-more-btn"
            await self._load_all_comments(page)

            # Step 2: Expand all replies
            await self._expand_all_replies(page)

            # Extract all comments
            comments = await self._extract_all_comments(page)
            logger.info(f"[toutiao] Total comments extracted: {len(comments)}")

            # Debug screenshot
            debug_dir = config.OUTPUT_DIR / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            parsed_url = urlparse(url)
            serial = parsed_url.path.rstrip("/").split("/")[-1] or "toutiao"
            serial = config.sanitize_filename(serial, max_len=40)
            await page.screenshot(path=str(debug_dir / f"toutiao_{serial}_final.png"), full_page=False)

            return {"post_content": post_content, "comments": comments, "video_url": self._get_primary_video_url(), "audio_url": self._get_primary_audio_url()}

        except Exception as e:
            logger.error(f"[toutiao] Failed to scrape {url}: {e}")
            raise
        finally:
            await page.close()

    # ── Core scraping logic ──────────────────────────────────────────

    async def _click_comment_tab(self, page):
        """Click the comment button to open comment panel."""
        selectors = [
            "button.video-action-button.comment",  # video page
            '[aria-label*="打开评论面板"]',
            ".detail-interaction-comment",
            '[aria-label*="评论"]',
            '[class*="comment-btn"]',
        ]
        for selector in selectors:
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0 and await btn.is_visible():
                    await btn.click()
                    logger.info(f"[toutiao] Clicked comment button: {selector}")
                    return
            except Exception:
                continue
        logger.warning("[toutiao] Could not find comment button")

    async def _extract_post_content(self, page) -> str:
        """Extract post title/content from the page."""
        content = await page.evaluate("""
            () => {
                const normalize = (text) => text
                    .replace(/\\u00a0/g, ' ')
                    .replace(/[ \\t\\f\\v]+/g, ' ')
                    .replace(/ *\\n+ */g, '\\n')
                    .trim();

                // Primary: h1 tag (video/article title)
                const h1 = document.querySelector('h1');
                if (h1) {
                    let text = normalize(h1.textContent || '');
                    // Strip leading "展开" button text
                    if (text.startsWith('展开')) text = text.substring(2).trim();
                    if (text.length >= 2) return text;
                }

                // Fallback: video info area
                const info = document.querySelector('.video-info-detail, [class*="video-info"], [class*="article-title"]');
                if (info) {
                    const rect = info.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        let text = normalize(info.textContent || '');
                        if (text.startsWith('展开')) text = text.substring(2).trim();
                        // Strip trailing noise: play count, date
                        text = text.replace(/播放\s*[\d.]+万?/g, '').replace(/发布于\s*[\d-]+\s*[\d:]+/g, '').trim();
                        if (text.length >= 2) return text;
                    }
                }

                return '';
            }
        """)
        if not content:
            logger.info("[toutiao] No post content found, using [无正文]")
            return "[无正文]"
        return content

    async def _pause_video(self, page):
        """Pause video playback by clicking the pause button."""
        media_state = await self._pause_all_media(page)
        if media_state.get("count", 0) <= 0:
            return
        if media_state.get("playing", 0) <= 0:
            logger.info("[toutiao] Media pause guard stopped autoplay")
            return

        # Click the pause button: button.xg-icon-pause
        try:
            pause_btn = page.locator("button.xg-icon-pause").first
            if await pause_btn.count() > 0 and await pause_btn.is_visible():
                await pause_btn.click()
                logger.info("[toutiao] Clicked pause button")
                await asyncio.sleep(0.5)
                await self._pause_all_media(page)
                return
        except Exception:
            pass

        # Fallback: try to click on the video element center
        video_info = media_state.get("target")

        if video_info and not video_info.get('paused'):
            await page.mouse.click(video_info['x'], video_info['y'])
            logger.info(f"[toutiao] Clicked video at ({video_info['x']}, {video_info['y']}) to pause")
            await asyncio.sleep(0.5)
            await self._pause_all_media(page)

    async def _load_all_comments(self, page, max_clicks=100):
        """Click 'load-more-btn' repeatedly to load all top-level comments."""
        stuck_count = 0
        for i in range(max_clicks):
            # Find and click load-more button within the dialog
            clicked = await page.evaluate("""
                () => {
                    const body = document.querySelector('.ttp-drawer .body');
                    if (!body) return false;
                    const btn = body.querySelector('.load-more-btn');
                    if (!btn) return false;
                    const rect = btn.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) return false;
                    // Only scroll into view if button is outside visible area
                    if (rect.top < -100 || rect.top >= window.innerHeight + 100) {
                        btn.scrollIntoView({ block: 'center' });
                    }
                    btn.click();
                    // Also scroll body to bottom to trigger lazy loading
                    body.scrollTop = body.scrollHeight;
                    return true;
                }
            """)
            if not clicked:
                stuck_count += 1
                if stuck_count >= 5:
                    logger.info(f"[toutiao] All top-level comments loaded after {i} clicks")
                    break
            else:
                stuck_count = 0
            logger.info(f"[toutiao] Clicked load-more ({i+1})")
            await asyncio.sleep(1.5)

    async def _scroll_to_load_all(self, page, max_scrolls=50):
        """Scroll within the comment dialog body to load all comments."""
        stuck_count = 0
        for i in range(max_scrolls):
            # Scroll incrementally inside .body within .ttp-drawer
            scrolled = await page.evaluate("""
                () => {
                    const body = document.querySelector('.ttp-drawer .body');
                    if (!body) return false;
                    const before = body.scrollTop;
                    body.scrollTop += 500;
                    return body.scrollTop > before + 10;
                }
            """)
            if not scrolled:
                stuck_count += 1
                if stuck_count >= 3:
                    break
            else:
                stuck_count = 0

            await asyncio.sleep(0.8)

            # Check if all loaded
            all_loaded = await page.evaluate("""
                () => {
                    const body = document.querySelector('.ttp-drawer .body');
                    if (!body) return true;
                    const btn = body.querySelector('.load-more-btn');
                    if (btn && btn.offsetParent !== null) return false;
                    return body.innerText.includes('已加载全部') ||
                           body.innerText.includes('没有更多');
                }
            """)
            if all_loaded:
                logger.info("[toutiao] All comments loaded")
                break

            logger.debug(f"[toutiao] Scroll {i+1}/{max_scrolls}")

    async def _expand_all_replies(self, page):
        """Click all expand buttons to show more comments and replies.

        Handles three button types in the Toutiao comment drawer:
        - "查看全部 x 条回复" — expand reply thread
        - "查看更多回复" — load more replies in a thread
        - "查看更多评论" — load more top-level comments

        Scrolls within .ttp-drawer .body only, never the main page.
        """
        total_clicked = 0
        max_rounds = 200
        stuck_count = 0

        for round_i in range(max_rounds):
            # Try to click reply expand buttons first, then load-more for top-level comments
            clicked = await page.evaluate("""
                () => {
                    const body = document.querySelector('.ttp-drawer .body');
                    if (!body) return false;

                    // 1. Click "查看全部 X 条回复" or "查看更多回复"
                    const expandableTexts = ['查看更多回复'];
                    const checkMoreBtns = body.querySelectorAll('.check-more-reply');
                    for (const btn of checkMoreBtns) {
                        const text = (btn.innerText || '').trim();
                        const isExpandable =
                            (text.includes('查看全部') && text.includes('回复')) ||
                            expandableTexts.includes(text);
                        if (!isExpandable) continue;
                        if (btn.dataset._expanded) continue;
                        const rect = btn.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        if (rect.top < -100 || rect.top >= window.innerHeight + 100) {
                            btn.scrollIntoView({ block: 'center' });
                        }
                        btn.dataset._expanded = '1';
                        btn.click();
                        return 'reply';
                    }

                    // 2. Click "查看更多评论" to load more top-level comments
                    const loadMore = body.querySelector('.load-more-btn');
                    if (loadMore) {
                        const text = (loadMore.innerText || '').trim();
                        if (text.includes('查看更多评论') && !loadMore.dataset._expanded) {
                            const rect = loadMore.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                loadMore.dataset._expanded = '1';
                                loadMore.click();
                                return 'loadmore';
                            }
                        }
                    }

                    return false;
                }
            """)

            if clicked:
                total_clicked += 1
                stuck_count = 0
                await asyncio.sleep(1)
                continue

            # No more buttons found — try scrolling to reveal more
            scrolled = await page.evaluate("""
                () => {
                    const body = document.querySelector('.ttp-drawer .body');
                    if (!body) return false;
                    const before = body.scrollTop;
                    body.scrollTop += 500;
                    return body.scrollTop > before + 10;
                }
            """)
            if not scrolled:
                stuck_count += 1
                if stuck_count >= 3:
                    break
            else:
                stuck_count = 0
            await asyncio.sleep(1.5)

        if total_clicked > 0:
            logger.info(f"[toutiao] Expanded {total_clicked} sections")

    async def _extract_all_comments(self, page) -> list[tuple[str, str]]:
        """Extract all comments from DOM."""
        items = await page.evaluate("""
            () => {
                const results = [];

                const commentItems = document.querySelectorAll('.ttp-comment-item');
                for (const item of commentItems) {
                    const nickEl = item.querySelector('.user-name .name') ||
                                   item.querySelector('.name') ||
                                   item.querySelector('[class*="nickname"]');
                    const contentEl = item.querySelector('.body .content') ||
                                      item.querySelector('.content') ||
                                      item.querySelector('[class*="content"]');

                    const nickname = nickEl ? (nickEl.textContent || '').trim() : '';
                    let content = contentEl ? (contentEl.textContent || '').trim() : '';

                    // Handle image-only comments
                    if (!content && contentEl) {
                        const images = contentEl.querySelectorAll('img');
                        if (images.length > 0) {
                            content = '[图片]';
                        }
                    }
                    if (!content) {
                        const images = item.querySelectorAll('img');
                        const nonAvatar = Array.from(images).filter(img => {
                            const parent = img.closest('[class*="avatar"], [class*="user-name"]');
                            return !parent && (img.width > 20 || img.height > 20);
                        });
                        if (nonAvatar.length > 0) {
                            content = '[图片]';
                        }
                    }
                    // Handle emoji-only comments (<i class="emoji"> tags)
                    if (!content && contentEl) {
                        const emojis = contentEl.querySelectorAll('i.emoji, i[class*="emoji"]');
                        if (emojis.length > 0) {
                            content = '[表情]';
                        }
                    }

                    if (!content) continue;

                    // Find parent comment for reply items (nested inside parent .ttp-comment-item)
                    let parentRef = '';
                    const parentItem = item.parentElement?.closest('.ttp-comment-item');
                    if (parentItem) {
                        const pNickEl = parentItem.querySelector('.user-name .name, .name, [class*="nickname"]');
                        const pNick = pNickEl ? (pNickEl.textContent || '').trim() : '';
                        const pContentEl = parentItem.querySelector('.body .content, .content');
                        const pContent = pContentEl ? (pContentEl.textContent || '').trim() : '';
                        if (pNick || pContent) {
                            parentRef = pNick + ':' + pContent.substring(0, 30);
                        }
                    }

                    results.push({ nickname, content, parentRef });
                }

                return results;
            }
        """)

        all_comments = []
        seen = set()
        for item in items:
            key = item['nickname'] + '::' + item['content'][:50]
            if item.get('parentRef'):
                key += '@@' + item['parentRef']
            if key not in seen:
                seen.add(key)
                all_comments.append((item['nickname'], item['content']))
        return all_comments
