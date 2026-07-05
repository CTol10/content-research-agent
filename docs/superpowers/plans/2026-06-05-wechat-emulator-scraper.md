# WeChat Emulator Scraper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add WeChat Official Account (公众号) and WeChat Channels (视频号) scraping via Android emulator (LDPlayer) + Appium (UIAutomator2), integrating into the existing multi-platform scraper pipeline.

**Architecture:** Create `AppiumBaseScraper` as a parallel base class to `BaseScraper`, managing ADB connection and Appium session lifecycle. Two subclasses (`WechatOfficialScraper`, `WechatChannelsEmulatorScraper`) handle platform-specific scraping. Both implement the same `scrape(url) -> dict` interface so `main.py` can treat them identically to existing Playwright scrapers.

**Tech Stack:** Python 3.12+, Appium-Python-Client 3.x, ADB (bundled with LDPlayer), UiAutomator2 driver

---

### Task 1: Add emulator configuration to config.py

**Files:**
- Modify: `config.py`

- [ ] **Step 1: Add emulator constants to config.py**

Add the following block after the existing `CHROME_SCRAPE_PROFILE` line (line 74) in `config.py`:

```python
# === Emulator Configuration (LDPlayer + Appium) ===
EMULATOR_ADB_HOST = "127.0.0.1"
EMULATOR_ADB_PORT = 5555
APPIUM_SERVER_URL = "http://127.0.0.1:4723"
EMULATOR_DEVICE_NAME = "emulator-5554"
WECHAT_PACKAGE = "com.tencent.mm"
WECHAT_ACTIVITY = "com.tencent.mm.ui.LauncherUI"

# Emulator scraping delays (slower than browser to mimic human)
EMULATOR_MIN_DELAY = 2.0
EMULATOR_MAX_DELAY = 5.0

# Video capture via ADB screenrecord
EMULATOR_SCREENRECORD_MAX_SECONDS = 180
EMULATOR_PROXY_HOST = "127.0.0.1"
EMULATOR_PROXY_PORT = 8080
```

- [ ] **Step 2: Uncomment wechat and add wechat_channels to PLATFORM_PATTERNS**

In `config.py`, replace the `PLATFORM_PATTERNS` block:

```python
PLATFORM_PATTERNS: dict[str, list[str]] = {
    "douyin": ["iesdouyin.com", "douyin.com"],
    "xiaohongshu": ["xiaohongshu.com"],
    "wechat": ["mp.weixin.qq.com"],
    "wechat_channels": ["channels.weixin.qq.com"],
    "toutiao": ["toutiao.com"],
    "weibo": ["weibo.com"],
}
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `python -m pytest tests/test_config.py -v`
Expected: All tests pass. Note: `test_wechat_url` will now fail because wechat is no longer commented out — this is expected, fix it in the test.

- [ ] **Step 4: Fix test_wechat_url to expect "wechat" instead of None**

In `tests/test_config.py`, change:

```python
def test_wechat_url():
    # WeChat 公众号已启用（模拟器模式）
    assert match_platform("https://mp.weixin.qq.com/s?__biz=abc") == "wechat"
```

Add a new test for wechat_channels:

```python
def test_wechat_channels_url():
    assert match_platform("https://channels.weixin.qq.com/web/pages/feed/abc123") == "wechat_channels"
```

- [ ] **Step 5: Run tests again**

Run: `python -m pytest tests/test_config.py -v`
Expected: All tests pass including the new wechat_channels test.

- [ ] **Step 6: Commit**

```bash
git add config.py tests/test_config.py
git commit -m "feat: add emulator config and wechat/wechat_channels platform patterns"
```

---

### Task 2: Add Appium-Python-Client dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add dependency**

Append to `requirements.txt`:

```
Appium-Python-Client>=3.0.0
```

- [ ] **Step 2: Install it**

Run: `pip install Appium-Python-Client>=3.0.0`
Expected: Successfully installed (or already satisfied).

- [ ] **Step 3: Verify import works**

Run: `python -c "from appium import webdriver; print('Appium client OK')"`
Expected: `Appium client OK`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "feat: add Appium-Python-Client dependency"
```

---

### Task 3: Create AppiumBaseScraper

**Files:**
- Create: `scrapers/appium_base.py`

- [ ] **Step 1: Create the AppiumBaseScraper class**

Create `scrapers/appium_base.py`:

