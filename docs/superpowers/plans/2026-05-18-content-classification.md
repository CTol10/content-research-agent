# Content Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract post content from Xiaohongshu/Douyin/Weibo scrapers and classify with DeepSeek API into 16 tags with sentiment + hotel comparison detection.

**Architecture:** Scrapers return `dict` with `post_content` and `comments`. A new `classifier.py` module calls DeepSeek API to analyze content. `main.py` orchestrates scraping → classification → Excel output with 3 sheets.

**Tech Stack:** Playwright (existing), DeepSeek API (`requests`), openpyxl (existing)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `config.py` | Modify | Add DeepSeek API config (API key, model, base URL) |
| `classifier.py` | Create | DeepSeek API call, prompt construction, JSON parsing |
| `tests/test_classifier.py` | Create | Unit tests for prompt building and response parsing |
| `scrapers/weibo.py` | Modify | Extract post content from API response, return dict |
| `scrapers/xiaohongshu.py` | Modify | Extract post content from DOM, return dict |
| `scrapers/douyin.py` | Modify | Extract post content from DOM, return dict |
| `main.py` | Modify | Add `write_content_analysis()`, update `scrape_all()` flow |

---

### Task 1: Add DeepSeek API config

**Files:**
- Modify: `config.py`

- [ ] **Step 1: Add imports and config constants**

Add to the top of `config.py`:

```python
import os
```

Add after the `WECHAT_CDP_URL` line:

```python
# DeepSeek API
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
```

- [ ] **Step 2: Verify config loads**

Run: `python -c "import config; print(config.DEEPSEEK_API_KEY[:5] if config.DEEPSEEK_API_KEY else 'NOT SET')"`
Expected: Prints API key prefix or "NOT SET"

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add DeepSeek API config"
```

---

### Task 2: Create classifier module

**Files:**
- Create: `classifier.py`

- [ ] **Step 1: Create classifier.py with prompt and API call**

```python
# classifier.py
"""Content classification using DeepSeek API.

Analyzes post content against 16 predefined tags with sentiment,
and detects whether the post compares with other hotels.
"""
import json
import logging

import requests

import config

logger = logging.getLogger(__name__)

TAGS = [
    "餐食", "茶饮", "服务", "客房面积", "装修", "公区设计",
    "客房设计", "价格", "客房用品", "会员权益", "卫生", "睡眠",
    "洗沐", "地理位置", "行业营建设计", "行业观察",
]

SYSTEM_PROMPT = """你是一个酒店行业内容分析专家。分析用户发布的帖子内容，完成以下任务：

1. 从以下16个标签中，匹配帖子内容涉及的标签（可多选）：
餐食、茶饮、服务、客房面积、装修、公区设计、客房设计、价格、客房用品、会员权益、卫生、睡眠、洗沐、地理位置、行业营建设计、行业观察

2. 对每个匹配的标签判定情感倾向：
- 正面：明确夸奖全季大观的，或者发布了看好大观的观点
- 中性：有说到优势也有说到一些无关痛痒的劣势；或者就是不带任何正/负观点的行业媒体文章
- 负面：提到了明确的劣势

3. 判断帖子内容是否提到了除"全季大观"以外的其他酒店（包括品牌名），如果是则标记"是"，否则标记"否"。

请严格返回以下JSON格式，不要添加任何其他内容：
{
    "tags": ["标签-情感", "标签-情感"],
    "has_comparison": "是或否"
}

示例输出：
{"tags": ["睡眠-正面", "服务-负面"], "has_comparison": "否"}"""


def build_user_prompt(post_content: str) -> str:
    """Build the user message for the API call."""
    return f"请分析以下帖子内容：\n\n{post_content}"


def parse_response(response_text: str) -> dict:
    """Parse the API response JSON into structured result.

    Returns:
        {"tags": ["睡眠-正面", ...], "has_comparison": "是"} or
        {"tags": [], "has_comparison": "否"} on failure
    """
    default_result = {"tags": [], "has_comparison": "否"}

    try:
        # Try to extract JSON from the response
        text = response_text.strip()
        # Handle markdown code blocks
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        result = json.loads(text)

        # Validate structure
        if "tags" not in result or "has_comparison" not in result:
            logger.warning(f"[classifier] Missing keys in response: {result}")
            return default_result

        # Validate tags format
        valid_tags = []
        for tag_str in result["tags"]:
            parts = tag_str.rsplit("-", 1)
            if len(parts) == 2 and parts[0] in TAGS and parts[1] in ("正面", "中性", "负面"):
                valid_tags.append(tag_str)
            else:
                logger.warning(f"[classifier] Invalid tag format: {tag_str}")

        return {
            "tags": valid_tags,
            "has_comparison": "是" if result["has_comparison"] == "是" else "否",
        }

    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"[classifier] Failed to parse response: {e}")
        return default_result


