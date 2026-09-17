import asyncio
import json
import logging
import os
import re
import tempfile
from urllib.parse import urlparse

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

    # ── URL / API helpers ────────────────────────────────────────────────

    @staticmethod
    def _extract_status_id(url: str) -> str | None:
        """Extract the status ID (mblogid/base62 or numeric id) from a detail URL.

        Supports:
            http://weibo.com/{uid}/{bid}
            https://weibo.com/u/{uid}/{bid}
            https://weibo.com/status/{bid}
            https://m.weibo.cn/status/{bid_or_id}
        Returns None for profile/home/login URLs.
        """
        if not url:
            return None
        parts = [p for p in urlparse(url).path.split("/") if p]
        if not parts:
            return None
        last = parts[-1]
        # Bare section/home page or numeric-only profile root (uid of a /u/ link)
        if last.lower() in ("u", "home", "profile", "discover", "login", "login.php", "tv", "search"):
            return None
        if last.isdigit() and len(last) <= 11:
            return None  # bare user id, not a status
        return last

    @staticmethod
    async def _api_fetch(page, url: str) -> dict:
        """Fetch JSON from a same-origin weibo.com API with login cookies."""
        return await page.evaluate(
            """
            async (url) => {
                try {
                    const resp = await fetch(url, {credentials: 'include'});
                    if (!resp.ok) return {code: resp.status, message: resp.statusText};
                    return await resp.json();
                } catch (e) {
                    return {code: -1, message: String(e)};
                }
            }
            """,
            url,
        )

    @staticmethod
    def _collect_mp4_candidates(status: dict) -> list[dict]:
        """Walk a statuses/show JSON collecting progressive MP4 entries.

        Video URLs live under one of:
          - page_info.media.video_info.playback_list[].play_info (single video)
          - mix_media_info.items[*].data.media_info.playback_list[].play_info (mixed media)
        Each play_info carries either `url` or `url_list[]`. Entry `type` 1 is the
        progressive MP4 stream; type 3 is the JPEG cover. Only MP4 is kept — MiMo's
        audio path needs an AAC-in-MP4 family container.
        """
        candidates = []

        def walk(node):
            if isinstance(node, dict):
                playback = node.get("playback_list")
                if isinstance(playback, list):
                    for item in playback:
                        info = item.get("play_info") if isinstance(item, dict) else None
                        info = info if isinstance(info, dict) else {}
                        urls = [u for u in (info.get("url_list") or []) if isinstance(u, str)]
                        if info.get("url"):
                            urls.append(info["url"])
                        mime_ok = (
                            info.get("mime") == "video/mp4"
                            or any(u.lower().find(".mp4") != -1 for u in urls)
                        )
                        if info.get("type") != 1 or not mime_ok or not urls:
                            continue
                        candidates.append(
                            {
                                "label": info.get("quality_label") or "",
                                "size": int(info.get("size") or 0),
                                "urls": urls,
                            }
                        )
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node[:20]:
                    walk(v)

        walk(status)
        return candidates

    @classmethod
    def _pick_playback_candidate(cls, candidates: list[dict]) -> dict | None:
        """Pick the smallest MP4 candidate — transcription only needs the audio track,
        so lower resolutions download faster at no recognition cost."""
        if not candidates:
            return None
        return min(candidates, key=lambda c: (c["size"] == 0, c["size"] or float("inf")))

    @staticmethod
    def _convert_to_m4a(input_path: str) -> str | None:
        """Re-encode any odd audio track into AAC-in-M4A via ffmpeg as fallback."""
        try:
            import subprocess

            import imageio_ffmpeg

            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            output_path = input_path.rsplit(".", 1)[0] + "_conv.m4a"
            cmd = [
                ffmpeg_exe,
                "-i", input_path,
                "-vn",
                "-c:a", "aac",
                "-b:a", "128k",
                output_path,
                "-y",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, errors="replace")
            if result.returncode == 0 and os.path.exists(output_path):
                logger.info(f"[weibo] Audio converted to M4A: {os.path.getsize(output_path) / 1024:.0f} KB")
                return output_path
            logger.warning(f"[weibo] ffmpeg conversion failed: {result.stderr[:200]}")
            return None
        except Exception:
            logger.warning("[weibo] Audio conversion failed", exc_info=True)
            return None

    # ── Narration (口播) extraction ──────────────────────────────────────

    _IDENTITY_ECHO_RE = re.compile(
        r"(you'?re\s+mimo|i'?m\s+mimo|我是.*?(语言模型|大模型)|你是.{0,6}(人工智能|AI)\s*助手)",
        re.IGNORECASE,
    )

    @staticmethod
    def _narration_suspect(text: str | None) -> bool:
        """Heuristic for model-garbage responses (identity echo / meta answers).

        Noisy music/concert clips occasionally make MiMo answer about itself
        ("你是AI助手…" / "You're MiMo…") instead of transcribing. Explicit
        identity-echo patterns are always flagged; short texts referencing AI
        assistants only if short, so real transcripts merely *mentioning* such
        words stay untouched.
        """
        if not text or not text.strip():
            return True
        if WeiboScraper._IDENTITY_ECHO_RE.search(text):
            return True
        if len(text.strip()) > 40:
            return False
        return bool(re.search(r"(MiMo|语言模型|人工智能助手|AI\s*助手)", text, re.IGNORECASE))

    async def _extract_narration(self, page, status: dict) -> str | None:
        """Download the post's video and transcribe its narration via MiMo.

        Mirrors BilibiliScraper._extract_narration: downloads through Playwright's
        context.request (carries cookies/Referer), extracts the audio track, then
        reuses VideoProcessor's base64 + MiMo transcription pipeline.
        """
        candidates = self._collect_mp4_candidates(status)
        best = self._pick_playback_candidate(candidates)
        if not best:
            logger.debug("[weibo] No playable MP4 entry found in status JSON")
            return None

        size_mb = best["size"] / (1024 * 1024) if best["size"] else 0
        label = best["label"] or "?"
        if size_mb and size_mb > config.MAX_VIDEO_SIZE_MB:
            logger.warning(f"[weibo] Video too large for narration: {size_mb:.1f}MB")
            return None
        logger.info(f"[weibo] Narration source: quality={label} size={size_mb:.1f}MB")

        tmp_path = None
        converted_path = None
        try:
            url = best["urls"][0].replace("http://", "https://", 1)
            resp = await page.context.request.get(
                url,
                headers={"Referer": "https://weibo.com/", "Origin": "https://weibo.com"},
            )
            if resp.status != 200:
                logger.warning(f"[weibo] Playback download HTTP {resp.status}")
                return None
            body = await resp.body()
            if len(body) < 2000:
                logger.warning(f"[weibo] Playback too small ({len(body)}B)")
                return None

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp4")
            os.close(tmp_fd)
            with open(tmp_path, "wb") as f:
                f.write(body)
            logger.info(f"[weibo] Playback downloaded: {len(body) / 1024 / 1024:.1f} MB")

            from video_processor import VideoProcessor

            vp = VideoProcessor()

            def _transcribe(audio_file: str) -> str | None:
                audio_b64 = vp._audio_to_base64(audio_file)
                if not audio_b64:
                    return None
                return vp._transcribe_audio_with_mimo(audio_b64)

            # Fast path: stream-copy AAC out of the MP4 container
            text = None
            audio_path = vp._extract_audio(tmp_path)
            if audio_path:
                try:
                    text = _transcribe(audio_path)
                finally:
                    vp.cleanup(audio_path)

            # Suspect answer / unusable track -> retry once on a forced AAC
            # re-encode (handles mp3/amr/odd containers and some transcribe flakiness)
            if self._narration_suspect(text) or (
                text and vp._transcript_has_annotations(text)
            ):
                converted_path = self._convert_to_m4a(tmp_path)
                if converted_path:
                    try:
                        retry_text = _transcribe(converted_path)
                    finally:
                        vp.cleanup(converted_path)
                        converted_path = None
                    if not self._narration_suspect(retry_text) and not (
                        retry_text and vp._transcript_has_annotations(retry_text)
                    ):
                        return retry_text
                    return None  # both sources polluted/garbage: no narration row
            return text

        except Exception:
            logger.warning("[weibo] Narration extraction failed", exc_info=True)
            return None
        finally:
            for p in (tmp_path, converted_path):
                if p:
                    try:
                        os.unlink(p)
                    except Exception:
                        pass

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

            # Narration via statuses/show API (video URLs are not exposed in DOM:
            # the player uses MSE/blob sources and does not autoplay)
            narration = None
            if getattr(config, "MIMO_API_KEY", ""):
                try:
                    status_id = self._extract_status_id(url)
                    if status_id:
                        status = await self._api_fetch(
                            page, f"https://weibo.com/ajax/statuses/show?id={status_id}"
                        )
                        if isinstance(status, dict) and status.get("ok", 0) == 1:
                            narration = await self._extract_narration(page, status)
                        else:
                            logger.warning(
                                f"[weibo] statuses/show failed for {status_id}: "
                                f"{str(status)[:120]}"
                            )
                    else:
                        logger.debug(f"[weibo] Not a detail URL, skipping narration: {url}")
                except Exception:
                    logger.warning("[weibo] Narration fetch failed", exc_info=True)

            debug_dir = config.OUTPUT_DIR / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            serial = url.split("/")[-1][:20]
            await page.screenshot(path=str(debug_dir / f"weibo_{serial}_final.png"), full_page=False)

            return {
                "post_content": post_content,
                "comments": all_comments,
                "video_url": None,
                "audio_url": None,
                "narration": narration,
            }

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