```python
# scrapers/appium_base.py
"""Base class for Appium-driven Android emulator scrapers.

Manages ADB connection, Appium WebDriver session, and common mobile
interaction helpers. Parallel to BaseScraper (Playwright) but for
Android app automation.
"""
import logging
import random
import subprocess
import time
from pathlib import Path

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import config

logger = logging.getLogger(__name__)


class AppiumBaseScraper:
    """Base class for scraping via Android emulator + Appium.

    Subclasses must set `platform_name` and implement `scrape(url)`.
    """

    platform_name: str = "base_emulator"

    # Config — override in subclass if needed
    ADB_HOST = config.EMULATOR_ADB_HOST
    ADB_PORT = config.EMULATOR_ADB_PORT
    APPIUM_URL = config.APPIUM_SERVER_URL
    DEVICE_NAME = config.EMULATOR_DEVICE_NAME
    WECHAT_PACKAGE = config.WECHAT_PACKAGE
    WECHAT_ACTIVITY = config.WECHAT_ACTIVITY

    def __init__(self):
        self._driver = None
        self._scrape_count = 0

    # ── Lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Connect ADB → create Appium session → verify WeChat ready."""
        self._ensure_adb_connected()
        self._create_session()
        self._ensure_wechat_ready()

    def stop(self) -> None:
        """Quit Appium session. ADB connection stays alive."""
        if self._driver:
            try:
                self._driver.quit()
                logger.info(f"[{self.platform_name}] Appium session closed")
            except Exception as e:
                logger.warning(f"[{self.platform_name}] Error closing session: {e}")
            finally:
                self._driver = None

    def scrape(self, url: str) -> dict:
        """Scrape comments from URL. Must be implemented by subclass.

        Returns:
            {"post_content": str, "comments": [(nickname, text), ...]}
        """
        raise NotImplementedError

    # ── ADB ──────────────────────────────────────────────────────

    def _ensure_adb_connected(self) -> None:
        """Start ADB server and connect to emulator."""
        adb_cmd = self._find_adb()
        logger.info(f"[{self.platform_name}] Using ADB: {adb_cmd}")

        # Start ADB server
        try:
            subprocess.run(
                [adb_cmd, "start-server"],
                capture_output=True, timeout=10,
            )
        except Exception as e:
            raise RuntimeError(
                f"ADB 启动失败。请确认已安装 ADB 或启动雷电模拟器。\n"
                f"错误: {e}"
            )

        # Connect to emulator
        target = f"{self.ADB_HOST}:{self.ADB_PORT}"
        result = subprocess.run(
            [adb_cmd, "connect", target],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout.strip()
        if "connected" not in output.lower() and "already" not in output.lower():
            raise RuntimeError(
                f"ADB 连接失败: {output}\n"
                f"请确认雷电模拟器已启动，ADB 端口为 {self.ADB_PORT}。"
            )
        logger.info(f"[{self.platform_name}] ADB connected to {target}")

        # Verify device appears
        result = subprocess.run(
            [adb_cmd, "devices"],
            capture_output=True, text=True, timeout=10,
        )
        if target not in result.stdout and "emulator" not in result.stdout:
            raise RuntimeError(
                f"ADB 设备列表中未找到模拟器。\n"
                f"输出: {result.stdout}\n"
                f"请确认雷电模拟器已启动。"
            )

    def _find_adb(self) -> str:
        """Find ADB executable. Try LDPlayer bundled ADB first, then system PATH."""
        # LDPlayer bundled ADB (common paths)
        ldplayer_paths = [
            Path(r"C:\LDPlayer\LDPlayer9\adb.exe"),
            Path(r"C:\LDPlayer\LDPlayer4\adb.exe"),
            Path(r"D:\LDPlayer\LDPlayer9\adb.exe"),
            Path(r"D:\LDPlayer\LDPlayer4\adb.exe"),
            Path(r"C:\leidian\LDPlayer9\adb.exe"),
        ]
        for p in ldplayer_paths:
            if p.exists():
                return str(p)

        # System ADB
        try:
            result = subprocess.run(
                ["adb", "version"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return "adb"
        except FileNotFoundError:
            pass

        raise RuntimeError(
            "找不到 ADB。请安装雷电模拟器（自带 ADB）或将 ADB 添加到 PATH。"
        )

    # ── Appium Session ───────────────────────────────────────────

    def _create_session(self) -> None:
        """Create Appium UiAutomator2 session."""
        options = UiAutomator2Options()
        options.platform_name = "Android"
        options.device_name = self.DEVICE_NAME
        options.app_package = self.WECHAT_PACKAGE
        options.app_activity = self.WECHAT_ACTIVITY
        options.no_reset = True
        options.auto_grant_permissions = True
        options.new_command_timeout = 300  # 5 min idle timeout

        try:
            self._driver = webdriver.Remote(
                command_executor=self.APPIUM_URL,
                options=options,
            )
            logger.info(
                f"[{self.platform_name}] Appium session created "
                f"(device={self.DEVICE_NAME})"
            )
        except Exception as e:
            raise RuntimeError(
                f"Appium session 创建失败。\n"
                f"请确认 Appium Server 已启动: appium\n"
                f"错误: {e}"
            )

    def _ensure_wechat_ready(self) -> None:
        """Check that WeChat is installed and at main screen (logged in)."""
        try:
            current = self._driver.current_activity
            logger.info(f"[{self.platform_name}] Current activity: {current}")
        except Exception as e:
            raise RuntimeError(
                f"无法获取当前 Activity。微信可能未安装。\n错误: {e}"
            )

    # ── Helpers ──────────────────────────────────────────────────

    def random_delay(self) -> None:
        """Sleep a random duration between actions."""
        delay = random.uniform(config.EMULATOR_MIN_DELAY, config.EMULATOR_MAX_DELAY)
        time.sleep(delay)

    def open_url_in_wechat(self, url: str) -> None:
        """Open URL in WeChat's built-in browser via Android Intent."""
        # URL-encode the URL for the shell command
        import shlex
        encoded_url = url.replace("&", "\\&")
        cmd = (
            f"am start -a android.intent.action.VIEW "
            f"-d {shlex.quote(encoded_url)} {self.WECHAT_PACKAGE}"
        )
        self._adb_shell(cmd)
        logger.info(f"[{self.platform_name}] Opened URL in WeChat: {url[:80]}")
        time.sleep(3)  # Wait for page to start loading

    def swipe_up(self, duration_ms: int = 800) -> None:
        """Swipe upward to scroll content down."""
        size = self._driver.get_window_size()
        x = size["width"] // 2
        start_y = int(size["height"] * 0.7)
        end_y = int(size["height"] * 0.3)
        self._driver.swipe(x, start_y, x, end_y, duration_ms)

    def swipe_down(self, duration_ms: int = 800) -> None:
        """Swipe downward to scroll content up."""
        size = self._driver.get_window_size()
        x = size["width"] // 2
        start_y = int(size["height"] * 0.3)
        end_y = int(size["height"] * 0.7)
        self._driver.swipe(x, start_y, x, end_y, duration_ms)

    def find_element(self, by, value, timeout: int = 10):
        """Find element with wait. Returns element or None if not found."""
        try:
            wait = WebDriverWait(self._driver, timeout)
            return wait.until(EC.presence_of_element_located((by, value)))
        except Exception:
            return None

    def find_elements(self, by, value, timeout: int = 10):
        """Find elements with wait. Returns list (empty if none found)."""
        try:
            wait = WebDriverWait(self._driver, timeout)
            wait.until(EC.presence_of_element_located((by, value)))
            return self._driver.find_elements(by, value)
        except Exception:
            return []

    def wait_for_element(self, by, value, timeout: int = 30):
        """Wait for element to appear. Raises TimeoutException if not found."""
        wait = WebDriverWait(self._driver, timeout)
        return wait.until(EC.presence_of_element_located((by, value)))

    def take_screenshot(self, name: str) -> str:
        """Take screenshot via ADB, save to output/debug/. Returns path."""
        debug_dir = config.OUTPUT_DIR / "debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        path = debug_dir / f"{self.platform_name}_{name}.png"
        self._driver.save_screenshot(str(path))
        logger.debug(f"[{self.platform_name}] Screenshot saved: {path}")
        return str(path)

    def switch_to_webview_context(self) -> bool:
        """Switch Appium context to WEBVIEW_com.tencent.mm. Returns True on success."""
        try:
            contexts = self._driver.contexts
            for ctx in contexts:
                if "WEBVIEW" in ctx:
                    self._driver.switch_to.context(ctx)
                    logger.info(f"[{self.platform_name}] Switched to {ctx}")
                    return True
            logger.warning(f"[{self.platform_name}] No WEBVIEW context found: {contexts}")
            return False
        except Exception as e:
            logger.warning(f"[{self.platform_name}] Failed to switch to WEBVIEW: {e}")
            return False

    def switch_to_native_context(self) -> None:
        """Switch back to NATIVE_APP context."""
        try:
            self._driver.switch_to.context("NATIVE_APP")
        except Exception:
            pass

    def _adb_shell(self, command: str) -> str:
        """Execute ADB shell command. Returns stdout."""
        adb_cmd = self._find_adb()
        target = f"{self.ADB_HOST}:{self.ADB_PORT}"
        result = subprocess.run(
            [adb_cmd, "-s", target, "shell", command],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout.strip()

    def _check_element_exists(self, by, value, timeout: int = 3) -> bool:
        """Quick check if element exists (short timeout)."""
        return self.find_element(by, value, timeout=timeout) is not None
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `python -c "from scrapers.appium_base import AppiumBaseScraper; print('Import OK')"`
Expected: `Import OK`

- [ ] **Step 3: Commit**

```bash
git add scrapers/appium_base.py
git commit -m "feat: add AppiumBaseScraper for emulator-based scraping"
```

---

### Task 4: Create WechatOfficialScraper

**Files:**
- Create: `scrapers/wechat_official.py`

- [ ] **Step 1: Create the WechatOfficialScraper class**

Create `scrapers/wechat_official.py`:

```python
# scrapers/wechat_official.py
"""WeChat Official Account (公众号) article comment scraper.

Uses Appium + UiAutomator2 to open articles in the emulator's WeChat
built-in browser, extract post content and comments (including replies).
"""
import logging
import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.by import By