def classify_content(post_content: str) -> dict:
    """Classify post content using DeepSeek API.

    Args:
        post_content: The post text to classify.

    Returns:
        {"tags": ["睡眠-正面", ...], "has_comparison": "是/否"}
    """
    if not config.DEEPSEEK_API_KEY:
        logger.error("[classifier] DEEPSEEK_API_KEY not set")
        return {"tags": [], "has_comparison": "否"}

    if not post_content or not post_content.strip():
        return {"tags": [], "has_comparison": "否"}

    try:
        response = requests.post(
            f"{config.DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": config.DEEPSEEK_MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(post_content)},
                ],
                "temperature": 0.1,
                "max_tokens": 500,
            },
            timeout=30,
        )
        response.raise_for_status()

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return parse_response(content)

    except requests.RequestException as e:
        logger.error(f"[classifier] API call failed: {e}")
        return {"tags": [], "has_comparison": "否"}
    except (KeyError, IndexError) as e:
        logger.error(f"[classifier] Unexpected response structure: {e}")
        return {"tags": [], "has_comparison": "否"}
```

- [ ] **Step 2: Verify module imports**

Run: `python -c "from classifier import classify_content, parse_response, build_user_prompt; print('OK')"`
Expected: Prints "OK"

- [ ] **Step 3: Commit**

```bash
git add classifier.py
git commit -m "feat: add DeepSeek API classifier module"
```

---

### Task 3: Add classifier unit tests

**Files:**
- Create: `tests/test_classifier.py`

- [ ] **Step 1: Create test file**

```python
# tests/test_classifier.py
"""Tests for the classifier module."""
import json
from unittest.mock import MagicMock, patch

from classifier import build_user_prompt, classify_content, parse_response, TAGS


class TestBuildUserPrompt:
    def test_builds_prompt_with_content(self):
        result = build_user_prompt("全季大观的床睡的好舒服")
        assert "全季大观的床睡的好舒服" in result
        assert "请分析" in result

    def test_handles_empty_content(self):
        result = build_user_prompt("")
        assert "请分析" in result


class TestParseResponse:
    def test_valid_json_single_tag(self):
        response = json.dumps({"tags": ["睡眠-正面"], "has_comparison": "否"})
        result = parse_response(response)
        assert result["tags"] == ["睡眠-正面"]
        assert result["has_comparison"] == "否"

    def test_valid_json_multiple_tags(self):
        response = json.dumps({
            "tags": ["睡眠-正面", "服务-负面", "餐食-中性"],
            "has_comparison": "是"
        })
        result = parse_response(response)
        assert len(result["tags"]) == 3
        assert result["has_comparison"] == "是"

    def test_json_in_code_block(self):
        response = '```json\n{"tags": ["睡眠-正面"], "has_comparison": "否"}\n```'
        result = parse_response(response)
        assert result["tags"] == ["睡眠-正面"]

    def test_json_in_plain_code_block(self):
        response = '```\n{"tags": ["睡眠-正面"], "has_comparison": "否"}\n```'
        result = parse_response(response)
        assert result["tags"] == ["睡眠-正面"]

    def test_invalid_json_returns_default(self):
        result = parse_response("not json at all")
        assert result["tags"] == []
        assert result["has_comparison"] == "否"

    def test_missing_keys_returns_default(self):
        result = parse_response(json.dumps({"wrong_key": []}))
        assert result["tags"] == []

    def test_invalid_tag_format_filtered(self):
        response = json.dumps({
            "tags": ["睡眠-正面", "invalid-tag", "服务-unknown"],
            "has_comparison": "否"
        })
        result = parse_response(response)
        assert result["tags"] == ["睡眠-正面"]

    def test_all_valid_tags_accepted(self):
        for tag in TAGS:
            response = json.dumps({"tags": [f"{tag}-正面"], "has_comparison": "否"})
            result = parse_response(response)
            assert result["tags"] == [f"{tag}-正面"], f"Tag {tag} should be valid"

    def test_invalid_sentiment_filtered(self):
        response = json.dumps({"tags": ["睡眠-开心"], "has_comparison": "否"})
        result = parse_response(response)
        assert result["tags"] == []

    def test_has_comparison_validation(self):
        for val in ["是", "否"]:
            response = json.dumps({"tags": [], "has_comparison": val})
            result = parse_response(response)
            assert result["has_comparison"] == val

    def test_has_comparison_invalid_value(self):
        response = json.dumps({"tags": [], "has_comparison": "maybe"})
        result = parse_response(response)
        assert result["has_comparison"] == "否"


