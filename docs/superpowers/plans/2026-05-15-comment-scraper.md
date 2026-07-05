# 多平台评论抓取工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python tool that reads social media URLs from an Excel file, scrapes comments (including replies) from 5 Chinese platforms using Playwright, and outputs each post's comments as a separate Excel file.

**Architecture:** Modular design with a base scraper class and platform-specific implementations. URL domain matching dispatches to the correct scraper. Playwright handles browser automation with cookie persistence for login reuse.

**Tech Stack:** Python, Playwright, openpyxl

**Spec:** `docs/superpowers/specs/2026-05-15-comment-scraper-design.md`

---

## File Map

| File | Responsibility |
|------|---------------|
| `requirements.txt` | Python dependencies |
| `config.py` | Constants: delays, timeouts, paths, platform URL patterns |
| `scrapers/__init__.py` | Package init, platform registry, URL→scraper mapping |
| `scrapers/base.py` | Base scraper: browser lifecycle, cookie I/O, scroll helper |
| `scrapers/douyin.py` | Douyin comment scraping |
| `scrapers/xiaohongshu.py` | Xiaohongshu comment scraping |
| `scrapers/wechat.py` | WeChat (公众号 + 视频号) comment scraping |
| `scrapers/toutiao.py` | Toutiao comment scraping |
| `scrapers/weibo.py` | Weibo comment scraping |
| `main.py` | Orchestrator: read Excel, dispatch scrapers, write output |
| `tests/test_config.py` | Test config and URL matching |
| `tests/test_excel_io.py` | Test Excel reading and output writing |
| `tests/test_filename.py` | Test filename sanitization |

---

### Task 1: Project Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `config.py`
- Create: `scrapers/__init__.py`
- Create: `scrapers/base.py` (empty placeholder)
- Create: `tests/__init__.py`

- [ ] **Step 1: Create requirements.txt**

```
playwright>=1.40.0
openpyxl>=3.1.0
pytest>=7.0.0
```

- [ ] **Step 2: Create config.py**

```python
import re
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent
INPUT_FILE = BASE_DIR / "example.xlsx"
OUTPUT_DIR = BASE_DIR / "output"
COOKIE_DIR = BASE_DIR / "cookies"
LOG_DIR = BASE_DIR / "logs"

# Scraping
MIN_DELAY = 1.0
MAX_DELAY = 3.0
MAX_SCROLL = 50
PAGE_TIMEOUT = 30000  # ms

# Excel input columns (0-indexed)
COL_SERIAL = 0       # 序号
COL_TITLE = 4        # 标题/微博内容
COL_SOURCE = 5       # 来源网站
COL_AUTHOR = 6       # 原文作者
COL_DATE = 7         # 日期
COL_PLATFORM = 8     # 媒体平台
COL_URL = 9          # 原文/评论链接

# Platform URL patterns → platform key
PLATFORM_PATTERNS: dict[str, list[str]] = {
    "douyin": ["iesdouyin.com", "douyin.com"],
    "xiaohongshu": ["xiaohongshu.com"],
    "wechat": ["mp.weixin.qq.com", "channels.weixin.qq.com"],
    "toutiao": ["toutiao.com"],
    "weibo": ["weibo.com"],
}

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')


def sanitize_filename(name: str, max_len: int = 20) -> str:
    """Remove illegal filesystem chars and truncate."""
    cleaned = ILLEGAL_CHARS.sub("_", name).strip()
    return cleaned[:max_len] if cleaned else "untitled"
```

- [ ] **Step 3: Create scrapers/__init__.py**

```python
from config import PLATFORM_PATTERNS


def match_platform(url: str) -> str | None:
    """Return platform key for a URL, or None if unsupported."""
    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if pattern in url:
                return platform
    return None
```

- [ ] **Step 4: Create empty scrapers/base.py and tests/__init__.py**

```python
# scrapers/base.py
# Base scraper - implemented in Task 3
```

```python
# tests/__init__.py
```

- [ ] **Step 5: Install dependencies**

Run: `pip install -r requirements.txt && playwright install chromium`
Expected: Installation succeeds

- [ ] **Step 6: Commit**