import config
from scrapers.appium_base import AppiumBaseScraper

logger = logging.getLogger(__name__)


class WechatOfficialScraper(AppiumBaseScraper):
    """Scrape comments from WeChat Official Account articles (mp.weixin.qq.com)."""

    platform_name = "wechat"

    def scrape(self, url: str) -> dict:
        """Scrape article content and comments."""
        # Delay between URLs to avoid rate limiting
        if self._scrape_count > 0:
            import random
            delay = random.uniform(5, 8)
            logger.info(f"[wechat] Waiting {delay:.1f}s before next URL")
            time.sleep(delay)
        self._scrape_count += 1

        try:
            # Step 1: Open article in WeChat's built-in browser
            self.open_url_in_wechat(url)
            time.sleep(5)  # Wait for page load

            # Step 2: Try to extract post content via WebView
            post_content = self._extract_post_content()

            # Step 3: Scroll to load all comments
            self._scroll_to_load_comments()

            # Step 4: Expand all reply sections
            self._expand_all_replies()

            # Step 5: Extract comments
            comments = self._extract_all_comments()
            logger.info(f"[wechat] Total comments extracted: {len(comments)}")

            # Debug screenshot
            self.take_screenshot("final")

            return {"post_content": post_content, "comments": comments}

        except Exception as e:
            logger.error(f"[wechat] Failed to scrape {url}: {e}")
            self.take_screenshot("error")
            raise

    # ── Content extraction ───────────────────────────────────────

    def _extract_post_content(self) -> str:
        """Extract article body text. Try WebView first, then native fallback."""
        # Try WebView context
        if self.switch_to_webview_context():
            try:
                el = self.find_element(By.CSS_SELECTOR, "#js_content", timeout=10)
                if el:
                    text = el.text
                    self.switch_to_native_context()
                    return text
                el = self.find_element(By.CSS_SELECTOR, ".rich_media_content", timeout=5)
                if el:
                    text = el.text
                    self.switch_to_native_context()
                    return text
            except Exception as e:
                logger.warning(f"[wechat] WebView content extraction failed: {e}")
            finally:
                self.switch_to_native_context()

        # Native fallback: look for text views with article content
        try:
            el = self.find_element(
                AppiumBy.ANDROID_UIAUTOMATOR,
                'new UiSelector().resourceId("com.tencent.mm:id/js_content")',
                timeout=5,
            )
            if el:
                return el.text
        except Exception:
            pass

        logger.warning("[wechat] Could not extract post content")
        return ""

    # ── Scrolling ────────────────────────────────────────────────

    def _scroll_to_load_comments(self, max_scrolls: int = 20) -> None:
        """Scroll down to load all lazy-loaded comments."""
        prev_count = 0
        no_change_count = 0

        for i in range(max_scrolls):
            self.swipe_up()
            time.sleep(1.5)

            # Check if new comments appeared
            current_count = self._count_visible_comments()
            if current_count == prev_count:
                no_change_count += 1
                if no_change_count >= 3:
                    logger.debug(f"[wechat] No new comments after {no_change_count} scrolls")
                    break
            else:
                no_change_count = 0
            prev_count = current_count
            logger.debug(f"[wechat] Scroll {i+1}/{max_scrolls}, comments: {current_count}")

    def _count_visible_comments(self) -> int:
        """Count currently visible comment elements."""
        # Try WebView first
        if self.switch_to_webview_context():
            try:
                count = self._driver.execute_script(
                    "return document.querySelectorAll("
                    "'.comment-item, .discuss-item, [class*=\"comment\"] li"
                    ").length"
                )
                self.switch_to_native_context()
                if count > 0:
                    return count
            except Exception:
                self.switch_to_native_context()

        # Native fallback
        elements = self.find_elements(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().resourceIdMatches(".*comment.*item.*")',
            timeout=3,
        )
        return len(elements)

    # ── Reply expansion ──────────────────────────────────────────

    def _expand_all_replies(self, max_rounds: int = 30) -> None:
        """Click all 'view more reply' buttons to expand collapsed replies."""
        total_clicked = 0

        for _ in range(max_rounds):
            clicked = self._click_one_expand_button()
            if not clicked:
                break
            total_clicked += 1
            time.sleep(2)

        if total_clicked > 0:
            logger.info(f"[wechat] Expanded {total_clicked} reply sections")

    def _click_one_expand_button(self) -> bool:
        """Find and click one expand button. Returns True if clicked."""
        # Try WebView context
        if self.switch_to_webview_context():
            try:
                btn = self._driver.execute_script("""
                    () => {
                        const keywords = ['查看更多评论', '展开更多', '查看全部评论',
                                          '展开回复', '查看更多回复'];
                        const els = document.querySelectorAll('a, span, div, p');
                        for (const el of els) {
                            const text = (el.innerText || '').trim();
                            if (keywords.some(k => text.includes(k)) && !text.includes('收起')) {
                                const rect = el.getBoundingClientRect();
                                if (rect.width > 0 && rect.height > 0) {
                                    return {x: rect.left + rect.width/2, y: rect.top + rect.height/2};
                                }
                            }
                        }
                        return null;
                    }
                """)
                if btn:
                    self._driver.execute_script(
                        "mobile: clickGesture",
                        {"x": btn["x"], "y": btn["y"]},
                    )
                    self.switch_to_native_context()
                    return True
            except Exception:
                pass
            finally:
                self.switch_to_native_context()

        # Native fallback: look for text matching expand keywords
        expand_keywords = ["查看更多评论", "展开更多", "展开回复", "查看更多回复"]
        for keyword in expand_keywords:
            try:
                el = self.find_element(
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    f'new UiSelector().textContains("{keyword}")',
                    timeout=2,
                )
                if el:
                    el.click()
                    return True
            except Exception:
                continue

        return False

    # ── Comment extraction ───────────────────────────────────────

    def _extract_all_comments(self) -> list[tuple[str, str]]:
        """Extract all visible comments. Returns list of (nickname, text)."""
        # Try WebView first (richer DOM access)
        if self.switch_to_webview_context():
            try:
                items = self._driver.execute_script("""
                    () => {
                        const results = [];
                        const selectors = [
                            '.comment-list .comment-item',
                            '.discuss-list .discuss-item',
                            '.discuss-list li',
                            '[class*="comment"] li',
                            '.reply_item',
                        ];
                        for (const sel of selectors) {
                            const items = document.querySelectorAll(sel);
                            if (items.length > 0) {
                                for (const item of items) {
                                    const nick = item.querySelector(
                                        '[class*="nickname"], [class*="name"], [class*="nick"]'
                                    );
                                    const content = item.querySelector(
                                        '[class*="content"], [class*="text"], p'
                                    );
                                    if (nick || content) {
                                        results.push({
                                            nickname: nick ? nick.innerText.trim() : '',
                                            content: content ? content.innerText.trim() : ''
                                        });
                                    }
                                }
                                if (results.length > 0) break;
                            }
                        }

                        // Fallback: scan repeated blocks
                        if (results.length === 0) {
                            const containers = document.querySelectorAll(
                                '[class*="comment"], [class*="discuss"], [class*="reply"]'
                            );
                            for (const container of containers) {
                                for (const child of container.children) {
                                    const lines = child.innerText.split('\\n')
                                        .map(l => l.trim()).filter(l => l);
                                    if (lines.length >= 2) {
                                        results.push({
                                            nickname: lines[0],
                                            content: lines.slice(1).join(' ')
                                        });
                                    }
                                }
                                if (results.length > 0) break;
                            }
                        }

                        return results.slice(0, 200);
                    }
                """)
                self.switch_to_native_context()
                if items:
                    return [(it["nickname"], it["content"]) for it in items if it["content"]]
            except Exception as e:
                logger.warning(f"[wechat] WebView comment extraction failed: {e}")
            finally:
                self.switch_to_native_context()

        # Native fallback: look for comment text views
        comments = []
        try:
            elements = self.find_elements(
                AppiumBy.ANDROID_UIAUTOMATOR,
                'new UiSelector().resourceIdMatches(".*comment.*")',
                timeout=5,
            )
            for el in elements:
                text = el.text
                if text and len(text) > 2:
                    # Heuristic: first line is nickname, rest is content
                    lines = text.split("\n")
                    if len(lines) >= 2:
                        comments.append((lines[0].strip(), " ".join(lines[1:]).strip()))
        except Exception as e:
            logger.warning(f"[wechat] Native comment extraction failed: {e}")

        return comments[:200]
