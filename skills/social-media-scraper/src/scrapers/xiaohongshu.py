import asyncio
import logging
import random
import re

import config
from base_scraper import BaseScraper

logger = logging.getLogger(__name__)

# Stealth JS to hide Playwright automation markers
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
delete navigator.__proto__.webdriver;
"""


class NoteUnavailableError(Exception):
    """Raised when a note is deleted, private, or otherwise unavailable."""
    pass


class XiaohongshuScraper(BaseScraper):
    platform_name = "xiaohongshu"
    CDP_PORT = 9234

    def __init__(self):
        super().__init__()
        self._consecutive_404 = 0
        self._use_warmup_strategy = False

    def _get_login_url(self) -> str:
        return "https://www.xiaohongshu.com"

    async def _inject_stealth(self, page):
        await page.add_init_script(STEALTH_JS)

    async def _warmup_session(self, page):
        logger.info("[xiaohongshu] Warming up session via homepage...")
        try:
            await page.goto("https://www.xiaohongshu.com/explore", timeout=15000, wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2, 4))
        except Exception:
            pass

    def _convert_url(self, url: str) -> str:
        if "/discovery/item/" in url:
            return url.replace("/discovery/item/", "/explore/")
        if "/explore/" not in url:
            m = re.search(r'/item/([a-f0-9]+)', url)
            if m:
                note_id = m.group(1)
                base = url.split("/discovery/")[0] if "/discovery/" in url else "https://www.xiaohongshu.com"
                return f"{base}/explore/{note_id}"
        return url

    async def _is_available(self, page) -> bool:
        url = page.url
        if "404" in url or "error_code" in url:
            return False
        if "login" in url.lower():
            return False
        try:
            has_content = await page.evaluate("""
                () => {
                    const el = document.querySelector('.note-content, [class*="note"], .content');
                    return el && el.innerText.trim().length > 0;
                }
            """)
            return has_content
        except Exception:
            return "404" not in url and "login" not in url.lower()

    async def _navigate_with_retry(self, page, url: str, context) -> bool:
        """Navigate to URL with anti-detection retry. Returns True if page loaded."""
        strategies = []

        if self._use_warmup_strategy or self._consecutive_404 >= 3:
            strategies = [("warmup+stealth", True), ("stealth_only", False)]
        else:
            strategies = [("stealth_only", False), ("warmup+stealth", True), ("alt_url", False)]

        for strategy_name, do_warmup in strategies:
            try:
                if strategy_name == "alt_url":
                    alt_url = self._convert_url(url)
                    if alt_url == url:
                        continue
                    logger.info(f"[xiaohongshu] Trying alternative URL: {alt_url}")
                    url_to_use = alt_url
                else:
                    url_to_use = url

                if do_warmup:
                    await self._warmup_session(page)

                await self._inject_stealth(page)
                await page.goto(url_to_use, timeout=30000, wait_until="domcontentloaded")
                await asyncio.sleep(random.uniform(3, 6))

                if await self._is_available(page):
                    logger.info(f"[xiaohongshu] Strategy '{strategy_name}' succeeded")
                    self._consecutive_404 = 0
                    return True

                logger.warning(f"[xiaohongshu] Strategy '{strategy_name}' got 404, trying next...")

            except Exception as e:
                logger.warning(f"[xiaohongshu] Strategy '{strategy_name}' error: {e}")
                continue

        self._consecutive_404 += 1
        if self._consecutive_404 >= 3:
            self._use_warmup_strategy = True
            logger.warning(f"[xiaohongshu] {self._consecutive_404} consecutive 404s, switching to warmup strategy")
        return False

    async def scrape(self, url: str) -> dict:
        context = await self.new_context()
        page = await context.new_page()

        try:
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))

            if not await self._navigate_with_retry(page, url, context):
                raise NoteUnavailableError(f"笔记不可用（反爬或已删除）: {url}")

            post_content = ""
            try:
                post_content = await page.evaluate("""
                    () => {
                        const selectors = [
                            '.note-content .desc',
                            '.note-content',
                            '[class*="note"] [class*="desc"]',
                            '.content',
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
                logger.warning(f"[xiaohongshu] Failed to extract post content: {e}")

            # Phase 1: expand reply threads (main time cost)
            for _ in range(8):
                try:
                    prev = await page.evaluate('document.querySelectorAll(".comment-item").length')
                    await self._expand_all_replies(page)
                    await asyncio.sleep(1)
                    cur = await page.evaluate('document.querySelectorAll(".comment-item").length')
                    if cur == prev:
                        break
                except Exception:
                    logger.warning("[xiaohongshu] Context lost during expand, extracting loaded comments")
                    break

            # Phase 2: scroll to load lazy comments (up to 5 passes)
            for _ in range(5):
                try:
                    prev = await page.evaluate('document.querySelectorAll(".comment-item").length')
                    await self._scroll_to_load_all(page)
                    await asyncio.sleep(1)
                    cur = await page.evaluate('document.querySelectorAll(".comment-item").length')
                    if cur == prev:
                        break
                except Exception:
                    break

            comments = await self._extract_all_comments(page)
            logger.info(f"[xiaohongshu] Total comments extracted: {len(comments)}")

            debug_dir = config.OUTPUT_DIR / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            serial = url.split("/")[-1].split("?")[0][:20]
            await page.screenshot(path=str(debug_dir / f"xhs_{serial}_final.png"), full_page=False)

            return {"post_content": post_content, "comments": comments}

        except Exception as e:
            logger.error(f"[xiaohongshu] Failed to scrape {url}: {e}")
            raise
        finally:
            await page.close()

    async def _scroll_to_load_all(self, page, max_scrolls=50):
        stuck_count = 0
        for i in range(max_scrolls):
            at_bottom = await page.evaluate("""
                () => {
                    const scroller = document.querySelector('.note-scroller');
                    if (!scroller) return true;
                    const before = scroller.scrollTop;
                    scroller.scrollTop = scroller.scrollHeight;
                    return scroller.scrollTop <= before + 10;
                }
            """)
            if at_bottom:
                stuck_count += 1
                if stuck_count >= 3:
                    break
            else:
                stuck_count = 0
            await asyncio.sleep(1.5)

            has_login_wall = await page.evaluate("""
                () => {
                    const loginEl = document.querySelector('.comments-login');
                    if (!loginEl) return false;
                    const rect = loginEl.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }
            """)
            if has_login_wall:
                break

    async def _expand_all_replies(self, page):
        total_clicked = 0
        max_rounds = 100
        stuck_count = 0

        for round_i in range(max_rounds):
            # Click the first unexpanded reply button found, then re-query DOM
            clicked = await page.evaluate("""
                () => {
                    const btns = document.querySelectorAll('.show-more');
                    for (const btn of btns) {
                        const text = (btn.innerText || '').trim();
                        const rect = btn.getBoundingClientRect();
                        if (rect.width <= 0 || rect.height <= 0) continue;
                        if (!text.includes('展开')) continue;
                        if (btn.dataset._expanded) continue;
                        // Scroll into view if needed
                        if (rect.top < -100 || rect.top >= window.innerHeight + 100) {
                            btn.scrollIntoView({ block: 'center' });
                        }
                        btn.dataset._expanded = '1';
                        btn.click();
                        return true;
                    }
                    return false;
                }
            """)

            if clicked:
                total_clicked += 1
                stuck_count = 0
                await asyncio.sleep(2)
                continue

            # No more buttons found — try scrolling to reveal more
            scrolled = await page.evaluate("""
                () => {
                    const scroller = document.querySelector('.note-scroller');
                    if (!scroller) return false;
                    const before = scroller.scrollTop;
                    scroller.scrollTop += 600;
                    return scroller.scrollTop > before + 10;
                }
            """)
            if not scrolled:
                stuck_count += 1
                if stuck_count >= 3:
                    break
            else:
                stuck_count = 0
            await asyncio.sleep(1)

        if total_clicked > 0:
            logger.info(f"[xiaohongshu] Expanded {total_clicked} reply sections")

    async def _extract_all_comments(self, page) -> list[tuple[str, str]]:
        items = await page.evaluate("""
            () => {
                const items = document.querySelectorAll('.comment-item');
                return Array.from(items).map(item => {
                    const nameEl = item.querySelector('.name, .nickname, [class*="name"]');
                    const nickname = nameEl ? nameEl.innerText.trim() : '';
                    const contentEl = item.querySelector('.content, .text, [class*="content"]');
                    const content = contentEl ? contentEl.innerText.trim() : '';
                    return { nickname, content };
                }).filter(c => c.nickname || c.content);
            }
        """)
        return [(item['nickname'], item['content']) for item in items]