```bash
git init
git add requirements.txt config.py scrapers/__init__.py scrapers/base.py tests/__init__.py
git commit -m "feat: project scaffolding with config and platform matching"
```

---

### Task 2: TDD - URL Matching, Filename Sanitization, Excel I/O

**Files:**
- Create: `tests/test_config.py`
- Create: `tests/test_excel_io.py`
- Create: `tests/test_filename.py`

- [ ] **Step 1: Write failing tests for URL matching**

```python
# tests/test_config.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scrapers import match_platform


def test_douyin_url():
    assert match_platform("https://www.iesdouyin.com/share/video/123") == "douyin"
    assert match_platform("https://www.douyin.com/video/123") == "douyin"


def test_xiaohongshu_url():
    assert match_platform("https://www.xiaohongshu.com/explore/abc123") == "xiaohongshu"


def test_wechat_url():
    assert match_platform("https://mp.weixin.qq.com/s?__biz=abc") == "wechat"
    assert match_platform("https://channels.weixin.qq.com/abc") == "wechat"


def test_toutiao_url():
    assert match_platform("https://www.toutiao.com/i123456/") == "toutiao"


def test_weibo_url():
    assert match_platform("http://weibo.com/123456/abc") == "weibo"


def test_unsupported_url():
    assert match_platform("https://www.unknown-site.com/post/1") is None


def test_empty_url():
    assert match_platform("") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL (match_platform not yet importable or logic wrong)

- [ ] **Step 3: Fix scrapers/__init__.py to make tests pass**

The `scrapers/__init__.py` created in Task 1 should already work. If import fails due to `config` path, add `sys.path` fix:

```python
# scrapers/__init__.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import PLATFORM_PATTERNS


def match_platform(url: str) -> str | None:
    """Return platform key for a URL, or None if unsupported."""
    if not url:
        return None
    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if pattern in url:
                return platform
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: 7 passed

- [ ] **Step 5: Write failing tests for filename sanitization**

```python
# tests/test_filename.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import sanitize_filename


def test_normal_title():
    assert sanitize_filename("全季大观酒店评测") == "全季大观酒店评测"


def test_illegal_chars_replaced():
    result = sanitize_filename('首店"扑街"的全季：大观')
    assert "/" not in result
    assert ":" not in result
    assert '"' not in result


def test_truncation():
    result = sanitize_filename("这是一个超过二十个字符的很长很长的标题名称测试", max_len=20)
    assert len(result) <= 20


def test_empty_string():
    assert sanitize_filename("") == "untitled"


def test_whitespace_stripped():
    assert sanitize_filename("  hello  ") == "hello"
```

- [ ] **Step 6: Run tests, implement if needed, verify pass**

Run: `python -m pytest tests/test_filename.py -v`
Expected: 5 passed (config.py already has the implementation)

- [ ] **Step 7: Write failing tests for Excel reading**