```

- [ ] **Step 2: Verify import**

Run: `python -c "from scrapers.wechat_official import WechatOfficialScraper; print('Import OK')"`
Expected: `Import OK`

- [ ] **Step 3: Commit**

```bash
git add scrapers/wechat_official.py
git commit -m "feat: add WechatOfficialScraper for 公众号文章评论"
```

---

### Task 5: Create WechatChannelsEmulatorScraper

**Files:**
- Create: `scrapers/wechat_channels_emulator.py`

- [ ] **Step 1: Create the WechatChannelsEmulatorScraper class**

Create `scrapers/wechat_channels_emulator.py`:

```python
# scrapers/wechat_channels_emulator.py
"""WeChat Channels (视频号) comment scraper via Android emulator.

Uses Appium + UiAutomator2 to open 视频号 content in the emulator's
WeChat, extract comments, and optionally capture video.
"""
import logging
import subprocess
import time

from appium.webdriver.common.appiumby import AppiumBy

import config
from scrapers.appium_base import AppiumBaseScraper

logger = logging.getLogger(__name__)


class WechatChannelsEmulatorScraper(AppiumBaseScraper):
    """Scrape comments and video from WeChat Channels (视频号)."""

    platform_name = "wechat_channels"

    def scrape(self, url: str) -> dict:
        """Scrape 视频号 comments and optionally download video."""
        # Delay between URLs
        if self._scrape_count > 0:
            import random
            delay = random.uniform(3, 6)
            logger.info(f"[wechat_channels] Waiting {delay:.1f}s before next URL")
            time.sleep(delay)
        self._scrape_count += 1

        try:
            # Step 1: Open 视频号 content
            self._open_channels_url(url)
            time.sleep(5)

            # Step 2: Wait for video to load
            self._wait_for_video_load()

            # Step 3: Open comment panel
            self._open_comment_panel()

            # Step 4: Extract comments
            comments = self._extract_all_comments()
            logger.info(f"[wechat_channels] Total comments: {len(comments)}")

            # Step 5: Video download (optional)
            video_path = self._try_download_video(url)

            # Debug screenshot
            self.take_screenshot("final")

            return {
                "post_content": "",
                "comments": comments,
                "video_url": video_path,
            }

        except Exception as e:
            logger.error(f"[wechat_channels] Failed to scrape {url}: {e}")
            self.take_screenshot("error")
            raise

    # ── Navigation ───────────────────────────────────────────────

    def _open_channels_url(self, url: str) -> None:
        """Open 视频号 URL in WeChat."""
        self.open_url_in_wechat(url)

    def _wait_for_video_load(self, timeout: int = 15) -> None:
        """Wait for video player to appear."""
        for _ in range(timeout):
            el = self.find_element(
                AppiumBy.CLASS_NAME,
                "android.widget.VideoView",
                timeout=2,
            )
            if el:
                logger.debug("[wechat_channels] Video element found")
                return
            # Also check for TextureView (common in modern players)
            el = self.find_element(
                AppiumBy.CLASS_NAME,
                "android.view.TextureView",
                timeout=1,
            )
            if el:
                logger.debug("[wechat_channels] TextureView found")
                return
            time.sleep(1)

        logger.warning("[wechat_channels] Video element not found after timeout")

    def _open_comment_panel(self) -> None:
        """Tap the comment button to open the comment panel."""
        # Try finding comment button by content description
        comment_keywords = ["评论", "comment"]
        for keyword in comment_keywords:
            el = self.find_element(
                AppiumBy.ANDROID_UIAUTOMATOR,
                f'new UiSelector().descriptionContains("{keyword}")',
                timeout=3,
            )
            if el:
                el.click()
                logger.debug("[wechat_channels] Comment button tapped (by description)")
                time.sleep(2)
                return

        # Fallback: try by text
        for keyword in comment_keywords:
            el = self.find_element(
                AppiumBy.ANDROID_UIAUTOMATOR,
                f'new UiSelector().textContains("{keyword}")',
                timeout=3,
            )
            if el:
                el.click()
                logger.debug("[wechat_channels] Comment button tapped (by text)")
                time.sleep(2)
                return

        # Last resort: tap at a common position (bottom-right area)
        size = self._driver.get_window_size()
        comment_x = int(size["width"] * 0.85)
        comment_y = int(size["height"] * 0.75)
        self._driver.tap([(comment_x, comment_y)])
        logger.warning(
            f"[wechat_channels] Comment button not found, tapped at ({comment_x}, {comment_y})"
        )
        time.sleep(2)

    # ── Comment extraction ───────────────────────────────────────

    def _extract_all_comments(self, max_scrolls: int = 20) -> list[tuple[str, str]]:
        """Extract comments from the comment panel. Scrolls to load more."""
        all_comments = []
        seen = set()
        prev_count = 0
        no_change_count = 0

        for i in range(max_scrolls):
            # Extract visible comments
            batch = self._extract_visible_comments()
            for nick, content in batch:
                key = (nick, content[:30])
                if key not in seen:
                    seen.add(key)
                    all_comments.append((nick, content))

            # Check progress
            if len(all_comments) == prev_count:
                no_change_count += 1
                if no_change_count >= 3:
                    logger.debug("[wechat_channels] No new comments after 3 scrolls")
                    break
            else:
                no_change_count = 0
            prev_count = len(all_comments)

            # Scroll down in comment panel
            self.swipe_up()
            time.sleep(1.5)

            logger.debug(
                f"[wechat_channels] Scroll {i+1}/{max_scrolls}, "
                f"comments: {len(all_comments)}"
            )

        # Try expanding replies
        self._expand_replies(all_comments, seen)

        return all_comments

    def _extract_visible_comments(self) -> list[tuple[str, str]]:
        """Extract currently visible comment elements."""
        comments = []

        # Strategy 1: Find RecyclerView items (native comment list)
        items = self.find_elements(
            AppiumBy.ANDROID_UIAUTOMATOR,
            'new UiSelector().resourceIdMatches(".*comment.*item.*")',
            timeout=3,
        )
        if items:
            for item in items:
                text = item.text
                if text and len(text) > 2:
                    lines = text.split("\n")
                    lines = [l.strip() for l in lines if l.strip()]
                    if len(lines) >= 2:
                        # First line is typically nickname + time
                        nickname = self._clean_nickname(lines[0])
                        content = " ".join(lines[1:])
                        if nickname and content:
                            comments.append((nickname, content))

        # Strategy 2: Find all TextViews in the comment area
        if not comments:
            text_views = self.find_elements(
                AppiumBy.CLASS_NAME,
                "android.widget.TextView",
                timeout=3,
            )
            # Group consecutive TextViews as potential comments
            buffer = []
            for tv in text_views:
                text = tv.text
                if text and text.strip():
                    buffer.append(text.strip())
                    if len(buffer) >= 2:
                        nickname = self._clean_nickname(buffer[0])
                        content = " ".join(buffer[1:])
                        if nickname and content:
                            comments.append((nickname, content))
                        buffer = []

        return comments

    def _clean_nickname(self, line: str) -> str:
        """Clean nickname by removing time/location suffixes."""
        import re
        line = re.sub(r'\d+天前|\d+小时前|\d+分钟前|刚刚', '', line)
        line = re.sub(
            r'浙江|广东|北京|上海|江苏|山东|四川|河南|湖北|湖南|福建|安徽|'
            r'辽宁|重庆|天津|河北|山西|吉林|黑龙江|江西|广西|海南|贵州|云南|'
            r'西藏|陕西|甘肃|青海|宁夏|新疆|内蒙古', '', line,
        )
        line = line.replace('作者', '').replace('V', '').strip()
        return line.strip()

    def _expand_replies(
        self, all_comments: list[tuple[str, str]], seen: set
    ) -> None:
        """Try to expand reply sections and extract reply comments."""
        for _ in range(10):
            el = self.find_element(
                AppiumBy.ANDROID_UIAUTOMATOR,
                'new UiSelector().textContains("展开回复")',
                timeout=2,
            )
            if not el:
                el = self.find_element(
                    AppiumBy.ANDROID_UIAUTOMATOR,
                    'new UiSelector().textContains("条回复")',
                    timeout=2,
                )
            if not el:
                break

            try:
                el.click()
                time.sleep(2)

                # Extract replies
                batch = self._extract_visible_comments()
                for nick, content in batch:
                    key = (nick, content[:30])
                    if key not in seen:
                        seen.add(key)
                        all_comments.append((nick, content))
            except Exception as e:
                logger.warning(f"[wechat_channels] Failed to expand reply: {e}")
                break

    # ── Video download ───────────────────────────────────────────

    def _try_download_video(self, url: str) -> str | None:
        """Attempt to download video. Returns local path or None."""
        # Priority 1: ADB screenrecord
        video_path = self._screenrecord_video()
        if video_path:
            return video_path

        # Priority 2: Skip
        logger.warning("[wechat_channels] Video download skipped")
        return None

    def _screenrecord_video(self) -> str | None:
        """Record video via ADB screenrecord. Returns local path or None."""
        max_seconds = config.EMULATOR_SCREENRECORD_MAX_SECONDS
        remote_path = "/sdcard/ch_video.mp4"

        logger.info(f"[wechat_channels] Starting screenrecord ({max_seconds}s max)")

        # Start screenrecord in background
        try:
            self._adb_shell(
                f"screenrecord --time-limit {max_seconds} {remote_path}"
            )
        except Exception as e:
            logger.warning(f"[wechat_channels] screenrecord failed: {e}")
            return None

        # Pull file to local
        local_path = config.OUTPUT_DIR / "video_temp" / f"{self.platform_name}_recorded.mp4"
        local_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            adb_cmd = self._find_adb()
            target = f"{self.ADB_HOST}:{self.ADB_PORT}"
            subprocess.run(
                [adb_cmd, "-s", target, "pull", remote_path, str(local_path)],
                capture_output=True, timeout=60,
            )
            if local_path.exists() and local_path.stat().st_size > 1000:
                logger.info(f"[wechat_channels] Video saved: {local_path}")
                return str(local_path)
        except Exception as e:
            logger.warning(f"[wechat_channels] Failed to pull video: {e}")

        return None
