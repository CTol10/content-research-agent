import asyncio
import logging

import config
from base_scraper import BaseScraper

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
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(5)

            await self._dismiss_popups(page)

            post_content = ""
            try:
                post_content = await page.evaluate("""
                    () => {
                        const selectors = [
                            '.video-info-detail .title',
                            '.video-info-detail',
                            '[data-e2e="video-desc"]',
                            '.desc',
                        ];
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el && el.innerText.trim().length > 5) {
                                return el.innerText.trim();
                            }
                        }
                        return '';
                    }
                """)
            except Exception as e:
                logger.warning(f"[douyin] Failed to extract post content: {e}")

            await self._click_comment_tab(page)
            await asyncio.sleep(3)

            no_comments = await page.evaluate(
                '() => document.body.innerText.includes(\'暂无评论\')'
            )
            if no_comments:
                raise NoCommentsError("暂无评论")

            await self._scroll_to_load_all(page)
            await asyncio.sleep(2)
            await self._expand_all_replies(page)
            comments = await self._extract_all_comments(page)
            logger.info(f"[douyin] Total comments extracted: {len(comments)}")

            debug_dir = config.OUTPUT_DIR / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            serial = url.split("/")[-1].split("?")[0]
            await page.screenshot(path=str(debug_dir / f"douyin_{serial}_final.png"), full_page=False)

            return {"post_content": post_content, "comments": comments}

        except Exception as e:
            logger.error(f"[douyin] Failed to scrape {url}: {e}")
            raise
        finally:
            await page.close()

    async def _scroll_to_load_all(self, page, max_scrolls=30):
        stuck_count = 0
        for i in range(max_scrolls):
            at_bottom = await page.evaluate(f"""
                () => {{
                    const main = document.querySelector('{SCROLL_CONTAINER}');
                    if (!main) return true;
                    const before = main.scrollTop;
                    main.scrollTop = main.scrollHeight;
                    return main.scrollTop <= before + 10;
                }}
            """)
            if at_bottom:
                stuck_count += 1
                if stuck_count >= 3:
                    break
            else:
                stuck_count = 0
            await asyncio.sleep(1.5)

    async def _expand_all_replies(self, page):
        clicked_buttons = set()
        total_clicked = 0
        max_rounds = 100
        stuck_count = 0

        for _ in range(max_rounds):
            await page.evaluate("""
                () => {
                    document.querySelectorAll('button.comment-reply-expand-btn, button.AeuV5xuv').forEach(btn => {
                        let p = btn.parentElement;
                        while (p && p !== document.body) {
                            if (window.getComputedStyle(p).display === 'none') {
                                p.style.display = 'block';
                            }
                            p = p.parentElement;
                        }
                    });
                }
            """)
            await asyncio.sleep(0.3)

            btn_info = await page.evaluate("""
                () => {
                    const btns = document.querySelectorAll('button.comment-reply-expand-btn, button.AeuV5xuv');
                    for (const btn of btns) {
                        const text = (btn.innerText || '').trim();
                        if (text.includes('收起') || text === '') continue;
                        if (!text.includes('展开')) continue;
                        const rect = btn.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0 &&
                            rect.top >= -50 && rect.top < window.innerHeight + 50) {
                            return {
                                text: text,
                                x: rect.left + rect.width / 2,
                                y: rect.top + rect.height / 2,
                                key: text + '_' + Math.round(rect.top)
                            };
                        }
                    }
                    return null;
                }
            """)

            if not btn_info:
                scrolled = await page.evaluate(f"""
                    () => {{
                        const main = document.querySelector('{SCROLL_CONTAINER}');
                        if (!main) return false;
                        const before = main.scrollTop;
                        main.scrollTop += 500;
                        return main.scrollTop > before + 10;
                    }}
                """)
                if not scrolled:
                    stuck_count += 1
                    if stuck_count >= 3:
                        break
                else:
                    stuck_count = 0
                await asyncio.sleep(1)
                continue

            if btn_info['key'] in clicked_buttons:
                await page.evaluate(f"""
                    () => {{
                        const main = document.querySelector('{SCROLL_CONTAINER}');
                        if (main) main.scrollTop += 200;
                    }}
                """)
                await asyncio.sleep(0.5)
                continue

            if btn_info['y'] < 0 or btn_info['y'] >= 900:
                await page.evaluate(f"""
                    (btnY) => {{
                        const main = document.querySelector('{SCROLL_CONTAINER}');
                        if (main) main.scrollTop += btnY - 300;
                    }}
                """, btn_info['y'])
                await asyncio.sleep(0.5)

                btn_info = await page.evaluate("""
                    () => {
                        const btns = document.querySelectorAll('button.comment-reply-expand-btn, button.AeuV5xuv');
                        for (const btn of btns) {
                            const text = (btn.innerText || '').trim();
                            if (text.includes('收起') || !text.includes('展开')) continue;
                            const rect = btn.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0 &&
                                rect.top >= 0 && rect.top < window.innerHeight) {
                                return {
                                    text: text,
                                    x: rect.left + rect.width / 2,
                                    y: rect.top + rect.height / 2,
                                    key: text + '_' + Math.round(rect.top)
                                };
                            }
                        }
                        return null;
                    }
                """)
                if not btn_info:
                    continue

            clicked_buttons.add(btn_info['key'])
            await page.mouse.click(btn_info['x'], btn_info['y'])
            total_clicked += 1
            await asyncio.sleep(2)

        if total_clicked > 0:
            logger.info(f"[douyin] Expanded {total_clicked} reply sections")

    async def _extract_all_comments(self, page) -> list[tuple[str, str]]:
        await page.evaluate(f"""
            () => {{
                const main = document.querySelector('{SCROLL_CONTAINER}');
                if (main) main.scrollTop = 0;
            }}
        """)
        await asyncio.sleep(1)

        all_comments = []
        seen = set()
        max_scrolls = 50
        stuck_count = 0

        for _ in range(max_scrolls):
            items = await page.evaluate("""
                () => {
                    const items = document.querySelectorAll('[data-e2e="comment-item"]');
                    return Array.from(items).map(item => {
                        const nickEl = item.querySelector('[data-click-from="title"]');
                        const contentEl = item.querySelector('[class*="JrWL1Ykc"]');
                        const nickname = nickEl ? nickEl.innerText.trim() : '';
                        const content = contentEl ? contentEl.innerText.trim() : '';
                        return { nickname, content };
                    }).filter(c => c.nickname || c.content);
                }
            """)

            before_count = len(seen)
            for item in items:
                key = item['nickname'] + '::' + item['content'][:50]
                if key not in seen:
                    seen.add(key)
                    all_comments.append((item['nickname'], item['content']))

            at_bottom = await page.evaluate(f"""
                () => {{
                    const main = document.querySelector('{SCROLL_CONTAINER}');
                    if (!main) return true;
                    const before = main.scrollTop;
                    main.scrollTop += main.clientHeight;
                    return main.scrollTop <= before + 10;
                }}
            """)
            if at_bottom and len(seen) == before_count:
                stuck_count += 1
                if stuck_count >= 3:
                    break
            else:
                stuck_count = 0
            await asyncio.sleep(0.8)

        return all_comments

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
