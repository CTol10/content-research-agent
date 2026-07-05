import asyncio
import json
import logging
import re

import config
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


def clean_weibo_content(text: str) -> str:
    """
    Clean weibo content by removing metadata like username, timestamp, IP, device info.

    Example:
        Input:  "公开阿睿Rick26-6-7 09:38发布于 浙江来自 iPad mini 关注体验了一下心心念念的大观...转发 15分享这条博文"
        Output: "体验了一下心心念念的大观..."
    """
    if not text:
        return text

    # Remove zero-width spaces
    text = text.replace('​', '').replace('‌', '').replace('‍', '')

    # Strategy 1: Extract content after "关注" keyword (most reliable)
    focus_match = re.search(r'关注(.+?)(?:转发|分享这条博文|评论|收藏|赞|\d+条评论|展开|收起|同时转发|按热度按时间|已加载全部评论|$)', text, re.DOTALL)
    if focus_match:
        content = focus_match.group(1).strip()
        if len(content) > 5:  # Valid content
            return content

    # Strategy 2: Remove metadata patterns from beginning and end
    patterns = [
        # Username + timestamp pattern: "用户名26-6-7 09:38"
        r'^[一-龥a-zA-Z0-9_]+\d{1,2}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}',
        # Username with colon: "用户名:" or "用户名："
        r'^[一-龥a-zA-Z0-9_]+[:：]',
        # "发布于 XXX"
        r'发布于\s*[一-龥]+',
        # "来自 XXX" (device info) - match until next Chinese or space
        r'来自\s*[a-zA-Z0-9\s]+?(?=[一-龥]|\s|$)',
        # "来自" + Chinese location
        r'来自[一-龥]+\s*',
        # "关注" button text at the beginning
        r'^关注',
        # Bottom metadata: "转发 X 分享这条博文"
        r'转发\s*\d*\s*分享这条博文\s*$',
        # "转发 X"
        r'转发\s*\d+\s*$',
        # "分享这条博文"
        r'分享这条博文\s*$',
        # "评论" at the end
        r'评论\s*$',
        # "收藏 X"
        r'收藏\s*\d+\s*$',
        # "赞 X"
        r'赞\s*\d+\s*$',
        # "X条评论"
        r'\d+条评论\s*$',
        # "展开" button
        r'展开\s*$',
        # "收起" button
        r'收起\s*$',
        # "同时转发" button
        r'同时转发\s*$',
        # "按热度按时间"
        r'按热度按时间\s*$',
        # "已加载全部评论"
        r'已加载全部评论\s*$',
        # Timestamp pattern at the end: "26-6-7 09:42"
        r'\d{1,2}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}\s*$',
        # "来自XXX" at the end
        r'来自[一-龥a-zA-Z0-9\s]+?\s*$',
    ]

    for pattern in patterns:
        text = re.sub(pattern, '', text)

    # Clean up extra whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    return text


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
            self._setup_video_capture(page)
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(8)

            await self._check_video_element(page)
            await self._try_extract_video_from_dom(page)

            try:
                post_content = await page.evaluate("""
                    () => {
                        // Priority 1: Specific weibo text class (clean content without metadata)
                        const selectors = [
                            '[class*="_text_"][class*="_ogText_"]',
                            '.weibo-text',
                            '.detail_wbtext_4CRf9',
                            '.wbpro-feed-ogText',
                        ];
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el && (el.textContent || '').trim().length > 10) {
                                let text = (el.textContent || '').trim();
                                // Remove zero-width spaces
                                text = text.replace(/\\u200B/g, '');
                                return text;
                            }
                        }

                        // Fallback: try article but extract only text nodes
                        const article = document.querySelector('article');
                        if (article) {
                            const textEl = article.querySelector('[class*="_text_"]');
                            if (textEl && (textEl.textContent || '').trim().length > 10) {
                                return (textEl.textContent || '').trim().replace(/\\u200B/g, '');
                            }
                        }

                        return '';
                    }
                """)
            except Exception as e:
                logger.warning(f"[weibo] Failed to extract post content: {e}")

            # Clean post content
            if post_content:
                post_content = clean_weibo_content(post_content)

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

            return {"post_content": post_content, "comments": all_comments, "video_url": self._get_primary_video_url(), "audio_url": self._get_primary_audio_url()}

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

            # Clean content to remove any metadata
            content = clean_weibo_content(content)

            # Handle image-only comments: if no text but has pic, use placeholder
            if not content and comment.get("pic"):
                content = "[图片]"

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

                # Clean reply content
                r_content = clean_weibo_content(r_content)

                # Handle image-only replies
                if not r_content and reply.get("pic"):
                    r_content = "[图片]"

                if r_nickname and r_content:
                    r_key = f"{r_nickname}::{r_content[:50]}"
                    if r_key not in seen:
                        seen.add(r_key)
                        all_comments.append((r_nickname, r_content))