```

- [ ] **Step 2: Verify import**

Run: `python -c "from scrapers.wechat_channels_emulator import WechatChannelsEmulatorScraper; print('Import OK')"`
Expected: `Import OK`

- [ ] **Step 3: Commit**

```bash
git add scrapers/wechat_channels_emulator.py
git commit -m "feat: add WechatChannelsEmulatorScraper for 视频号评论"
```

---

### Task 6: Update scrapers/__init__.py

**Files:**
- Modify: `scrapers/__init__.py`

The `match_platform()` function already reads from `config.PLATFORM_PATTERNS`, which we updated in Task 1. No code changes needed in `__init__.py` — it will automatically pick up the new patterns.

- [ ] **Step 1: Verify match_platform works for new URLs**

Run: `python -c "from scrapers import match_platform; print(match_platform('https://channels.weixin.qq.com/web/pages/feed/abc'))"`
Expected: `wechat_channels`

Run: `python -c "from scrapers import match_platform; print(match_platform('https://mp.weixin.qq.com/s?__biz=abc'))"`
Expected: `wechat`

- [ ] **Step 2: Run all tests**

Run: `python -m pytest tests/test_config.py -v`
Expected: All tests pass (including the new `test_wechat_channels_url`).

- [ ] **Step 3: Commit (if any changes were needed)**

No changes expected. Skip if `match_platform` works correctly.

---

### Task 7: Integrate into main.py

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add --emulator CLI argument**

In `main.py`, after the `--no-video` argument (line 868), add:

```python
    parser.add_argument("--emulator", action="store_true", help="启用模拟器模式（微信公众号/视频号需要）")
