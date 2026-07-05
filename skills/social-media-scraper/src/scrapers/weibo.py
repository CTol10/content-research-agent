import asyncio
import json
import logging
import re

import config
from base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class WeiboScraper(BaseScraper):
    platform_name = "weibo"
    CDP_PORT = 9235

    def _get_login_url(self) -> str:
        return "https://weibo.com"

    async def scrape(self, url: str) -> dict:
        context = await self.new_context()
        page = await context.new_page()

        api_responses = []
        post_content = ""

        async def handle_response(response):
            if "buildComments" in response.url:
                try:
                    body = await response.json()
                    api_responses.append(body)
                except Exception:
                    pass

        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        try:
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(8)

            try:
                post_content = await page.evaluate("""
                    () => {
                        const selectors = [
                            '.weibo-text',
                            '.detail_wbtext_4CRf9',
                            '[class*="text"]',
                            'article',
                        ];
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el && el.innerText.trim().length > 10) {
                                return el.innerText.trim();
                            }
                        }
                        return '';
                    }
                """)
            except Exception as e:
                logger.warning(f"[weibo] Failed to extract post content: {e}")

            if not api_responses:
                logger.warning(f"[weibo] No comment API response captured for {url}")

            all_comments = []
            seen = set()

            prev_response_count = 0
            self._process_responses(api_responses, all_comments, seen)
            prev_response_count = len(api_responses)

            max_pages = 10
            for page_num in range(max_pages):
                max_id = 0
                for resp in api_responses:
                    mid = resp.get("max_id", 0)
                    if mid > max_id:
                        max_id = mid

                if max_id <= 0:
                    break

                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(3)

                self._process_responses(api_responses, all_comments, seen)

                if len(api_responses) == prev_response_count:
                    break

                prev_response_count = len(api_responses)

            logger.info(f"[weibo] Total comments extracted: {len(all_comments)}")

            debug_dir = config.OUTPUT_DIR / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            serial = url.split("/")[-1][:20]
            await page.screenshot(path=str(debug_dir / f"weibo_{serial}_final.png"), full_page=False)

            return {"post_content": post_content, "comments": all_comments}

        except Exception as e:
            logger.error(f"[weibo] Failed to scrape {url}: {e}")
            raise
        finally:
            await page.close()

    def _process_responses(self, api_responses: list, all_comments: list, seen: set):
        for resp in api_responses:
            self._parse_api_response(resp, all_comments, seen)

    def _parse_api_response(self, resp: dict, all_comments: list, seen: set):
        comments_data = resp.get("data", [])
        if not isinstance(comments_data, list):
            return

        for comment in comments_data:
            user = comment.get("user", {})
            nickname = user.get("screen_name", "")
            content = comment.get("text_raw", comment.get("text", ""))
            if "<" in content:
                content = re.sub(r"<[^>]+>", "", content)

            if nickname and content:
                key = f"{nickname}::{content[:50]}"
                if key not in seen:
                    seen.add(key)
                    all_comments.append((nickname, content))

            replies = comment.get("comments", [])
            for reply in replies:
                r_user = reply.get("user", {})
                r_nickname = r_user.get("screen_name", "")
                r_content = reply.get("text_raw", reply.get("text", ""))
                if "<" in r_content:
                    r_content = re.sub(r"<[^>]+>", "", r_content)

                if r_nickname and r_content:
                    r_key = f"{r_nickname}::{r_content[:50]}"
                    if r_key not in seen:
                        seen.add(r_key)
                        all_comments.append((r_nickname, r_content))
