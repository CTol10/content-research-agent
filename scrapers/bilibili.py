"""Bilibili (哔哩哔哩) comment scraper.

Uses Bilibili's public REST APIs via page.evaluate() to fetch video info
and paginate through comments (including folded sub-replies). Follows the
same API-first pattern as Weibo.

API endpoints:
  - Video info:  GET https://api.bilibili.com/x/web-interface/view?bvid={bvid}
  - Top-level:   GET https://api.bilibili.com/x/v2/reply/main?oid={aid}&type=1&mode=3&next={cursor}
  - Sub-replies: GET https://api.bilibili.com/x/v2/reply/reply?oid={aid}&type=1&root={rpid}&pn={page}
  - DASH audio:  GET https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}&fnval=16
"""
import asyncio
import logging
import os
import re
import tempfile

import config
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class BilibiliScraper(BaseScraper):
    platform_name = "bilibili"
    CDP_PORT = 9237

    # ── URL helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _extract_bvid(url: str) -> str | None:
        """Extract BV ID from a Bilibili URL."""
        match = re.search(r'BV[a-zA-Z0-9]{10}', url)
        return match.group(0) if match else None

    # ── API call via browser context ─────────────────────────────────────

    @staticmethod
    async def _api_fetch(page, url: str) -> dict:
        """Call a Bilibili API endpoint from within the browser context.

        This carries the page's cookies and Referer, which are required
        by Bilibili's anti-crawl checks (otherwise code -352).
        """
        js = """
            async (url) => {
                const resp = await fetch(url, { credentials: 'include' });
                if (!resp.ok) return { code: resp.status, message: resp.statusText };
                try { return await resp.json(); } catch (e) { return { code: -1, message: String(e) }; }
            }
        """
        return await page.evaluate(js, url)

    # ── Core scrape ──────────────────────────────────────────────────────

    def _get_login_url(self) -> str:
        return "https://www.bilibili.com"

    async def scrape(self, url: str) -> dict:
        context = await self.new_context()
        page = await context.new_page()

        try:
            # ---- Navigate and extract BVID ----
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            self._setup_video_capture(page)
            await self._install_media_pause_guard(page)

            logger.info(f"[bilibili] Navigating to {url}")
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await asyncio.sleep(3)

            # Handle b23.tv short links: extract BVID from final URL
            final_url = page.url
            bvid = self._extract_bvid(final_url)
            if not bvid:
                raise ValueError(f"无法从URL提取BV号: {final_url}")
            logger.info(f"[bilibili] BVID={bvid}")

            # ---- Step 1: Get video info via API ----
            view_api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
            view_resp = await self._api_fetch(page, view_api)

            if view_resp.get("code") != 0:
                msg = view_resp.get("message", "未知错误")
                raise ValueError(f"获取视频信息失败 (code={view_resp.get('code')}): {msg}")

            video_data = view_resp.get("data", {})
            title = (video_data.get("title") or "").strip()
            desc = (video_data.get("desc") or "").strip()
            aid = video_data.get("aid", 0)
            cid = video_data.get("cid", 0)
            logger.info(f"[bilibili] Title: {title[:60]}... aid={aid} cid={cid}")

            # ---- Post content ----
            post_parts = []
            if title:
                post_parts.append(title)
            if desc and desc != title:
                post_parts.append(desc)
            post_content = "\n".join(post_parts) if post_parts else "[无正文]"

            # ---- Video capture (DOM-based, may catch <video> element) ----
            await self._check_video_element(page)
            await self._try_extract_video_from_dom(page)

            # ---- Audio URL for narration (via DASH playurl API) ----
            narration = await self._extract_narration(page, bvid, cid, aid)

            # ---- Step 2: Paginate through comments ----
            all_comments: list[tuple[str, str]] = []
            seen: set[str] = set()
            max_pages = 100
            cursor = 0
            all_count = 0
            is_logged_in = None  # None = unknown

            for page_num in range(max_pages):
                mode = 3 if is_logged_in is not False else 2
                comment_api = (
                    f"https://api.bilibili.com/x/v2/reply/main"
                    f"?oid={aid}&type=1&mode={mode}&next={cursor}"
                )
                comment_resp = await self._api_fetch(page, comment_api)

                code = comment_resp.get("code")
                if code != 0:
                    logger.warning(
                        f"[bilibili] Comment API error (code={code}): "
                        f"{comment_resp.get('message', '')}"
                    )
                    break

                data = comment_resp.get("data") or {}
                replies = data.get("replies") or []

                if not replies:
                    logger.info(f"[bilibili] No more replies at page {page_num}")
                    break

                # Parse replies — this also fetches folded sub-replies
                await self._parse_replies_async(page, aid, replies, all_comments, seen)

                # Check pagination state from cursor
                cursor_data = data.get("cursor") or {}
                if page_num == 0:
                    all_count = cursor_data.get("all_count", 0)
                    top_count = len(replies)
                    is_end_on_first = cursor_data.get("is_end", False)
                    if is_end_on_first and top_count < 10 and all_count > top_count:
                        is_logged_in = False
                        logger.warning(
                            f"[bilibili] 未登录状态，仅能获取部分热门评论 "
                            f"({len(all_comments)}/{all_count})。"
                            f"请运行 --login --platforms bilibili 登录后重试。"
                        )
                    else:
                        is_logged_in = True

                logger.info(
                    f"[bilibili] Page {page_num}: {len(replies)} top-level, "
                    f"total extracted={len(all_comments)}/{all_count}"
                )

                # Check if we've hit the end
                is_end = cursor_data.get("is_end", False)
                if is_end:
                    logger.info(f"[bilibili] Pagination complete (is_end=true)")
                    break

                next_cursor = cursor_data.get("next", 0)
                if not next_cursor or next_cursor == cursor:
                    logger.info(f"[bilibili] Pagination complete (no next cursor)")
                    break

                cursor = next_cursor
                await self.random_delay()

            logger.info(f"[bilibili] Total comments extracted: {len(all_comments)}")

            # ---- Debug screenshot ----
            debug_dir = config.OUTPUT_DIR / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            await page.screenshot(
                path=str(debug_dir / f"bilibili_{bvid}_final.png"),
                full_page=False,
            )

            return {
                "post_content": post_content,
                "comments": all_comments,
                "video_url": None,
                "audio_url": None,
                "narration": narration,
            }

        except Exception:
            logger.error(f"[bilibili] Failed to scrape {url}", exc_info=True)
            raise
        finally:
            await page.close()

    # ── Audio URL extraction for narration ───────────────────────────────

    @staticmethod
    async def _convert_to_m4a(input_path: str) -> str | None:
        """Convert DASH audio (MP4/ISOM container) to proper M4A (AAC).

        Bilibili DASH audio streams use ftypisom (MP4) containers that MiMo
        rejects as "invalid audio format". Re-encode to AAC-in-M4A (ftypM4A42)
        via ffmpeg.
        """
        try:
            import subprocess

            import imageio_ffmpeg

            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            output_path = input_path.rsplit(".", 1)[0] + "_conv.m4a"

            # Re-encode to AAC (handles container/codec issues)
            cmd = [
                ffmpeg_exe,
                "-i", input_path,
                "-vn",
                "-c:a", "aac",
                "-b:a", "128k",
                output_path,
                "-y",
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0 and os.path.exists(output_path):
                logger.info(
                    f"[bilibili] Audio converted to M4A: "
                    f"{os.path.getsize(output_path) / 1024:.0f} KB"
                )
                return output_path

            logger.warning(
                f"[bilibili] ffmpeg conversion failed: {result.stderr[:200]}"
            )
            return None
        except Exception:
            logger.warning(
                "[bilibili] Audio conversion failed", exc_info=True
            )
            return None

    async def _fetch_audio_url(self, page, bvid: str, cid: int) -> str | None:
        """Fetch the best DASH audio track URL via Bilibili's playurl API.

        Bilibili serves video+audio as separate DASH streams.  The audio
        track (AAC) can be passed to VideoProcessor for narration extraction.
        """
        try:
            playurl_api = (
                f"https://api.bilibili.com/x/player/playurl"
                f"?bvid={bvid}&cid={cid}&qn=0&fnval=16&fnver=0&fourk=1"
            )
            resp = await self._api_fetch(page, playurl_api)
            if resp.get("code") != 0:
                logger.warning(
                    f"[bilibili] playurl API failed: code={resp.get('code')}"
                )
                return None

            dash = (resp.get("data") or {}).get("dash") or {}
            audio_tracks = dash.get("audio") or []
            if not audio_tracks:
                logger.warning("[bilibili] No DASH audio tracks found")
                return None

            # Pick highest-bandwidth audio track for best transcription quality
            best = max(audio_tracks, key=lambda t: t.get("bandwidth", 0))
            base_url = best.get("base_url", "")
            backup_urls = best.get("backup_url", []) or []
            url = base_url or (backup_urls[0] if backup_urls else "")
            if url:
                logger.info(
                    f"[bilibili] DASH audio: bandwidth={best.get('bandwidth')}, "
                    f"codecs={best.get('codecs')}"
                )
                return url
        except Exception:
            logger.warning("[bilibili] Failed to fetch DASH audio URL", exc_info=True)
        return None

    async def _extract_narration(
        self, page, bvid: str, cid: int, aid: int
    ) -> str | None:
        """Fetch DASH audio and transcribe via MiMo API.

        Downloads audio via Playwright's context.request (shares browser
        cookies, bypasses CORS), then reuses VideoProcessor's base64 + MiMo
        transcription pipeline.
        """
        audio_url = await self._fetch_audio_url(page, bvid, cid)
        if not audio_url:
            return None

        tmp_path = None
        converted_path = None
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".m4a")
            os.close(tmp_fd)

            # Download via Playwright (has cookies + Referer, bypasses CORS)
            logger.info(f"[bilibili] Downloading audio via Playwright...")
            resp = await page.context.request.get(audio_url, headers={
                "Referer": "https://www.bilibili.com",
                "Origin": "https://www.bilibili.com",
            })
            if resp.status != 200:
                logger.warning(f"[bilibili] Audio download HTTP {resp.status}")
                return None

            body = await resp.body()
            file_size = len(body)
            if file_size < 2000:
                logger.warning(f"[bilibili] Audio too small ({file_size}B)")
                return None

            with open(tmp_path, "wb") as f:
                f.write(body)
            logger.info(f"[bilibili] Audio downloaded: {file_size / 1024:.0f} KB")

            # Bilibili DASH audio is served in MP4/ISOM container (ftypisom),
            # not proper M4A (ftypM4A42). MiMo checks the actual container format,
            # so we must re-encode to AAC-in-M4A via ffmpeg.
            converted_path = await self._convert_to_m4a(tmp_path)
            if not converted_path:
                return None

            # Transcribe via VideoProcessor's internal pipeline
            from video_processor import VideoProcessor
            vp = VideoProcessor()

            audio_b64 = vp._audio_to_base64(converted_path)
            if not audio_b64:
                return None

            transcription = vp._transcribe_audio_with_mimo(audio_b64)
            return transcription

        except Exception:
            logger.warning("[bilibili] Narration extraction failed", exc_info=True)
            return None
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            if converted_path:
                try:
                    os.unlink(converted_path)
                except Exception:
                    pass

    # ── Reply parsing (async — fetches folded sub-replies) ───────────────

    async def _parse_replies_async(
        self,
        page,
        aid: int,
        replies: list,
        all_comments: list[tuple[str, str]],
        seen: set[str],
    ):
        """Parse top-level replies + fetch all hidden sub-replies via API.

        Bilibili only returns ~2-3 visible sub-replies per top-level comment
        in the main API response. The rest (indicated by rcount) must be
        fetched via the sub-reply endpoint with pagination.
        """
        for reply in replies:
            member = reply.get("member") or {}
            nickname = (member.get("uname") or "").strip()
            content = ((reply.get("content") or {}).get("message") or "").strip()

            if nickname and content:
                key = f"{nickname}::{content[:50]}"
                if key not in seen:
                    seen.add(key)
                    all_comments.append((nickname, content))

            # Collect visible sub-replies
            sub_replies = reply.get("replies") or []

            # Check if there are hidden sub-replies to fetch
            rcount = reply.get("rcount", 0)
            if rcount > len(sub_replies):
                rpid = reply.get("rpid", 0)
                hidden_subs = await self._fetch_all_sub_replies(
                    page, aid, rpid, seen
                )
                # Merge: API returns all sub-replies (visible + hidden),
                # so we use the full set from the dedicated API call.
                if hidden_subs:
                    sub_replies = hidden_subs

            # Recurse into sub-replies (which may themselves have sub-replies)
            if sub_replies:
                await self._parse_replies_async(
                    page, aid, sub_replies, all_comments, seen
                )

    async def _fetch_all_sub_replies(
        self,
        page,
        aid: int,
        root_rpid: int,
        seen: set[str],
    ) -> list:
        """Fetch all pages of sub-replies for a specific root comment.

        Returns the complete list of reply dicts from the sub-reply API.
        """
        all_subs = []
        max_pages = 10  # safety limit per root comment
        for pn in range(1, max_pages + 1):
            api_url = (
                f"https://api.bilibili.com/x/v2/reply/reply"
                f"?oid={aid}&type=1&root={root_rpid}&pn={pn}"
            )
            resp = await self._api_fetch(page, api_url)
            if resp.get("code") != 0:
                break

            data = resp.get("data") or {}
            subs = data.get("replies") or []
            if not subs:
                break

            all_subs.extend(subs)

            # Check if all pages have been fetched
            page_info = data.get("page") or {}
            page_count = page_info.get("count", 0)
            if len(all_subs) >= page_count:
                break

            await asyncio.sleep(0.3)  # small delay between sub-reply pages
        return all_subs