```

- [ ] **Step 2: Update scraper_classes in both run_all() and scrape_all()**

Both `run_all()` (line 418) and `scrape_all()` (line 476) have their own `scraper_classes` dicts. Update both to use the new scrapers.

In `run_all()`, replace the imports and scraper_classes dict:

```python
    from scrapers.douyin import DouyinScraper
    from scrapers.xiaohongshu import XiaohongshuScraper
    from scrapers.wechat_official import WechatOfficialScraper
    from scrapers.wechat_channels_emulator import WechatChannelsEmulatorScraper
    from scrapers.weibo import WeiboScraper
    from scrapers.toutiao import ToutiaoScraper

    scraper_classes = {
        "douyin": DouyinScraper,
        "xiaohongshu": XiaohongshuScraper,
        "wechat": WechatOfficialScraper,
        "wechat_channels": WechatChannelsEmulatorScraper,
        "weibo": WeiboScraper,
        "toutiao": ToutiaoScraper,
    }
```

In `scrape_all()`, replace the imports and scraper_classes dict (around line 471):

```python
    from scrapers.douyin import DouyinScraper
    from scrapers.xiaohongshu import XiaohongshuScraper
    from scrapers.wechat_official import WechatOfficialScraper
    from scrapers.wechat_channels_emulator import WechatChannelsEmulatorScraper
    from scrapers.toutiao import ToutiaoScraper
    from scrapers.weibo import WeiboScraper

    scraper_classes = {
        "douyin": DouyinScraper,
        "xiaohongshu": XiaohongshuScraper,
        "wechat": WechatOfficialScraper,
        "wechat_channels": WechatChannelsEmulatorScraper,
        "toutiao": ToutiaoScraper,
        "weibo": WeiboScraper,
    }