```python
# tests/test_excel_io.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import openpyxl
import tempfile
import os


def _create_test_excel(rows: list[dict]) -> str:
    """Create a temp xlsx file with test data."""
    wb = openpyxl.Workbook()
    ws = wb.active
    headers = ["序号", "数据划分", "标签", "信息属性", "标题/微博内容",
               "来源网站", "原文作者", "日期", "媒体平台", "原文/评论链接",
               "阅读/播放", "点赞数", "评论数", "转发数", "收藏"]
    ws.append(headers)
    for row in rows:
        ws.append([
            row.get("serial", 1),
            row.get("division", "有效数据"),
            row.get("tag", ""),
            row.get("attr", ""),
            row.get("title", "测试标题"),
            row.get("source", "抖音"),
            row.get("author", "测试作者"),
            row.get("date", "2026-01-01"),
            row.get("platform", "视频"),
            row.get("url", "https://www.iesdouyin.com/share/video/123"),
            row.get("reads", 0),
            row.get("likes", 0),
            row.get("comments", 0),
            row.get("shares", 0),
            row.get("favorites", 0),
        ])
    fd, path = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    wb.save(path)
    return path


def test_read_excel_extracts_urls():
    from main import read_input_excel
    path = _create_test_excel([
        {"serial": 1, "title": "抖音测试", "url": "https://www.iesdouyin.com/share/video/111"},
        {"serial": 2, "title": "小红书测试", "url": "https://www.xiaohongshu.com/explore/222"},
    ])
    try:
        rows = read_input_excel(path)
        assert len(rows) == 2
        assert rows[0]["url"] == "https://www.iesdouyin.com/share/video/111"
        assert rows[0]["title"] == "抖音测试"
        assert rows[0]["serial"] == 1
        assert rows[1]["platform_key"] is not None
    finally:
        os.unlink(path)


def test_read_excel_skips_invalid_urls():
    from main import read_input_excel
    path = _create_test_excel([
        {"serial": 1, "title": "有效", "url": "https://www.iesdouyin.com/share/video/111"},
        {"serial": 2, "title": "无效", "url": ""},
        {"serial": 3, "title": "不支持", "url": "https://www.unknown.com/post/1"},
    ])
    try:
        rows = read_input_excel(path)
        assert len(rows) == 1
    finally:
        os.unlink(path)


def test_write_comments_excel():
    from main import write_comments_excel
    import tempfile, os
    comments = [("用户A", "评论1"), ("用户B", "评论2")]
    outdir = tempfile.mkdtemp()
    try:
        filepath = write_comments_excel(outdir, comments)
        assert os.path.exists(filepath)
        wb = openpyxl.load_workbook(filepath)
        ws = wb.active
        assert ws.cell(1, 1).value == "序号"
        assert ws.cell(1, 2).value == "ID名称"
        assert ws.cell(1, 3).value == "评论内容"
        assert ws.cell(2, 1).value == 1
        assert ws.cell(2, 2).value == "用户A"
        assert ws.cell(2, 3).value == "评论1"
        assert ws.cell(3, 1).value == 2
    finally:
        import shutil
        shutil.rmtree(outdir)
```

- [ ] **Step 8: Run tests to verify they fail**

