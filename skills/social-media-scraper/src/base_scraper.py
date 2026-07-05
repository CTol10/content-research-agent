import asyncio
import json
import logging
import os
import random
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

import config

logger = logging.getLogger(__name__)


def _detect_browser_channel() -> str | None:
    """Detect available system browser. Returns channel name or None."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            # Try Chrome first
            try:
                b = p.chromium.launch(channel="chrome", headless=True)
                b.close()
                return "chrome"
            except Exception:
                pass
            # Try Edge (pre-installed on Windows)
            try:
                b = p.chromium.launch(channel="msedge", headless=True)
                b.close()
                return "msedge"
            except Exception:
                pass
    except Exception:
        pass
    return None


# Cache result so we only check once per session
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
        # Use system browser (Chrome > Edge) — no need to download Chromium
        channel = _get_browser_channel()
        if channel:
            args["channel"] = channel
        return args

    async def start(self, headless: bool = False):
        """Launch persistent browser context — login state is automatically kept."""
        # Auto-detect headless on Linux without a display server
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
        """Open browser, wait for user login, auto-detect success via URL change.

        Returns True if login succeeded, False on timeout.
        """
        await self.start()
        page = await self._context.new_page()
        login_url = self._get_login_url()
        await page.goto(login_url, timeout=30000)
        print(f"[{self.platform_name}] 浏览器已打开，请登录。等待自动检测...")

        login_indicators = ("login", "passport", "signin", "sign-in", "register", "signup")
        elapsed = 0
        interval = 3
        while elapsed < timeout:
            await asyncio.sleep(interval)
            elapsed += interval
            current_url = page.url.lower()
            if not any(ind in current_url for ind in login_indicators):
                print(f"[{self.platform_name}] 检测到登录成功！")
                # Save cookie JSON so check_cookies() can detect login state
                await self.save_cookies(self._context)
                print(f"[{self.platform_name}] Cookie 已保存。")
                await self.stop()
                return True

        print(f"[{self.platform_name}] 登录超时（{timeout}秒）。")
        await self.stop()
        return False

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
