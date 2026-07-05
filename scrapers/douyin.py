import asyncio
import logging
from urllib.parse import urlparse

import config
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

SCROLL_CONTAINER = 'main.ICt0G60V'


class NoCommentsError(Exception):
    """Raised when a video has no comments."""
    pass


class DouyinScraper(BaseScraper):
    platform_name = "douyin"
    CDP_PORT = 9233

    def _get_login_url(self) -> str:
        return "https://www.douyin.com"

    async def scrape(self, url: str) -> dict:
        context = await self.new_context()
        page = await context.new_page()

        try:
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            self._setup_video_capture(page)
            await self._install_media_pause_guard(page)
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await self._pause_video(page)
            await asyncio.sleep(2)
            expected_video_id = self._extract_video_id(page.url or url)

            await self._dismiss_popups(page)
            await self._ensure_expected_video(page, url, expected_video_id)
            await self._pause_video(page)
            await asyncio.sleep(3)

            # Check if this is an image/text post (hideXgVideo indicates non-video content)
            is_image_post = await page.evaluate("""
                () => {
                    const xgContainer = document.querySelector('.xg-video-container');
                    if (xgContainer && xgContainer.classList.contains('hideXgVideo')) {
                        return true;
                    }
                    // Also check for note-detail which indicates image/text post
                    if (document.querySelector('[data-e2e="note-detail"]')) {
                        return true;
                    }
                    return false;
                }
            """)

            if is_image_post:
                logger.info(f"[douyin] Image/text post detected (hideXgVideo or note-detail), skipping video extraction")
            else:
                await self._check_video_element(page)
                await self._try_extract_video_from_dom(page)
                await self._ensure_expected_video(page, url, expected_video_id)
                # Re-extract after potential page reload in _ensure_expected_video
                await self._check_video_element(page)
                await self._try_extract_video_from_dom(page)

            post_content = ""
            try:
                post_content = await self._extract_post_content(page)
            except Exception as e:
                logger.warning(f"[douyin] Failed to extract post content: {e}")

            await self._click_comment_tab(page)
            await asyncio.sleep(3)

            no_comments = await page.evaluate(
                '() => document.body.innerText.includes(\'暂无评论\')'
            )
            if no_comments:
                raise NoCommentsError("暂无评论")

            expected_count = await self._get_expected_comment_count(page)
            if expected_count:
                logger.info(f"[douyin] Expected comment count: {expected_count}")
            await self._scroll_to_load_all(page, expected_count=expected_count)
            await asyncio.sleep(2)
            await self._expand_all_replies(page)
            comments = await self._extract_all_comments(page, expected_count=expected_count)
            logger.info(f"[douyin] Total comments extracted: {len(comments)}")

            debug_dir = config.OUTPUT_DIR / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            serial = url.split("/")[-1].split("?")[0]
            await page.screenshot(path=str(debug_dir / f"douyin_{serial}_final.png"), full_page=False)

            # Only return video_url for actual video posts
            video_url = None if is_image_post else self._get_primary_video_url()
            audio_url = None if is_image_post else self._get_primary_audio_url()
            return {"post_content": post_content, "comments": comments, "video_url": video_url, "audio_url": audio_url}

        except Exception as e:
            logger.error(f"[douyin] Failed to scrape {url}: {e}")
            raise
        finally:
            await page.close()

    @staticmethod
    async def _get_scroll_container_js():
        """Return JavaScript that finds the comment scroll container."""
        return """
            () => {
                const getContainer = () => {
                    const list = document.querySelector('[data-e2e="comment-list"]');
                    const firstItem = document.querySelector('[data-e2e="comment-item"]');
                    const seed = list || firstItem;
                    if (seed) {
                        let el = seed;
                        while (el && el !== document.body) {
                            const cs = window.getComputedStyle(el);
                            const oy = cs.overflowY;
                            if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 20) {
                                return el;
                            }
                            el = el.parentElement;
                        }
                    }
                    for (const cmc of document.querySelectorAll('.comment-mainContent')) {
                        if (cmc.scrollHeight > cmc.clientHeight + 20) return cmc;
                    }
                    return document.querySelector('div.route-scroll-container') ||
                           document.querySelector('main.ICt0G60V');
                };
                return getContainer();
            }
        """

    async def _scroll_comment_container(self, page, position: int):
        """Scroll comment container to absolute position."""
        js = await self._get_scroll_container_js()
        await page.evaluate(f"""
            {js.replace('return getContainer();', f'''
                const c = getContainer();
                if (c) c.scrollTop = {position};
                return c;
            ''')}
        """)

    async def _scroll_comment_container_relative(self, page, delta: int) -> bool:
        """Scroll comment container by delta pixels. Returns True if scrolled."""
        js = await self._get_scroll_container_js()
        result = await page.evaluate(f"""
            {js.replace('return getContainer();', f'''
                const c = getContainer();
                if (!c) return false;
                const before = c.scrollTop;
                c.scrollTop += {delta};
                return c.scrollTop > before + 5;
            ''')}
        """)
        return bool(result)

    async def _scroll_to_load_all(self, page, max_scrolls=120, expected_count: int | None = None):
        stuck_count = 0
        prev_items = 0
        for i in range(max_scrolls):
            state = await page.evaluate(f"""
                () => {{
                    const getContainer = () => {{
                        const list = document.querySelector('[data-e2e="comment-list"]');
                        const firstItem = document.querySelector('[data-e2e="comment-item"]');
                        const seed = list || firstItem;
                        if (seed) {{
                            let el = seed;
                            while (el && el !== document.body) {{
                                const cs = window.getComputedStyle(el);
                                const oy = cs.overflowY;
                                if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 20) {{
                                    return el;
                                }}
                                el = el.parentElement;
                            }}
                        }}
                        // Fallback: comment-mainContent (image/text posts have a dedicated
                        // scrollable .comment-mainContent with overflow:scroll)
                        for (const cmc of document.querySelectorAll('.comment-mainContent')) {{
                            if (cmc.scrollHeight > cmc.clientHeight + 20) return cmc;
                        }}
                        return document.querySelector('div.route-scroll-container') || document.querySelector('{SCROLL_CONTAINER}');
                    }};

                    const container = getContainer();
                    if (!container) return {{ moved: false, atBottom: true }};
                    const beforeTop = container.scrollTop;
                    container.scrollTop = beforeTop + Math.max(300, Math.floor(container.clientHeight * 0.9));
                    const afterTop = container.scrollTop;
                    const atBottom = container.scrollTop >= container.scrollHeight - container.clientHeight - 10;
                    return {{ moved: afterTop > beforeTop + 5, atBottom }};
                }}
            """)

            current_items = await page.evaluate("() => document.querySelectorAll('[data-e2e=\"comment-item\"]').length")
            if expected_count and current_items >= expected_count:
                break

            if (not state.get("moved")) and current_items <= prev_items:
                stuck_count += 1
                if stuck_count >= 6:
                    break
            else:
                stuck_count = 0
            prev_items = max(prev_items, current_items)
            await asyncio.sleep(1.2)

    async def _expand_all_replies(self, page):
        total_clicked = 0

        # Initialize a global Set to track clicked buttons.
        # React re-renders destroy dataset attributes, so we use a persistent Set.
        await page.evaluate("() => { if (!window.__clickedExpandBtns) window.__clickedExpandBtns = new Set(); }")

        # Scroll comment container back to top first.
        await self._scroll_comment_container(page, 0)
        await asyncio.sleep(1)

        # Strategy: scroll through the container top-to-bottom, clicking all visible
        # expand buttons at each position. After reaching the bottom, scroll back to top
        # and repeat — new "展开更多" buttons appear inside just-expanded replies.
        # Stop when a full top-to-bottom pass clicks zero new buttons.
        for full_pass in range(5):
            await self._scroll_comment_container(page, 0)
            await asyncio.sleep(1)
            pass_clicked = 0

            for scroll_round in range(60):
                # At current position, click all visible buttons, re-scan until stable
                for rescan in range(10):
                    btns = page.locator('button:has-text("展开"):not(:has-text("收起")):not(:has-text("全文"))')
                    count = await btns.count()

                    clicked_this_scan = 0
                    for i in range(count):
                        btn = btns.nth(i)
                        try:
                            text = (await btn.text_content(timeout=2000) or '').strip()
                        except Exception:
                            continue
                        if not text or ('条回复' not in text and '更多' not in text):
                            continue
                        try:
                            if not await btn.is_visible(timeout=2000):
                                continue
                        except Exception:
                            continue

                        parent_nick = await btn.evaluate("""
                            (el) => {
                                const parent = el.closest('[data-e2e="comment-item"]');
                                return parent ? (parent.querySelector('[data-click-from="title"]') || {}).textContent || '' : '';
                            }
                        """)
                        btn_id = text + '::' + parent_nick.strip()
                        if await page.evaluate("(id) => window.__clickedExpandBtns.has(id)", btn_id):
                            continue

                        try:
                            await page.evaluate("(id) => { window.__clickedExpandBtns.add(id); }", btn_id)
                            await btn.click(force=True, timeout=3000)
                            clicked_this_scan += 1
                            pass_clicked += 1
                            total_clicked += 1
                            logger.info(f"[douyin] Clicked expand: '{text}'")
                            await asyncio.sleep(1.5)
                        except Exception:
                            continue

                    if clicked_this_scan == 0:
                        break
                    await asyncio.sleep(1)

                # Scroll down one viewport
                scrolled = await self._scroll_comment_container_relative(page, 800)
                if not scrolled:
                    break
                await asyncio.sleep(0.8)

            logger.info(f"[douyin] Pass {full_pass+1}: clicked {pass_clicked} buttons")
            if pass_clicked == 0:
                break  # No new buttons in this full pass — done

        if total_clicked > 0:
            logger.info(f"[douyin] Expanded {total_clicked} reply sections")

    async def _extract_all_comments(self, page, expected_count: int | None = None) -> list[tuple[str, str]]:
        await page.evaluate(f"""
            () => {{
                const getContainer = () => {{
                    const list = document.querySelector('[data-e2e="comment-list"]');
                    const firstItem = document.querySelector('[data-e2e="comment-item"]');
                    const seed = list || firstItem;
                    if (seed) {{
                        let el = seed;
                        while (el && el !== document.body) {{
                            const cs = window.getComputedStyle(el);
                            const oy = cs.overflowY;
                            if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 20) {{
                                return el;
                            }}
                            el = el.parentElement;
                        }}
                    }}
                    const cmc = document.querySelector('.comment-mainContent');
                    if (cmc && cmc.scrollHeight > cmc.clientHeight + 20) return cmc;
                    return document.querySelector('div.route-scroll-container') || document.querySelector('{SCROLL_CONTAINER}');
                }};
                const container = getContainer();
                if (container) container.scrollTop = 0;
            }}
        """)
        await asyncio.sleep(1)

        all_comments = []
        seen = set()
        max_scrolls = 150
        stuck_count = 0

        # Override CSS line-clamp so innerText returns full text
        await page.evaluate("""
            () => {
                const style = document.createElement('style');
                style.textContent = '[data-e2e="comment-item"] * { -webkit-line-clamp: unset !important; max-height: none !important; overflow: visible !important; text-overflow: clip !important; }';
                document.head.appendChild(style);
            }
        """)

        for _ in range(max_scrolls):
            items = await page.evaluate("""
                () => {
                    const items = document.querySelectorAll('[data-e2e="comment-item"]');
                    const normalize = (text) => (text || '').replace(/\\u00a0/g, ' ').trim();

                    return Array.from(items).map(item => {
                        const nickEl = item.querySelector('[data-click-from="title"]');
                        const nickname = nickEl ? normalize(nickEl.textContent || '') : '';

                        // Primary: use the content container inside .Sbe6bqNb
                        const contentWrap = item.querySelector('.Sbe6bqNb');
                        let content = '';
                        let hasImage = false;

                        if (contentWrap) {
                            // Extract text from .LqTo7UJT (the actual comment text element)
                            // Use textContent on this element only — it contains just this
                            // comment's text, not nested replies.
                            const contentSpan = contentWrap.querySelector('.LqTo7UJT');
                            content = contentSpan ? normalize(contentSpan.textContent || '') : '';

                            // Check for non-emoji images (larger than 20px) in the content area
                            const images = contentWrap.querySelectorAll('img');
                            for (const img of images) {
                                if (img.width > 20 || img.height > 20) {
                                    hasImage = true;
                                    break;
                                }
                            }

                            // If no text but has image, use placeholder
                            if (!content && hasImage) {
                                content = '[图片]';
                            }
                        }

                        // Fallback: extract text WITHOUT nested reply content
                        // Clone the item and strip reply containers first to avoid
                        // including concatenated reply thread text.
                        if (!content) {
                            const clone = item.cloneNode(true);
                            // Remove nested reply containers so innerText only
                            // reflects THIS comment's text
                            const replyContainers = clone.querySelectorAll(
                                '.replyContainer, [class*="replyContainer"], ' +
                                '[class*="reply-container"], [class*="reply_wrap"]'
                            );
                            replyContainers.forEach(el => el.remove());
                            // Also remove "展开" / "收起" buttons
                            clone.querySelectorAll('button').forEach(btn => {
                                const t = (btn.textContent || '').trim();
                                if (t.includes('展开') || t.includes('收起') || t.includes('回复')) {
                                    btn.remove();
                                }
                            });

                            const raw = normalize(clone.innerText || clone.textContent || '');
                            const lines = raw.split(/\\n+/).map(s => normalize(s)).filter(Boolean);
                            const bad = ['分享', '回复', '小时前', '天前', '周前', '刚刚',
                                         '置顶', '作者', '展开', '收起', '查看更多', '播放中'];
                            const filtered = [];
                            for (const line of lines) {
                                if (!line) continue;
                                if (nickname && line === nickname) continue;
                                if (/^\\.+$/.test(line)) continue;
                                if (/^\\d+$/.test(line)) continue;
                                if (bad.some(b => line.includes(b))) continue;
                                filtered.push(line);
                            }
                            content = filtered.join(' ').trim();
                        }

                        // Find parent comment for reply items (nested inside .replyContainer)
                        let parentRef = '';
                        const replyContainer = item.closest('.replyContainer');
                        if (replyContainer) {
                            const parentItem = replyContainer.closest('[data-e2e="comment-item"]');
                            if (parentItem && parentItem !== item) {
                                const pNickEl = parentItem.querySelector('[data-click-from="title"]');
                                const pNick = pNickEl ? normalize(pNickEl.textContent || '') : '';
                                const pWrap = parentItem.querySelector('.Sbe6bqNb .LqTo7UJT');
                                const pContent = pWrap ? normalize(pWrap.textContent || '') : '';
                                if (pNick || pContent) {
                                    parentRef = pNick + ':' + pContent.substring(0, 30);
                                }
                            }
                        }

                        return { nickname, content, parentRef };
                    }).filter(c => c.nickname || c.content);
                }
            """)

            before_count = len(seen)
            for item in items:
                key = item['nickname'] + '::' + item['content'][:50]
                if item.get('parentRef'):
                    key += '@@' + item['parentRef']
                if key not in seen:
                    seen.add(key)
                    all_comments.append((item['nickname'], item['content']))

            if expected_count and len(seen) >= expected_count:
                break

            state = await page.evaluate(f"""
                () => {{
                    const getContainer = () => {{
                        const list = document.querySelector('[data-e2e="comment-list"]');
                        const firstItem = document.querySelector('[data-e2e="comment-item"]');
                        const seed = list || firstItem;
                        if (seed) {{
                            let el = seed;
                            while (el && el !== document.body) {{
                                const cs = window.getComputedStyle(el);
                                const oy = cs.overflowY;
                                if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 20) {{
                                    return el;
                                }}
                                el = el.parentElement;
                            }}
                        }}
                        const cmc = document.querySelector('.comment-mainContent');
                        if (cmc && cmc.scrollHeight > cmc.clientHeight + 20) return cmc;
                        return document.querySelector('div.route-scroll-container') || document.querySelector('{SCROLL_CONTAINER}');
                    }};

                    const container = getContainer();
                    if (!container) return {{ moved: false, atBottom: true }};
                    const beforeTop = container.scrollTop;
                    container.scrollTop = beforeTop + Math.max(300, Math.floor(container.clientHeight * 0.9));
                    const afterTop = container.scrollTop;
                    const atBottom = container.scrollTop >= container.scrollHeight - container.clientHeight - 10;
                    return {{ moved: afterTop > beforeTop + 5, atBottom }};
                }}
            """)

            if (not state.get("moved")) and len(seen) == before_count:
                stuck_count += 1
                if stuck_count >= 6:
                    break
            else:
                stuck_count = 0
            await asyncio.sleep(0.8)

        return all_comments

    async def _get_expected_comment_count(self, page) -> int | None:
        try:
            return await page.evaluate("""
                () => {
                    const candidates = [];
                    const icon = document.querySelector('[data-e2e="feed-comment-icon"]');
                    if (icon) candidates.push((icon.innerText || '').trim());

                    const els = Array.from(document.querySelectorAll('span,div,button'));
                    for (const el of els) {
                        const t = (el.innerText || '').trim();
                        if (!t) continue;
                        const m = t.match(/评论\\s*\\(?\\s*(\\d+)\\s*\\)?/);
                        if (m) candidates.push(m[1]);
                    }
                    for (const raw of candidates) {
                        const n = parseInt(raw, 10);
                        if (!Number.isNaN(n) && n > 0) return n;
                    }
                    return null;
                }
            """)
        except Exception:
            return None

    async def _dismiss_popups(self, page):
        for _ in range(5):
            dismissed = False
            for selector in [
                'button:has-text("暂不")', 'button:has-text("不保存")',
                'button:has-text("取消")', 'button:has-text("关闭")',
            ]:
                try:
                    btn = page.locator(selector).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        dismissed = True
                        await asyncio.sleep(1)
                        break
                except Exception:
                    continue
            if dismissed:
                break
            await asyncio.sleep(1)

        await page.evaluate("""
            document.querySelectorAll('button').forEach(btn => {
                const text = btn.textContent || '';
                if (text.includes('暂不') || text.includes('不保存') || text.includes('取消')) {
                    btn.click();
                }
            });
        """)
        await asyncio.sleep(1)

    async def _click_comment_tab(self, page):
        comment_tab_clicked = False
        for selector in [
            '[data-e2e="feed-comment-icon"]',
            'div[data-e2e="feed-comment-icon"]',
            'span[data-e2e="feed-comment-icon"]',
        ]:
            try:
                tab = page.locator(selector).first
                if await tab.count() > 0 and await tab.is_visible():
                    await tab.click()
                    comment_tab_clicked = True
                    await asyncio.sleep(2)
                    break
            except Exception:
                continue

        for count in range(10):
            pattern = f'评论({count})'
            try:
                tab = page.locator(f'div:has-text("{pattern}")').first
                if await tab.count() > 0 and await tab.is_visible():
                    await tab.click()
                    comment_tab_clicked = True
                    await asyncio.sleep(2)
                    break
            except Exception:
                continue

        if not comment_tab_clicked:
            try:
                tab = page.locator('div:has-text("评论")').first
                if await tab.count() > 0 and await tab.is_visible():
                    await tab.click()
                    comment_tab_clicked = True
                    await asyncio.sleep(2)
            except Exception:
                pass

    async def _extract_post_content(self, page) -> str:
        """Extract post content from the video info area."""
        # Step 1: Click expand button if present to reveal full content
        await page.evaluate("""
            () => {
                // Find and click expand button in note-detail
                const noteDetail = document.querySelector('[data-e2e="note-detail"]');
                if (!noteDetail) return;
                const btns = noteDetail.querySelectorAll('button, [class*="expand"], [class*="more"]');
                for (const btn of btns) {
                    const text = (btn.innerText || '').trim();
                    if (text === '展开' || text.includes('展开')) {
                        btn.click();
                        return;
                    }
                }
            }
        """)
        await asyncio.sleep(0.5)

        # Step 2: Extract content with updated selectors
        content = await page.evaluate("""
            () => {
                const normalize = (text) => text
                    .replace(/\\u00a0/g, ' ')
                    .replace(/[ \\t\\f\\v]+/g, ' ')
                    .replace(/ *\\n+ */g, '\\n')
                    .trim();

                // Primary: note-detail (new Douyin layout)
                const noteDetail = document.querySelector('[data-e2e="note-detail"]');
                if (noteDetail) {
                    // Find the description div inside note-detail
                    // Look for the div that contains the main text (not user info)
                    const descDiv = noteDetail.querySelector('.D7OuYODi, .SAYsgcoF, [class*="desc"]');
                    if (descDiv) {
                        let text = normalize(descDiv.innerText || '');
                        // Remove expand/collapse button text
                        text = text.replace(/\\n?展开\\n?/g, '\\n').replace(/\\n?收起\\n?/g, '\\n');
                        // Remove trailing publish time
                        text = text.replace(/\\n发布时间：.*$/, '').trim();
                        // Collapse multiple newlines into single space for consistent format
                        text = text.replace(/\\n+/g, ' ').trim();
                        if (text.length >= 2) return text;
                    }

                    // Fallback: get text from the content area, skip user info
                    const contentDiv = noteDetail.querySelector('.oEmB895Z');
                    if (contentDiv) {
                        const allText = contentDiv.innerText || '';
                        // Extract only the content part (after user info)
                        const lines = allText.split('\\n');
                        const contentLines = [];
                        let foundContent = false;
                        for (const line of lines) {
                            const trimmed = line.trim();
                            // Skip user info lines
                            if (trimmed.match(/^(粉丝|关注|获赞)/)) continue;
                            if (trimmed.match(/^\\d+$/)) continue;
                            if (['展开', '收起', '发布时间'].some(k => trimmed.includes(k))) continue;
                            if (trimmed.length > 10) foundContent = true;
                            if (foundContent) contentLines.push(trimmed);
                        }
                        let text = contentLines.join(' ').trim();
                        text = text.replace(/发布时间：.*$/, '').trim();
                        if (text.length >= 2) return text;
                    }
                }

                // Legacy selectors (fallback)
                const info = document.querySelector('[data-e2e="detail-video-info"]');
                if (info) {
                    const textDiv = info.firstElementChild;
                    if (textDiv) {
                        let text = normalize(textDiv.innerText || '');
                        if (text.startsWith('展开')) text = text.substring(2).trim();
                        text = text.replace(/\\d+举报.*$/, '').trim();
                        if (text.length >= 2) return text;
                    }
                }

                // More fallbacks
                const fallbacks = [
                    '[data-e2e="video-desc"]',
                    '[data-e2e="cover-age-title-container"]',
                    '.video-info-detail',
                ];
                for (const sel of fallbacks) {
                    const el = document.querySelector(sel);
                    if (!el) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) continue;
                    const text = normalize(el.innerText || '');
                    if (text.length >= 5) return text;
                }

                return '';
            }
        """)
        if not content:
            logger.info("[douyin] No post content found, using [无正文]")
            return "[无正文]"
        return content

    async def _pause_video(self, page):
        """Pause video playback immediately after opening the page."""
        media_state = await self._pause_all_media(page)
        if media_state.get("count", 0) <= 0:
            return
        if media_state.get("playing", 0) <= 0:
            logger.info("[douyin] Media pause guard stopped autoplay")
            return

        video_info = media_state.get("target")
        if video_info and not video_info.get("paused"):
            await page.mouse.click(video_info["x"], video_info["y"])
            logger.info(f"[douyin] Clicked video at ({video_info['x']}, {video_info['y']}) to pause")
            await asyncio.sleep(0.5)
            await self._pause_all_media(page)

    @staticmethod
    def _extract_video_id(raw_url: str) -> str:
        """Extract Douyin video id from a URL for same-video validation."""
        if not raw_url:
            return ""
        path = urlparse(raw_url).path
        parts = [part for part in path.split("/") if part]
        for idx, part in enumerate(parts):
            if part == "video" and idx + 1 < len(parts):
                return parts[idx + 1]
        return parts[-1] if parts else ""

    async def _ensure_expected_video(self, page, original_url: str, expected_video_id: str):
        """Reload the original URL if the page has auto-switched to another video."""
        if not expected_video_id:
            return
        current_video_id = self._extract_video_id(page.url)
        if not current_video_id or current_video_id == expected_video_id:
            return

        logger.warning(
            f"[douyin] Video auto-switched from {expected_video_id} to {current_video_id}, reloading original URL"
        )
        await page.goto(original_url, timeout=30000, wait_until="domcontentloaded")
        await self._pause_video(page)
        await asyncio.sleep(2)