```

- [ ] **Step 3: Add emulator parameter to run_all() and scrape_all() signatures**

Change both function signatures to accept `emulator: bool = False`:

```python
async def run_all(input_file, platforms_filter=None, resume=False, batch_size=0,
                  login_timeout=300, classify=True, process_video=True, emulator=False):

async def scrape_all(input_file, platforms_filter=None, resume=False, batch_size=0,
                     classify=True, process_video=True, emulator=False):
```

- [ ] **Step 4: Add emulator row filtering in both functions**

In both `run_all()` and `scrape_all()`, after the `rows` list is built and filtered by `platforms_filter`, add:

```python
    # Skip emulator platforms if --emulator not set
    if not emulator:
        emulator_platforms = {"wechat", "wechat_channels"}
        before = len(rows)
        rows = [r for r in rows if r["platform_key"] not in emulator_platforms]
        skipped = before - len(rows)
        if skipped > 0:
            print(f"[提示] 跳过 {skipped} 条微信链接（需要 --emulator 参数）")
```

Also update the `run_all()` call to `scrape_all()` to pass `emulator` through:

```python
    return await scrape_all(input_file, platforms_filter, resume=resume,
                            batch_size=batch_size, classify=classify,
                            process_video=process_video, emulator=emulator)
```

- [ ] **Step 5: Add emulator dependency check in main()**

In the `main()` function, after the `install_dependencies()` block (around line 878), add:

```python
    # Check emulator dependencies if needed
    if args.emulator:
        try:
            from appium import webdriver as _aw
        except ImportError:
            print("[提示] 模拟器模式需要 Appium-Python-Client")
            print("正在安装...")
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "Appium-Python-Client>=3.0.0"],
                stdout=subprocess.PIPE,
            )
```

- [ ] **Step 6: Update the CLI dispatch in main()**

Update the `--run` branch to pass `emulator`:

```python
    if args.run:
        platforms_filter = None
        if args.platforms:
            platforms_filter = [p.strip() for p in args.platforms.split(",") if p.strip()]
        asyncio.run(run_all(input_file, platforms_filter, resume=args.resume,
                            batch_size=args.batch_size, login_timeout=args.login_timeout,
                            classify=not args.no_classify, process_video=not args.no_video,
                            emulator=args.emulator))
```

And the default (no --run) branch:

```python
    else:
        platforms_filter = None
        if args.platforms:
            platforms_filter = [p.strip() for p in args.platforms.split(",") if p.strip()]
        asyncio.run(scrape_all(input_file, platforms_filter, resume=args.resume,
                               batch_size=args.batch_size, classify=not args.no_classify,
                               process_video=not args.no_video, emulator=args.emulator))
```

- [ ] **Step 7: Update _PLATFORM_DISPLAY**

Add the missing platform display names:

```python
_PLATFORM_DISPLAY = {
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "weibo": "微博",
    "toutiao": "今日头条",
    "wechat": "微信公众号",
    "wechat_channels": "微信视频号",
}
```

- [ ] **Step 8: Update epilog text**

Update the argparse epilog to mention the new platforms:

```python
        epilog="""
使用示例:
  python main.py --setup                     首次使用，自动安装环境
  python main.py --run --input input.xlsx    一键执行：自动登录+抓取（推荐）
  python main.py --run --input input.xlsx --emulator  包含微信公众号/视频号
  python main.py --run --input input.xlsx --resume --batch-size 20  带断点续抓
  python main.py --run --input input.xlsx --no-classify   只抓取不分类
  python main.py --run --input input.xlsx --platforms wechat,wechat_channels --emulator  只抓微信
  python main.py --login                     登录所有平台（打开浏览器，等待登录后保存）

微信公众号/视频号需要:
  1. 启动雷电模拟器 + 微信并登录
  2. 启动 Appium Server: appium
  3. 运行: python main.py --run --emulator
        """,