Run: `python -m pytest tests/test_excel_io.py -v`
Expected: FAIL (main.py doesn't exist yet)

- [ ] **Step 9: Commit test files**

```bash
git add tests/
git commit -m "test: add tests for URL matching, filename, and Excel I/O"
```

---

### Task 3: Base Scraper Class

**Files:**
- Modify: `scrapers/base.py`

- [ ] **Step 1: Implement BaseScraper**

```python
# scrapers/base.py
import asyncio
import json
import logging
import random
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

import config

logger = logging.getLogger(__name__)


class BaseScraper:
    platform_name: str = "base"

    def __init__(self):
        self.cookie_path = config.COOKIE_DIR / f"{self.platform_name}.json"
        self._playwright = None
        self._browser = None

    async def start(self):
        """Launch browser."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=False)

    async def stop(self):
        """Close browser."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def new_context(self) -> BrowserContext:
        """Create a new browser context, loading cookies if available."""
        context = await self._browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        await self._load_cookies(context)
        return context

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

    async def login_interactive(self):
        """Open browser for manual login, save cookies when done."""
        await self.start()
        try:
            context = await self._browser.new_context(
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()
            login_url = self._get_login_url()
            await page.goto(login_url)
            print(f"\n[{self.platform_name}] 请在浏览器中手动登录。")
            print(f"登录完成后，回到此窗口按 Enter 保存 Cookie...")
            input()
            await self.save_cookies(context)
            print(f"[{self.platform_name}] Cookie 已保存。")
        finally:
            await self.stop()

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

    async def scrape(self, url: str) -> list[tuple[str, str]]:
        """Scrape comments from URL. Returns [(nickname, comment_text), ...]"""
        raise NotImplementedError
```

- [ ] **Step 2: Commit**

```bash
git add scrapers/base.py
git commit -m "feat: add base scraper with Playwright browser and cookie management"
```

---

### Task 4: Douyin Scraper

**Files:**
- Create: `scrapers/douyin.py`

- [ ] **Step 1: Implement Douyin scraper**

```python
# scrapers/douyin.py
import logging
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class DouyinScraper(BaseScraper):
    platform_name = "douyin"

    def _get_login_url(self) -> str:
        return "https://www.douyin.com"

    async def scrape(self, url: str) -> list[tuple[str, str]]:
        comments = []
        context = await self.new_context()
        page = await context.new_page()
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await self.random_delay()

            # Wait for comment section to load
            # Douyin comments are in a sidebar or below the video
            try:
                await page.wait_for_selector('[class*="comment"]', timeout=10000)
            except Exception:
                logger.warning(f"[douyin] Comment section not found for {url}")
                return comments

            # Scroll to load more comments
            comment_container = page.locator('[class*="comment-list"]').first
            if await comment_container.count() > 0:
                for _ in range(50):
                    await comment_container.evaluate("el => el.scrollTop = el.scrollHeight")
                    await self.random_delay()
                    # Check if "load more" button exists
                    load_more = page.locator('text="加载更多"').first
                    if await load_more.count() > 0:
                        await load_more.click()
                        await self.random_delay()
                    else:
                        break

            # Extract comments
            comment_items = page.locator('[class*="comment-item"], [class*="CommentItem"]')
            count = await comment_items.count()
            for i in range(count):
                item = comment_items.nth(i)
                try:
                    nickname_el = item.locator('[class*="nickname"], [class*="name"]').first
                    content_el = item.locator('[class*="content"], [class*="text"]').first
                    nickname = await nickname_el.inner_text()
                    content = await content_el.inner_text()
                    if nickname and content:
                        comments.append((nickname.strip(), content.strip()))
                except Exception:
                    continue

            # Also extract replies (nested comments)
            reply_items = page.locator('[class*="reply-item"], [class*="ReplyItem"]')
            reply_count = await reply_items.count()
            for i in range(reply_count):
                item = reply_items.nth(i)
                try:
                    nickname_el = item.locator('[class*="nickname"], [class*="name"]').first
                    content_el = item.locator('[class*="content"], [class*="text"]').first
                    nickname = await nickname_el.inner_text()
                    content = await content_el.inner_text()
                    if nickname and content:
                        comments.append((nickname.strip(), content.strip()))
                except Exception:
                    continue

        except Exception as e:
            logger.error(f"[douyin] Failed to scrape {url}: {e}")
            raise
        finally:
            await page.close()
            await context.close()

        return comments
```

- [ ] **Step 2: Commit**

```bash
git add scrapers/douyin.py
git commit -m "feat: add Douyin comment scraper"
```

---

### Task 5: Xiaohongshu Scraper

**Files:**
- Create: `scrapers/xiaohongshu.py`

- [ ] **Step 1: Implement Xiaohongshu scraper**

```python
# scrapers/xiaohongshu.py
import logging
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class XiaohongshuScraper(BaseScraper):
    platform_name = "xiaohongshu"

    def _get_login_url(self) -> str:
        return "https://www.xiaohongshu.com"

    async def scrape(self, url: str) -> list[tuple[str, str]]:
        comments = []
        context = await self.new_context()
        page = await context.new_page()
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await self.random_delay()

            # Wait for the note page to load
            try:
                await page.wait_for_selector('[class*="note-detail"], [class*="note-container"]', timeout=10000)
            except Exception:
                logger.warning(f"[xiaohongshu] Note page not found for {url}")
                return comments

            # Click to expand comments if needed
            expand_btn = page.locator('text="展开更多评论", text="查看更多评论"').first
            if await expand_btn.count() > 0:
                await expand_btn.click()
                await self.random_delay()

            # Scroll comment area to load more
            for _ in range(50):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await self.random_delay()
                load_more = page.locator('text="加载更多", text="查看更多"').first
                if await load_more.count() > 0:
                    await load_more.click()
                    await self.random_delay()
                else:
                    break

            # Extract comments
            comment_items = page.locator('[class*="comment-item"], [class*="comment-inner"]')
            count = await comment_items.count()
            for i in range(count):
                item = comment_items.nth(i)
                try:
                    nickname_el = item.locator('[class*="name"], [class*="nickname"]').first
                    content_el = item.locator('[class*="content"]').first
                    nickname = await nickname_el.inner_text()
                    content = await content_el.inner_text()
                    if nickname and content:
                        comments.append((nickname.strip(), content.strip()))
                except Exception:
                    continue

        except Exception as e:
            logger.error(f"[xiaohongshu] Failed to scrape {url}: {e}")
            raise
        finally:
            await page.close()
            await context.close()

        return comments
```

- [ ] **Step 2: Commit**

```bash
git add scrapers/xiaohongshu.py
git commit -m "feat: add Xiaohongshu comment scraper"
```

---

### Task 6: WeChat Scraper

**Files:**
- Create: `scrapers/wechat.py`

- [ ] **Step 1: Implement WeChat scraper (公众号 + 视频号)**

```python
# scrapers/wechat.py
import logging
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class WechatScraper(BaseScraper):
    platform_name = "wechat"

    def _get_login_url(self) -> str:
        return "https://mp.weixin.qq.com"

    async def scrape(self, url: str) -> list[tuple[str, str]]:
        comments = []
        context = await self.new_context()
        page = await context.new_page()
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await self.random_delay()

            # WeChat articles: comments are at the bottom
            # Try to click "展开更多" for comments
            for _ in range(5):
                expand_btn = page.locator('text="展开更多", text="查看更多评论"').first
                if await expand_btn.count() > 0:
                    await expand_btn.click()
                    await self.random_delay()
                else:
                    break

            # Extract comments from WeChat article
            comment_items = page.locator('[class*="comment-item"], [class*="discuss-item"], .rich_media_area_primary .comment_item')
            count = await comment_items.count()

            # Also try the newer WeChat comment structure
            if count == 0:
                comment_items = page.locator('[id*="comment"] [class*="item"], [class*="js_comment_item"]')
                count = await comment_items.count()

            for i in range(count):
                item = comment_items.nth(i)
                try:
                    nickname_el = item.locator('[class*="nickname"], [class*="name"], [class*="user"]').first
                    content_el = item.locator('[class*="content"], [class*="text"], [class*="desc"]').first
                    nickname = await nickname_el.inner_text()
                    content = await content_el.inner_text()
                    if nickname and content:
                        comments.append((nickname.strip(), content.strip()))
                except Exception:
                    continue

        except Exception as e:
            logger.error(f"[wechat] Failed to scrape {url}: {e}")
            raise
        finally:
            await page.close()
            await context.close()

        return comments
```

- [ ] **Step 2: Commit**

```bash
git add scrapers/wechat.py
git commit -m "feat: add WeChat comment scraper"
```

---

### Task 7: Toutiao Scraper

**Files:**
- Create: `scrapers/toutiao.py`

- [ ] **Step 1: Implement Toutiao scraper**

```python
# scrapers/toutiao.py
import logging
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class ToutiaoScraper(BaseScraper):
    platform_name = "toutiao"

    def _get_login_url(self) -> str:
        return "https://www.toutiao.com"

    async def scrape(self, url: str) -> list[tuple[str, str]]:
        comments = []
        context = await self.new_context()
        page = await context.new_page()
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await self.random_delay()

            # Wait for comment section
            try:
                await page.wait_for_selector('[class*="comment"], [class*="Comment"]', timeout=10000)
            except Exception:
                logger.warning(f"[toutiao] Comment section not found for {url}")
                return comments

            # Scroll to load more comments
            for _ in range(50):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await self.random_delay()
                load_more = page.locator('text="展开更多", text="加载更多", text="查看更多"').first
                if await load_more.count() > 0:
                    await load_more.click()
                    await self.random_delay()
                else:
                    break

            # Extract comments
            comment_items = page.locator('[class*="comment-item"], [class*="commentItem"], [class*="reply-item"]')
            count = await comment_items.count()
            for i in range(count):
                item = comment_items.nth(i)
                try:
                    nickname_el = item.locator('[class*="name"], [class*="nickname"], [class*="user"]').first
                    content_el = item.locator('[class*="content"], [class*="text"]').first
                    nickname = await nickname_el.inner_text()
                    content = await content_el.inner_text()
                    if nickname and content:
                        comments.append((nickname.strip(), content.strip()))
                except Exception:
                    continue

        except Exception as e:
            logger.error(f"[toutiao] Failed to scrape {url}: {e}")
            raise
        finally:
            await page.close()
            await context.close()

        return comments
```

- [ ] **Step 2: Commit**

```bash
git add scrapers/toutiao.py
git commit -m "feat: add Toutiao comment scraper"
```

---

### Task 8: Weibo Scraper

**Files:**
- Create: `scrapers/weibo.py`

- [ ] **Step 1: Implement Weibo scraper**

```python
# scrapers/weibo.py
import logging
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class WeiboScraper(BaseScraper):
    platform_name = "weibo"

    def _get_login_url(self) -> str:
        return "https://weibo.com"

    async def scrape(self, url: str) -> list[tuple[str, str]]:
        comments = []
        context = await self.new_context()
        page = await context.new_page()
        try:
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await self.random_delay()

            # Wait for the post page
            try:
                await page.wait_for_selector('[class*="comment"], [class*="Comment"]', timeout=10000)
            except Exception:
                logger.warning(f"[weibo] Comment section not found for {url}")
                return comments

            # Click "展开" to expand truncated comments
            for _ in range(10):
                expand = page.locator('text="展开"').first
                if await expand.count() > 0:
                    await expand.click()
                    await self.random_delay()
                else:
                    break

            # Scroll to load more comments
            for _ in range(50):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await self.random_delay()
                load_more = page.locator('text="加载更多", text="查看更多"').first
                if await load_more.count() > 0:
                    await load_more.click()
                    await self.random_delay()
                else:
                    break

            # Extract comments
            comment_items = page.locator('[class*="comment-item"], [class*="CommentItem"], [class*="card-comment"]')
            count = await comment_items.count()
            for i in range(count):
                item = comment_items.nth(i)
                try:
                    nickname_el = item.locator('[class*="name"], [class*="nickname"]').first
                    content_el = item.locator('[class*="text"], [class*="content"]').first
                    nickname = await nickname_el.inner_text()
                    content = await content_el.inner_text()
                    if nickname and content:
                        comments.append((nickname.strip(), content.strip()))
                except Exception:
                    continue

        except Exception as e:
            logger.error(f"[weibo] Failed to scrape {url}: {e}")
            raise
        finally:
            await page.close()
            await context.close()

        return comments
```

- [ ] **Step 2: Commit**

```bash
git add scrapers/weibo.py
git commit -m "feat: add Weibo comment scraper"
```

---

### Task 9: Main Orchestrator

**Files:**
- Create: `main.py`

- [ ] **Step 1: Implement main.py with Excel I/O and orchestration**

```python
# main.py
import argparse
import asyncio
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

import openpyxl

import config
from scrapers import match_platform
from scrapers.douyin import DouyinScraper
from scrapers.xiaohongshu import XiaohongshuScraper
from scrapers.wechat import WechatScraper
from scrapers.toutiao import ToutiaoScraper
from scrapers.weibo import WeiboScraper

SCRAPER_MAP = {
    "douyin": DouyinScraper,
    "xiaohongshu": XiaohongshuScraper,
    "wechat": WechatScraper,
    "toutiao": ToutiaoScraper,
    "weibo": WeiboScraper,
}

logger = logging.getLogger("scraper")


def setup_logging():
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = config.LOG_DIR / f"scrape_{datetime.now():%Y%m%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def read_input_excel(filepath: str | Path) -> list[dict]:
    """Read input Excel and return rows with valid URLs and supported platforms."""
    wb = openpyxl.load_workbook(filepath, read_only=True)
    ws = wb.active
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        url = row[config.COL_URL]
        if not url or not isinstance(url, str) or not url.startswith("http"):
            continue
        platform_key = match_platform(url)
        if platform_key is None:
            continue
        rows.append({
            "serial": row[config.COL_SERIAL],
            "title": str(row[config.COL_TITLE] or ""),
            "source": str(row[config.COL_SOURCE] or ""),
            "author": str(row[config.COL_AUTHOR] or ""),
            "url": url,
            "platform_key": platform_key,
        })
    wb.close()
    return rows


def write_comments_excel(outdir: str | Path, comments: list[tuple[str, str]]) -> Path:
    """Write comments to an Excel file in the given directory."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    filepath = outdir / "comments.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["序号", "ID名称", "评论内容"])
    for idx, (nickname, content) in enumerate(comments, start=1):
        ws.append([idx, nickname, content])
    wb.save(filepath)
    return filepath


def write_summary_report(output_dir: Path, results: list[dict]):
    """Write the summary report."""
    filepath = output_dir / f"scrape_report_{datetime.now():%Y%m%d}.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["序号", "平台", "标题", "URL", "状态", "评论数", "错误信息"])
    for idx, r in enumerate(results, start=1):
        ws.append([
            idx,
            r.get("platform", ""),
            r.get("title", ""),
            r.get("url", ""),
            r.get("status", ""),
            r.get("comment_count", 0),
            r.get("error", ""),
        ])
    wb.save(filepath)
    logger.info(f"汇总报告已保存: {filepath}")


def build_output_dirname(row: dict) -> str:
    """Build output directory name from row data."""
    today = datetime.now().strftime("%Y%m%d")
    title_clean = config.sanitize_filename(row["title"])
    return f"{row['platform_key']}_{title_clean}_{today}"


async def login_all():
    """Interactive login for all platforms."""
    for name, scraper_cls in SCRAPER_MAP.items():
        scraper = scraper_cls()
        await scraper.login_interactive()


async def scrape_all(input_file: str | Path):
    """Main scraping loop."""
    rows = read_input_excel(input_file)
    total = len(rows)
    logger.info(f"开始抓取，共 {total} 条链接")

    results = []
    success_count = 0
    fail_count = 0

    # Group by platform to reuse browser sessions
    for platform_key, scraper_cls in SCRAPER_MAP.items():
        platform_rows = [r for r in rows if r["platform_key"] == platform_key]
        if not platform_rows:
            continue

        scraper = scraper_cls()
        await scraper.start()
        try:
            for row in platform_rows:
                url = row["url"]
                title_short = row["title"][:30]
                logger.info(f"[{platform_key}] 正在抓取: {url}")

                try:
                    comments = await scraper.scrape(url)
                    count = len(comments)
                    logger.info(f"[{platform_key}] 抓取成功，获得 {count} 条评论")

                    # Write per-post Excel
                    dirname = build_output_dirname(row)
                    outdir = config.OUTPUT_DIR / dirname
                    write_comments_excel(outdir, comments)

                    results.append({
                        "platform": platform_key,
                        "title": row["title"],
                        "url": url,
                        "status": "成功",
                        "comment_count": count,
                        "error": "",
                    })
                    success_count += 1

                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"[{platform_key}] 抓取失败: {url} - {error_msg}")
                    results.append({
                        "platform": platform_key,
                        "title": row["title"],
                        "url": url,
                        "status": "失败",
                        "comment_count": 0,
                        "error": error_msg,
                    })
                    fail_count += 1

        finally:
            await scraper.stop()

    # Write summary
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_summary_report(config.OUTPUT_DIR, results)
    logger.info(f"抓取完成，成功: {success_count}，失败: {fail_count}")


def main():
    parser = argparse.ArgumentParser(description="多平台评论抓取工具")
    parser.add_argument("--login", action="store_true", help="交互式登录各平台并保存Cookie")
    parser.add_argument("--input", default=str(config.INPUT_FILE), help="输入Excel文件路径")
    args = parser.parse_args()

    setup_logging()

    config.COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.login:
        asyncio.run(login_all())
    else:
        asyncio.run(scrape_all(args.input))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the Excel I/O tests**

Run: `python -m pytest tests/test_excel_io.py -v`
Expected: All 3 tests pass

- [ ] **Step 3: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: add main orchestrator with Excel I/O and platform dispatch"
```

---

### Task 10: Manual Verification

- [ ] **Step 1: Verify project structure**

Run: `ls -R` (or `dir /s` on Windows)
Expected: All files from the file map exist

- [ ] **Step 2: Run all tests one final time**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 3: Dry run with --help**

Run: `python main.py --help`
Expected: Shows usage with `--login` and `--input` options

- [ ] **Step 4: Test login flow (manual)**

Run: `python main.py --login`
Expected: Browser opens for each platform, user can login, cookies are saved to `cookies/`

- [ ] **Step 5: Test scraping with a single URL (manual)**

Create a small test Excel with 1-2 known URLs, run:
Run: `python main.py --input test_sample.xlsx`
Expected: Comments appear in `output/` directory

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "chore: final cleanup and verification"
```