class TestClassifyContent:
    def test_empty_content_returns_default(self):
        result = classify_content("")
        assert result["tags"] == []
        assert result["has_comparison"] == "否"

    def test_whitespace_content_returns_default(self):
        result = classify_content("   ")
        assert result["tags"] == []

    @patch("classifier.config")
    @patch("classifier.requests.post")
    def test_successful_classification(self, mock_post, mock_config):
        mock_config.DEEPSEEK_API_KEY = "test-key"
        mock_config.DEEPSEEK_MODEL = "deepseek-chat"
        mock_config.DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": json.dumps({
                "tags": ["睡眠-正面", "服务-负面"],
                "has_comparison": "是"
            })}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        result = classify_content("全季大观的床睡的好舒服，但是服务太差了，比万豪差远了")
        assert "睡眠-正面" in result["tags"]
        assert "服务-负面" in result["tags"]
        assert result["has_comparison"] == "是"

    @patch("classifier.config")
    def test_no_api_key_returns_default(self, mock_config):
        mock_config.DEEPSEEK_API_KEY = ""
        result = classify_content("some content")
        assert result["tags"] == []

    @patch("classifier.config")
    @patch("classifier.requests.post")
    def test_api_failure_returns_default(self, mock_post, mock_config):
        import requests
        mock_config.DEEPSEEK_API_KEY = "test-key"
        mock_config.DEEPSEEK_MODEL = "deepseek-chat"
        mock_config.DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
        mock_post.side_effect = requests.RequestException("timeout")

        result = classify_content("some content")
        assert result["tags"] == []
        assert result["has_comparison"] == "否"
```

- [ ] **Step 2: Run tests**

Run: `python -m pytest tests/test_classifier.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_classifier.py
git commit -m "test: add classifier unit tests"
```

---

### Task 4: Modify Weibo scraper to extract post content

**Files:**
- Modify: `scrapers/weibo.py`

- [ ] **Step 1: Add post content extraction to API response handler**

In `scrapers/weibo.py`, modify the `scrape()` method to capture post content from the page.

Add a new instance variable to collect post content. After the `api_responses = []` line, add:

```python
        post_content = ""