```

- [ ] **Step 9: Run existing tests to verify no regressions**

Run: `python -m pytest tests/ -v`
Expected: All existing tests pass.

- [ ] **Step 10: Commit**

```bash
git add main.py
git commit -m "feat: integrate WeChat emulator scrapers into main pipeline with --emulator flag"
```

---

### Task 8: Write unit tests for emulator scrapers

**Files:**
- Create: `tests/test_appium_base.py`
- Create: `tests/test_wechat_official.py`
- Create: `tests/test_wechat_channels_emulator.py`

- [ ] **Step 1: Create test_appium_base.py**

Create `tests/test_appium_base.py`:

```python
# tests/test_appium_base.py
"""Tests for AppiumBaseScraper (mocked Appium)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestAppiumBaseScraper:
    """Test AppiumBaseScraper with mocked ADB and Appium."""

    def test_import(self):
        from scrapers.appium_base import AppiumBaseScraper
        assert AppiumBaseScraper.platform_name == "base_emulator"

    def test_find_adb_system(self):
        """Test _find_adb finds system adb when no LDPlayer."""
        from scrapers.appium_base import AppiumBaseScraper
        scraper = AppiumBaseScraper()
        with patch("subprocess.run") as mock_run:
            # Mock: no LDPlayer paths exist, but system adb works
            mock_run.return_value = MagicMock(returncode=0)
            with patch("pathlib.Path.exists", return_value=False):
                result = scraper._find_adb()
                assert result == "adb"

    def test_find_adb_not_found(self):
        """Test _find_adb raises when no ADB available."""
        from scrapers.appium_base import AppiumBaseScraper
        scraper = AppiumBaseScraper()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with patch("pathlib.Path.exists", return_value=False):
                with pytest.raises(RuntimeError, match="找不到 ADB"):
                    scraper._find_adb()

    def test_random_delay_range(self):
        """Test random_delay sleeps within configured range."""
        import config
        from scrapers.appium_base import AppiumBaseScraper
        scraper = AppiumBaseScraper()
        with patch("time.sleep") as mock_sleep:
            scraper.random_delay()
            args = mock_sleep.call_args[0][0]
            assert config.EMULATOR_MIN_DELAY <= args <= config.EMULATOR_MAX_DELAY

    def test_switch_to_native_context(self):
        """Test switch_to_native_context calls driver."""
        from scrapers.appium_base import AppiumBaseScraper
        scraper = AppiumBaseScraper()
        scraper._driver = MagicMock()
        scraper.switch_to_native_context()
        scraper._driver.switch_to.context.assert_called_once_with("NATIVE_APP")

    def test_switch_to_native_context_no_driver(self):
        """Test switch_to_native_context handles missing driver."""
        from scrapers.appium_base import AppiumBaseScraper
        scraper = AppiumBaseScraper()
        scraper._driver = None
        # Should not raise
        scraper.switch_to_native_context()

    def test_stop_with_no_session(self):
        """Test stop() handles no active session."""
        from scrapers.appium_base import AppiumBaseScraper
        scraper = AppiumBaseScraper()
        scraper._driver = None
        scraper.stop()  # Should not raise

    def test_config_values(self):
        """Test that emulator config values are set."""
        import config
        assert config.EMULATOR_ADB_HOST == "127.0.0.1"
        assert config.EMULATOR_ADB_PORT == 5555
        assert config.APPIUM_SERVER_URL == "http://127.0.0.1:4723"
        assert config.WECHAT_PACKAGE == "com.tencent.mm"
        assert config.EMULATOR_MIN_DELAY >= 1.0
        assert config.EMULATOR_MAX_DELAY > config.EMULATOR_MIN_DELAY
```

- [ ] **Step 2: Create test_wechat_official.py**

Create `tests/test_wechat_official.py`:

```python
# tests/test_wechat_official.py
"""Tests for WechatOfficialScraper (mocked Appium)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestWechatOfficialScraper:
    """Test WechatOfficialScraper with mocked Appium driver."""

    def test_import(self):
        from scrapers.wechat_official import WechatOfficialScraper
        assert WechatOfficialScraper.platform_name == "wechat"

    def test_platform_name(self):
        from scrapers.wechat_official import WechatOfficialScraper
        scraper = WechatOfficialScraper()
        assert scraper.platform_name == "wechat"

    def test_scrape_not_implemented_on_base(self):
        """Verify scrape is implemented (not raising NotImplementedError)."""
        from scrapers.wechat_official import WechatOfficialScraper
        scraper = WechatOfficialScraper()
        # scrape should exist and be callable
        assert callable(scraper.scrape)
```

- [ ] **Step 3: Create test_wechat_channels_emulator.py**

Create `tests/test_wechat_channels_emulator.py`:

```python
# tests/test_wechat_channels_emulator.py
"""Tests for WechatChannelsEmulatorScraper (mocked Appium)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestWechatChannelsEmulatorScraper:
    """Test WechatChannelsEmulatorScraper with mocked Appium driver."""

    def test_import(self):
        from scrapers.wechat_channels_emulator import WechatChannelsEmulatorScraper
        assert WechatChannelsEmulatorScraper.platform_name == "wechat_channels"

    def test_platform_name(self):
        from scrapers.wechat_channels_emulator import WechatChannelsEmulatorScraper
        scraper = WechatChannelsEmulatorScraper()
        assert scraper.platform_name == "wechat_channels"

    def test_clean_nickname(self):
        """Test nickname cleaning removes time/location suffixes."""
        from scrapers.wechat_channels_emulator import WechatChannelsEmulatorScraper
        scraper = WechatChannelsEmulatorScraper()

        assert scraper._clean_nickname("张三3天前") == "张三"
        assert scraper._clean_nickname("李四作者") == "李四"
        assert scraper._clean_nickname("王五北京") == "王五"
        assert scraper._clean_nickname("赵六V") == "赵六"
        assert scraper._clean_nickname("孙七刚刚") == "孙七"
        assert scraper._clean_nickname("周八") == "周八"

    def test_scrape_returns_dict(self):
        """Verify scrape method exists and is callable."""
        from scrapers.wechat_channels_emulator import WechatChannelsEmulatorScraper
        scraper = WechatChannelsEmulatorScraper()
        assert callable(scraper.scrape)
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests pass (including new emulator tests and existing tests).

- [ ] **Step 5: Commit**

```bash
git add tests/test_appium_base.py tests/test_wechat_official.py tests/test_wechat_channels_emulator.py
git commit -m "test: add unit tests for emulator scrapers"
```

---

### Task 9: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the platform lists and add emulator section**

In `CLAUDE.md`, update the supported platforms line and add a new section after the "Login" section:

```markdown
### Emulator Setup (WeChat)
```bash
# 1. Start LDPlayer emulator with WeChat logged in
# 2. Start Appium Server
appium
# 3. Run with emulator flag
python main.py --run --platforms wechat,wechat_channels --emulator
```
```

Update the "Suspended platforms" line to note they are now supported via emulator:

```markdown
Emulator-dependent platforms: WeChat Official Accounts (微信公众号), WeChat Channels (微信视频号) — require LDPlayer + Appium.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with WeChat emulator scraper info"
```

---

### Task 10: Final integration verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 2: Verify all imports work together**

Run: `python -c "
from scrapers.appium_base import AppiumBaseScraper
from scrapers.wechat_official import WechatOfficialScraper
from scrapers.wechat_channels_emulator import WechatChannelsEmulatorScraper
from scrapers import match_platform
print('All imports OK')
print(f'wechat URL: {match_platform(\"https://mp.weixin.qq.com/s?__biz=abc\")}')
print(f'channels URL: {match_platform(\"https://channels.weixin.qq.com/web/pages/feed/abc\")}')
"`
Expected:
```
All imports OK
wechat URL: wechat
channels URL: wechat_channels
```

- [ ] **Step 3: Verify main.py --help shows --emulator**

Run: `python main.py --help`
Expected: `--emulator` appears in the output.

- [ ] **Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "chore: final integration fixes for WeChat emulator scraper"
```
