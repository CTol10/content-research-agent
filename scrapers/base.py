import asyncio
import json
import logging
import os
import random
import sys
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

import config

logger = logging.getLogger(__name__)


def _detect_browser_channel() -> str | None:
    """Detect available system browser. Returns channel name or None."""
    import sys
    # In frozen exe, skip Playwright's own browser check and detect directly
    if getattr(sys, 'frozen', False):
        import winreg
        for reg_key, channel in [
            (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe", "chrome"),
            (r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe", "msedge"),
        ]:
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_key)
                val, _ = winreg.QueryValueEx(key, "")
                winreg.CloseKey(key)
                if val and Path(val).exists():
                    return channel
            except (OSError, FileNotFoundError):
                pass
        for path_pattern, channel in [
            (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "chrome"),
            (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "msedge"),
        ]:
            if Path(path_pattern).exists():
                return channel
        return None

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(channel="chrome", headless=True)
                b.close()
                return "chrome"
            except Exception:
                pass
            try:
                b = p.chromium.launch(channel="msedge", headless=True)
                b.close()
                return "msedge"
            except Exception:
                pass
    except Exception:
        pass
    return None


def _detect_browser_channel_fallback() -> str | None:
    """Fallback: detect browser by checking common install paths."""
    from pathlib import Path
    for path_pattern, channel in [
        (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "chrome"),
        (r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", "chrome"),
        (r"C:\Program Files\Microsoft\Edge\Application\msedge.exe", "msedge"),
        (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", "msedge"),
        (r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe", "chrome"),
    ]:
        if Path(path_pattern).exists():
            return channel
    return None


_UNSET = object()
_BROWSER_CHANNEL: str | None = _UNSET


def _get_browser_channel() -> str | None:
    global _BROWSER_CHANNEL
    if _BROWSER_CHANNEL is _UNSET:
        _BROWSER_CHANNEL = _detect_browser_channel()
    return _BROWSER_CHANNEL


class BaseScraper:
    platform_name: str = "base"
    CDP_PORT = 9233  # Default remote debugging port for login

    # Shared persistent user-data directory so login state survives across runs
    _USER_DATA_DIR = config.COOKIE_DIR / "_browser_profile"

    def __init__(self):
        self.cookie_path = config.COOKIE_DIR / f"{self.platform_name}.json"
        self._playwright = None
        self._context: BrowserContext | None = None

    def _browser_launch_args(self) -> dict:
        """Build common browser launch kwargs."""
        args = {
            "user_data_dir": str(self._USER_DATA_DIR),
            "viewport": {"width": 1280, "height": 900},
            "user_agent": config.DEFAULT_USER_AGENT,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
            ],
        }
        channel = _get_browser_channel()
        if not channel:
            channel = _detect_browser_channel_fallback()
        if channel:
            args["channel"] = channel
        return args

    async def start(self, headless: bool = False):
        """Launch persistent browser context — login state is automatically kept."""
        if not headless and config.SYSTEM == "Linux" and not os.environ.get("DISPLAY"):
            headless = True
            logger.info("No DISPLAY found on Linux, enabling headless mode")

        self._USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        launch_args = self._browser_launch_args()
        launch_args["headless"] = headless
        self._context = await self._playwright.chromium.launch_persistent_context(**launch_args)
        self._context.on("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))

    async def start_with_cdp(self, cdp_port: int | None = None):
        """Launch persistent browser with remote debugging for reconnection."""
        port = cdp_port if cdp_port is not None else self.CDP_PORT
        self._USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        launch_args = self._browser_launch_args()
        launch_args["headless"] = False
        launch_args["args"].append(f"--remote-debugging-port={port}")
        self._context = await self._playwright.chromium.launch_persistent_context(**launch_args)
        self._cdp_port = port

    async def stop(self):
        """Close browser."""
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()

    async def new_context(self) -> BrowserContext:
        """Return the shared persistent context."""
        if self._context is None:
            raise RuntimeError("Browser not started. Call start() first.")
        return self._context

    async def _install_media_pause_guard(self, page: Page):
        """Install a page-level guard that keeps HTML media elements paused."""
        await page.add_init_script("""
            (() => {
                if (window.__scraperMediaGuardInstalled) return;
                window.__scraperMediaGuardInstalled = true;

                const bindMedia = (media) => {
                    if (!media || media.dataset.scraperPauseBound === '1') return;
                    media.dataset.scraperPauseBound = '1';
                    media.autoplay = false;
                    media.loop = false;
                    media.muted = true;
                    media.addEventListener('play', () => {
                        try { media.pause(); } catch (e) {}
                    }, true);
                    media.addEventListener('loadeddata', () => {
                        try { media.pause(); } catch (e) {}
                    }, true);
                };

                window.__scraperPauseMedia = () => {
                    const mediaNodes = Array.from(document.querySelectorAll('video, audio'));
                    mediaNodes.forEach(bindMedia);
                    mediaNodes.forEach((media) => {
                        try {
                            media.autoplay = false;
                            media.loop = false;
                            media.muted = true;
                            media.pause();
                        } catch (e) {}
                    });
                };

                const install = () => {
                    try { window.__scraperPauseMedia(); } catch (e) {}
                    if (!window.__scraperMediaPauseTimer) {
                        window.__scraperMediaPauseTimer = setInterval(() => {
                            try { window.__scraperPauseMedia(); } catch (e) {}
                        }, 500);
                    }
                    if (!window.__scraperMediaObserver) {
                        const root = document.documentElement || document.body;
                        if (!root) return;
                        window.__scraperMediaObserver = new MutationObserver(() => {
                            try { window.__scraperPauseMedia(); } catch (e) {}
                        });
                        window.__scraperMediaObserver.observe(root, {
                            childList: true,
                            subtree: true,
                            attributes: true,
                            attributeFilter: ['src', 'autoplay'],
                        });
                    }
                };

                if (document.readyState === 'loading') {
                    document.addEventListener('DOMContentLoaded', install, { once: true });
                } else {
                    install();
                }
                window.addEventListener('load', install, { once: true });
            })();
        """)

    async def _pause_all_media(self, page: Page) -> dict:
        """Pause all current HTML media elements and return a playback summary."""
        return await page.evaluate("""
            () => {
                if (typeof window.__scraperPauseMedia === 'function') {
                    try { window.__scraperPauseMedia(); } catch (e) {}
                }

                const mediaNodes = Array.from(document.querySelectorAll('video, audio'));
                let playing = 0;
                let target = null;

                for (const media of mediaNodes) {
                    const rect = media.getBoundingClientRect();
                    const visible = rect.width > 0 && rect.height > 0;
                    if (!media.paused) {
                        playing += 1;
                    }
                    if (!target && visible) {
                        target = {
                            x: rect.left + rect.width / 2,
                            y: rect.top + rect.height / 2,
                            paused: !!media.paused,
                        };
                    }
                }

                return {
                    count: mediaNodes.length,
                    playing: playing,
                    target: target,
                };
            }
        """)

    async def _load_cookies(self, context: BrowserContext) -> bool:
        """Load cookies from file. Returns True if loaded."""
        if self.cookie_path.exists():
            try:
                cookies = json.loads(self.cookie_path.read_text(encoding="utf-8"))
                await context.add_cookies(cookies)
                logger.info(f"[{self.platform_name}] Cookie loaded from {self.cookie_path}")
                return True
            except Exception as e:
                logger.warning(f"[{self.platform_name}] Failed to load cookies: {e}")
        return False

    async def save_cookies(self, context: BrowserContext):
        """Save current cookies to file."""
        config.COOKIE_DIR.mkdir(parents=True, exist_ok=True)
        cookies = await context.cookies()
        self.cookie_path.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"[{self.platform_name}] Cookies saved to {self.cookie_path}")

    async def login_interactive(self, wait_seconds: int = 300):
        """Open browser for manual login, save cookies after wait.

        With persistent context, login state is automatically kept in the
        user-data directory. Cookie file is also saved for backup.
        """
        await self.start()
        page = await self._context.new_page()
        login_url = self._get_login_url()
        await page.goto(login_url)
        print(f"\n[{self.platform_name}] 浏览器已打开，请在浏览器中手动登录。")

        if wait_seconds > 0:
            print(f"等待 {wait_seconds} 秒后自动保存 Cookie...")
            try:
                await asyncio.sleep(wait_seconds)
                await self.save_cookies(self._context)
                print(f"[{self.platform_name}] Cookie 已保存。")
            finally:
                await self.stop()
        else:
            signal_file = config.COOKIE_DIR / f".{self.platform_name}_login_done"
            if signal_file.exists():
                signal_file.unlink()
            print(f"[{self.platform_name}] 等待登录完成信号: {signal_file}")
            while not signal_file.exists():
                await asyncio.sleep(2)
            signal_file.unlink()
            await self.save_cookies(self._context)
            print(f"[{self.platform_name}] Cookie 已保存。")
            await self.stop()

    async def wait_for_login(self, timeout: int = 300) -> bool:
        """Open browser, wait for user to confirm login via console input.

        Returns True if login succeeded, False on timeout.
        """
        await self.start()
        page = await self._context.new_page()
        login_url = self._get_login_url()
        await page.goto(login_url, timeout=30000)
        print(f"\n{'='*50}")
        print(f"  [{self.platform_name}] 浏览器已打开，请在浏览器中完成登录")
        print(f"  登录完成后，请回到此处按 Enter 键确认")
        print(f"{'='*50}")
        sys.stdout.flush()

        # Read from stdin in a thread — input() can be unreliable across threads,
        # so we use sys.stdin.readline() instead.
        import concurrent.futures
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(pool, sys.stdin.readline),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                print(f"[{self.platform_name}] 等待超时（{timeout}秒）。")
                await self.stop()
                return False

        await self.save_cookies(self._context)
        print(f"[{self.platform_name}] Cookie 已保存。")
        await self.stop()
        return True

    @staticmethod
    async def save_cookies_from_cdp(platform_name: str, cookie_path: Path, cdp_port: int = 9233):
        """Reconnect to an open browser via CDP, save cookies, and close it."""
        try:
            pw = await async_playwright().start()
            browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
            contexts = browser.contexts
            if contexts:
                cookies = await contexts[0].cookies()
                cookie_path.parent.mkdir(parents=True, exist_ok=True)
                cookie_path.write_text(
                    json.dumps(cookies, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"[{platform_name}] Cookie 已保存到 {cookie_path}")
            else:
                print(f"[{platform_name}] 警告: 没有找到浏览器上下文，Cookie 未保存")
            await browser.close()
            await pw.stop()
        except Exception as e:
            print(f"[{platform_name}] 保存 Cookie 失败: {e}")

    def _get_login_url(self) -> str:
        """Return the platform's login/home page URL. Override in subclass."""
        raise NotImplementedError

    async def random_delay(self):
        """Random delay between page actions."""
        delay = random.uniform(config.MIN_DELAY, config.MAX_DELAY)
        await asyncio.sleep(delay)

    async def scroll_to_bottom(self, page: Page, max_scroll: int = config.MAX_SCROLL) -> int:
        """Scroll page to load more content. Returns number of scrolls performed."""
        prev_height = 0
        scrolls = 0
        for _ in range(max_scroll):
            height = await page.evaluate("document.body.scrollHeight")
            if height == prev_height:
                break
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await self.random_delay()
            prev_height = height
            scrolls += 1
        return scrolls

    async def scrape(self, url: str) -> dict:
        """Scrape comments from URL.

        Returns:
            {"post_content": str, "comments": [(nickname, comment_text), ...]}
        """
        raise NotImplementedError

    async def _get_expected_comment_count(self, page) -> int | None:
        """Read the expected comment count from the page. Override in subclass."""
        return None

    async def _verify_comment_count(self, page, comments: list[tuple[str, str]]):
        """Compare extracted count against the page's displayed count."""
        expected = await self._get_expected_comment_count(page)
        if expected is None:
            return
        actual = len(comments)
        if actual != expected:
            logger.warning(
                f"[{self.platform_name}] Comment count mismatch: "
                f"expected {expected}, got {actual} (diff={actual - expected:+d})"
            )
        else:
            logger.info(f"[{self.platform_name}] Comment count verified: {actual} == {expected}")

    # ---- Video capture infrastructure ----

    # Known video CDN domains - capture even without <video> DOM element
    _VIDEO_CDN_PATTERNS = [
        "xhscdn.com",
        "douyinvod.com",
        "toutiaovod.com",
        "ixigua.com",
        "bytevcloudcdn.com",
    ]

    def _setup_video_capture(self, page: Page):
        """Register network response listener to capture video and audio URLs."""
        self._video_urls = []
        self._video_content_lengths = {}
        self._audio_urls = []
        self._audio_content_lengths = {}
        self._video_enabled = False
        self._video_exclude = getattr(config, "VIDEO_URL_EXCLUDE_KEYWORDS", ["ad", "tracker", "analytics", "beacon"])

        def on_response(response):
            # Always try to capture known video CDN requests
            if not self._video_enabled:
                url_lower = response.url.lower()
                if any(pat in url_lower for pat in self._VIDEO_CDN_PATTERNS):
                    self._capture_media_response(response)
                return
            self._capture_media_response(response)

        page.on("response", on_response)
        logger.debug(f"[{self.platform_name}] Media capture listener registered (disabled until video element found)")

    async def _check_video_element(self, page) -> bool:
        """Check if page has a <video> DOM element. Enables video capture if found."""
        try:
            has_video = await page.evaluate("""
                () => {
                    const videos = document.querySelectorAll('video');
                    for (const v of videos) {
                        const rect = v.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) return true;
                    }
                    return false;
                }
            """)
            self._video_enabled = has_video
            if has_video:
                logger.info(f"[{self.platform_name}] Video DOM element found, enabling video capture")
            else:
                logger.info(f"[{self.platform_name}] No video DOM element found, skipping video capture")
            return has_video
        except Exception as e:
            logger.debug(f"[{self.platform_name}] Video element check failed: {e}")
            self._video_enabled = False
            return False

    async def _try_extract_video_from_dom(self, page):
        """Extract video URL from DOM <video> element. Replaces HEVC/audio-only URLs with standard MP4."""
        import asyncio
        # Wait up to 5s for video to have a source (retry every 0.5s)
        video_url = None
        for _ in range(10):
            try:
                video_url = await page.evaluate("""
                    () => {
                        const VIDEO_CDNS = ['douyinvod', 'ixigua', 'toutiaovod', 'bytevcloudcdn', 'xhscdn.com'];
                        const videos = document.querySelectorAll('video');
                        for (const v of videos) {
                            const rect = v.getBoundingClientRect();
                            if (rect.width === 0 || rect.height === 0) continue;
                            // Collect all HTTP sources from this video element
                            const urls = [];
                            if (v.currentSrc && v.currentSrc.startsWith('http')) urls.push(v.currentSrc);
                            if (v.src && v.src.startsWith('http') && v.src !== v.currentSrc) urls.push(v.src);
                            for (const s of v.querySelectorAll('source')) {
                                if (s.src && s.src.startsWith('http') && !urls.includes(s.src)) urls.push(s.src);
                            }
                            if (urls.length === 0) continue;
                            // Filter to known video CDNs only (skip tracking pixels like mssdk.bytedance.com)
                            const videoCdnUrls = urls.filter(u => {
                                const lower = u.toLowerCase();
                                return VIDEO_CDNS.some(cdn => lower.includes(cdn));
                            });
                            if (videoCdnUrls.length === 0) continue;
                            // Prefer video URLs over audio-only URLs
                            const nonAudio = videoCdnUrls.filter(u => !u.includes('media-audio'));
                            if (nonAudio.length > 0) return nonAudio[0];
                            return videoCdnUrls[0];
                        }
                        return null;
                    }
                """)
                if video_url:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)

        if not video_url:
            logger.debug(f"[{self.platform_name}] No video URL found in DOM")
            return

        logger.debug(f"[{self.platform_name}] DOM video URL: {video_url[:80]}")

        # If we already have captured video URLs, check if DOM provides a better (standard MP4) one
        if self._video_urls:
            def _is_media_only(u):
                lower = u.lower()
                return "media-video-hvc1" in lower or "media-video-avc1" in lower

            def _is_audio_only(u):
                return "media-audio" in u.lower()

            has_media_only = any(_is_media_only(u) for u in self._video_urls)
            dom_is_video = not _is_media_only(video_url) and not _is_audio_only(video_url)
            if has_media_only and dom_is_video:
                self._video_urls = [video_url]
                self._video_content_lengths = {video_url: 0}
                logger.info(f"[{self.platform_name}] Replaced media-only URL with standard MP4 from DOM: {video_url[:80]}")
            else:
                logger.debug(f"[{self.platform_name}] DOM URL not better (dom_is_video={dom_is_video}, captured_media_only={has_media_only})")
            return

        self._video_urls.append(video_url)
        logger.info(f"[{self.platform_name}] Video URL extracted from DOM: {video_url[:80]}")

    def _capture_media_response(self, response):
        """Check if response is a video or audio and record its URL."""
        try:
            url = response.url
            content_type = response.headers.get("content-type", "")
            headers = response.headers

            # Filter out ads/tracking first
            from urllib.parse import urlparse
            parsed = urlparse(url.lower())
            domain_parts = parsed.hostname.split(".") if parsed.hostname else []
            path_segments = [s for s in parsed.path.split("/") if s]
            check_parts = set(domain_parts + path_segments)
            if any(kw in check_parts for kw in self._video_exclude):
                return

            # Check if audio by content-type OR by URL pattern (media-audio-und-mp4a)
            is_audio = "audio" in content_type or "media-audio" in url.lower()
            if is_audio:
                content_length = int(headers.get("content-length", 0))
                if content_length > 10000:  # Skip tiny audio (tracking pixels etc.)
                    self._audio_urls.append(url)
                    self._audio_content_lengths[url] = content_length
                    logger.debug(f"[{self.platform_name}] Audio captured: {url[:80]} ({content_length / 1024 / 1024:.1f}MB)")
                return

            # Check if video by content-type or URL extension
            is_video = "video" in content_type
            if not is_video:
                video_extensions = getattr(config, "VIDEO_EXTENSIONS", [".mp4", ".m3u8", ".webm", ".ts"])
                url_lower = url.split("?")[0].lower()
                is_video = any(url_lower.endswith(ext) for ext in video_extensions)

            # Also check for video CDN patterns in URL (for MSE/blob-based players)
            if not is_video:
                video_cdn_patterns = ["douyinvod", "ixigua", "toutiaovod", "bytevcloudcdn", "xhscdn.com"]
                url_lower = url.lower()
                is_video = any(pat in url_lower for pat in video_cdn_patterns)

            if not is_video:
                return

            content_length = int(headers.get("content-length", 0))
            self._video_urls.append(url)
            self._video_content_lengths[url] = content_length
            logger.debug(f"[{self.platform_name}] Video captured: {url[:80]} ({content_length / 1024 / 1024:.1f}MB)")

        except Exception:
            pass  # Don't let capture errors affect scraping

    def _get_primary_video_url(self) -> str | None:
        """Return the URL of the largest captured video, preferring standard MP4, or None."""
        if not self._video_urls:
            logger.debug(f"[{self.platform_name}] No video URLs captured (audio_urls={len(self._audio_urls)})")
            return None

        # Deduplicate
        unique_urls = list(dict.fromkeys(self._video_urls))
        logger.debug(f"[{self.platform_name}] Captured {len(unique_urls)} unique video URLs")

        if len(unique_urls) == 1:
            return unique_urls[0]

        # Prefer standard MP4 URLs (avoid media-video-hvc1/avc1 which are video-only MSE streams)
        def is_standard_mp4(u):
            lower = u.lower()
            return "media-video-hvc1" not in lower and "media-video-avc1" not in lower

        standard = [u for u in unique_urls if is_standard_mp4(u)]
        candidates = standard if standard else unique_urls

        # Return largest by content-length
        best = max(candidates, key=lambda u: self._video_content_lengths.get(u, 0))
        logger.info(f"[{self.platform_name}] Selected primary video: {best[:80]}")
        return best

    def _get_primary_audio_url(self) -> str | None:
        """Return the URL of the largest captured audio, or None."""
        if not self._audio_urls:
            return None

        unique_urls = list(dict.fromkeys(self._audio_urls))
        if len(unique_urls) == 1:
            return unique_urls[0]

        best = max(unique_urls, key=lambda u: self._audio_content_lengths.get(u, 0))
        logger.info(f"[{self.platform_name}] Selected primary audio: {best[:80]}")
        return best