```

After the `await asyncio.sleep(8)` line, add DOM extraction for post content:

```python
            # Extract post content
            try:
                post_content = await page.evaluate("""
                    () => {
                        // Try multiple selectors for Weibo post content
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
```

Change the return statement from:

```python
            return all_comments
```

to:

```python
            return {"post_content": post_content, "comments": all_comments}
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "from scrapers.weibo import WeiboScraper; print('OK')"`
Expected: Prints "OK"

- [ ] **Step 3: Commit**

```bash
git add scrapers/weibo.py
git commit -m "feat(weibo): extract post content from page"
```

---

### Task 5: Modify Xiaohongshu scraper to extract post content

**Files:**
- Modify: `scrapers/xiaohongshu.py`

- [ ] **Step 1: Add post content extraction after page navigation**

In `scrapers/xiaohongshu.py`, add post content extraction after the login check section (after the `if "login" in page.url.lower()` block, before the scrolling loop).

Add this code:

```python
            # Extract post content
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
```

Change the return statement from:

```python
            return comments
```

to:

```python
            return {"post_content": post_content, "comments": comments}
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "from scrapers.xiaohongshu import XiaohongshuScraper; print('OK')"`
Expected: Prints "OK"

- [ ] **Step 3: Commit**

```bash
git add scrapers/xiaohongshu.py
git commit -m "feat(xiaohongshu): extract post content from page"
```

---

### Task 6: Modify Douyin scraper to extract post content

**Files:**
- Modify: `scrapers/douyin.py`

- [ ] **Step 1: Add post content extraction after popup dismissal**

In `scrapers/douyin.py`, add post content extraction after `await self._dismiss_popups(page)` and before `await self._click_comment_tab(page)`.

Add this code:

```python
            # Extract post content
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
```

Change the return statement from:

```python
            return comments
```

to:

```python
            return {"post_content": post_content, "comments": comments}
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "from scrapers.douyin import DouyinScraper; print('OK')"`
Expected: Prints "OK"

- [ ] **Step 3: Commit**

```bash
git add scrapers/douyin.py
git commit -m "feat(douyin): extract post content from page"
```

---

### Task 7: Add write_content_analysis to main.py

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add the import for classifier**

Add to the imports section of `main.py`:

```python
from classifier import classify_content
```

- [ ] **Step 2: Add write_content_analysis function**

Add this function after `write_comments_excel()`:

```python
def write_content_analysis(outdir, row: dict, classification: dict):
    """Write content analysis to Sheet 3 of the comments Excel file."""
    outdir = Path(outdir)
    filepath = outdir / "comments.xlsx"

    if filepath.exists():
        wb = openpyxl.load_workbook(filepath)
    else:
        wb = openpyxl.Workbook()
        wb.active  # Ensure at least one sheet exists

    # Create or get Sheet 3
    if "内容分析" in wb.sheetnames:
        ws = wb["内容分析"]
    else:
        if len(wb.sheetnames) == 1 and wb.active.title == "Sheet":
            ws = wb.active
            ws.title = "内容分析"
        else:
            ws = wb.create_sheet("内容分析")
        ws.append(["序号", "标题", "内容", "标签", "是否比较其他酒店"])

    # Format tags: "睡眠-正面,服务-负面"
    tags_str = ",".join(classification.get("tags", []))
    has_comparison = classification.get("has_comparison", "否")

    ws.append([
        row.get("serial", ""),
        row.get("title", ""),
        classification.get("post_content", ""),
        tags_str,
        has_comparison,
    ])

    wb.save(filepath)
```

- [ ] **Step 3: Verify syntax**

Run: `python -c "from main import write_content_analysis; print('OK')"`
Expected: Prints "OK"

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: add write_content_analysis for Sheet 3 output"
```

---

### Task 8: Update scrape_all to use new return format and call classifier

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Update the scrape result handling in scrape_all**

In `main.py`, find the `scrape_all` function. Replace the comment scraping section:

Find this code block (around line 183-198):

```python
                try:
                    comments = await scraper.scrape(url)
                    count = len(comments)
                    logger.info(f"[{platform_key}] 抓取成功，获得 {count} 条评论")

                    dirname = build_output_dirname(row)
                    outdir = config.OUTPUT_DIR / dirname
                    write_comments_excel(outdir, comments)

                    results.append({
```

Replace with:

```python
                try:
                    result = await scraper.scrape(url)
                    post_content = result.get("post_content", "")
                    comments = result.get("comments", [])
                    count = len(comments)
                    logger.info(f"[{platform_key}] 抓取成功，获得 {count} 条评论")

                    dirname = build_output_dirname(row)
                    outdir = config.OUTPUT_DIR / dirname
                    write_comments_excel(outdir, comments)

                    # Classify content
                    classification = {"tags": [], "has_comparison": "否"}
                    if post_content:
                        try:
                            classification = classify_content(post_content)
                            classification["post_content"] = post_content
                            logger.info(f"[{platform_key}] 分类完成: {classification['tags']}")
                        except Exception as e:
                            logger.error(f"[{platform_key}] 分类失败: {e}")

                    write_content_analysis(outdir, row, classification)

                    results.append({
```

- [ ] **Step 2: Verify full module loads**

Run: `python -c "import main; print('OK')"`
Expected: Prints "OK"

- [ ] **Step 3: Run existing tests to ensure no regressions**

Run: `python -m pytest tests/ -v`
Expected: All existing tests PASS

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: integrate classifier into scrape flow, add Sheet 3 output"
```

---

### Task 9: Update remaining scrapers (WeChat, Toutiao, WeChat Channels)

These scrapers also need to return dict format for consistency. WeChat Channels and Toutiao will return empty post_content for now since their extraction is more complex.

**Files:**
- Modify: `scrapers/wechat.py`
- Modify: `scrapers/toutiao.py`
- Modify: `scrapers/wechat_channels.py`

- [ ] **Step 1: Update WeChat scraper return format**

In `scrapers/wechat.py`, find the return statement in `scrape()` and change:

```python
            return comments
```

to:

```python
            return {"post_content": "", "comments": comments}
```

- [ ] **Step 2: Update Toutiao scraper return format**

In `scrapers/toutiao.py`, find the return statement in `scrape()` and change:

```python
            return comments
```

to:

```python
            return {"post_content": "", "comments": comments}
```

- [ ] **Step 3: Update WeChat Channels scraper return format**

In `scrapers/wechat_channels.py`, find the return statement in `scrape()` and change:

```python
            return all_comments
```

to:

```python
            return {"post_content": "", "comments": all_comments}
```

- [ ] **Step 4: Verify all scrapers import cleanly**

Run: `python -c "from scrapers.wechat import WechatScraper; from scrapers.toutiao import ToutiaoScraper; from scrapers.wechat_channels import WechatChannelsScraper; print('OK')"`
Expected: Prints "OK"

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add scrapers/wechat.py scrapers/toutiao.py scrapers/wechat_channels.py
git commit -m "refactor: update remaining scrapers to return dict format"
```
