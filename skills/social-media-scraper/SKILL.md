---
name: social-media-scraper
description: "Scrape comments and post content from Chinese social media platforms (Douyin, Xiaohongshu, Weibo). Reads URLs from an Excel file, uses Playwright browser automation, and returns raw structured data for agent processing."
---

# Social Media Comment Scraper

Scrape comments (including replies) and post content from Douyin, Xiaohongshu, and Weibo. Returns raw structured JSON data — the AI agent handles classification, sentiment analysis, and output formatting.

## Capabilities

- **Douyin**: DOM extraction with `[data-e2e="comment-item"]` selectors, coordinate-based reply expansion
- **Xiaohongshu**: Persistent browser context, anti-detection (stealth JS + warmup), interleaved scroll-and-expand cycles, reply thread expansion
- **Weibo**: API response interception (`buildComments` endpoint), pagination via `max_id`
- **Beginner-friendly**: Auto-dependency installation, `--setup` environment check, Chinese error messages for all failure cases

## Quick Start

1. **自动安装环境**:
```bash
python skills/social-media-scraper/src/main.py --setup
```

2. **一键执行**（推荐）:
```bash
python skills/social-media-scraper/src/main.py --run --input path/to/input.xlsx
```
自动检查登录状态，缺失的平台会打开浏览器，登录成功后自动保存 Cookie 并开始抓取。
无需分步操作，无需 agent 协调。

## 完整工作流

执行此 skill 时**必须严格按以下阶段顺序执行**，详细约束见 `skills/social-media-scraper/harness.yaml`。

```
┌─────────────┐     ┌───────────────────────────────┐
│ 1.环境准备   │────▶│ 2.一键执行（--run）              │
└─────────────┘     │   自动登录 + 自动抓取            │
                    └───────────────────────────────┘
                                              │
                                              ▼
                                      ┌─────────────┐
                                      │ 3.批量分类   │
                                      └─────────────┘
                                              │
                                              ▼
                                      ┌─────────────┐
                                      │ 4.生成Excel  │
                                      └─────────────┘
```

### Phase 1: 环境准备

```bash
python skills/social-media-scraper/src/main.py --setup
```
首次使用时运行，自动安装 Playwright 和 openpyxl。

### Phase 2: 一键执行（--run）

使用 `--run` 一条命令完成登录+抓取：

```bash
python skills/social-media-scraper/src/main.py --run --input path/to/input.xlsx
```

`--run` 自动处理：
1. 检查各平台 Cookie 是否存在
2. 缺失的平台 → 自动打开浏览器
3. 用户在浏览器中登录 → 自动检测登录成功（URL 变化）
4. 自动保存 Cookie → 自动开始抓取

**无需分步操作，无需 agent 协调，无需用户文字确认。**

**⚠️ 沙箱要求：`--run` 必须禁用沙箱（`dangerouslyDisableSandbox: true`）。**

### Phase 3: 抓取数据

```bash
python skills/social-media-scraper/src/main.py --input path/to/input.xlsx
```

输出 `===RAW_RESULTS_START===` 到 `===RAW_RESULTS_END===` 之间的 JSON 数组。

**约束：抓取期间 agent 不做任何分类操作，必须等进程结束获取完整 JSON。**

**⚠️ 超时恢复：根据链接数量选择执行方式：**

- **链接 ≤ 20 条**：直接跑，不用批次
  ```bash
  python skills/social-media-scraper/src/main.py --input path/to/input.xlsx
  ```

- **链接 > 20 条**：使用批次循环，每轮 20 条，避免 Bash 超时
  ```bash
  while true; do
    output=$(python skills/social-media-scraper/src/main.py --input path/to/input.xlsx --resume --batch-size 20 2>&1)
    echo "$output"
    if echo "$output" | grep -q "所有链接均已抓取完成"; then
      break
    fi
  done
  ```

Scraper 支持增量 checkpoint 保存（JSONL 格式），每次 URL 抓完后立即写入 `output/scrape_checkpoint.jsonl`。
带 `--resume` 启动时会自动加载 checkpoint，跳过已完成的 URL，全部完成后自动清理 checkpoint 文件。

### Phase 4: 批量分类

抓取全部完成后，agent 一次性处理所有成功结果：
1. **通读全部数据** — 了解数据全貌
2. **确定标签体系** — 统一命名（如"装修"、"服务"、"价格"）
3. **批量标注** — 情感偏向（正面/负面/中性）、标签、是否与其他酒店比较（是/否）

**约束：禁止边抓取边分类，禁止逐条分类，标签命名必须全局一致。**

### Phase 5: 生成 Excel

分类完成后，agent 使用 openpyxl 生成最终 Excel（格式见下方）。

**约束：禁止在分类未完成时开始生成，分类字段不得留空。**

### 为什么必须批量处理

| 方式 | 问题 |
|------|------|
| 边抓边分类 | 抓取每条后都要切上下文做分类，效率低；标签不一致；中途失败导致部分数据丢失 |
| 批量处理 | 抓取专注抓取，分类专注分类，标签一致性强，失败项可跳过 |

### Scraping Output

The scraper outputs JSON with this structure:
```json
[
  {
    "serial": 1,
    "title": "帖子标题",
    "url": "https://...",
    "platform": "douyin",
    "status": "成功",
    "error": "",
    "post_content": "帖子正文内容...",
    "comments": [
      {"nickname": "用户A", "content": "评论内容"},
      {"nickname": "用户B", "content": "另一条评论"}
    ]
  }
]
```

### Standard Excel Output Format

Agent 处理完抓取结果后，输出 Excel 应包含以下 3 个 Sheet：

**Sheet 1: 原始底表**（直接复制输入数据，不做修改）

| 序号 | 标题 | 链接 | 来源 | 平台 | 日期 | 作者 |
|------|------|------|------|------|------|------|

**Sheet 2: 评论+标签+情感**（每条评论一行，agent 负责填写情感偏向、标签、是否与其他酒店比较）

| 序号 | 情感偏向 | 标签 | 是否与其他酒店比较 | 内容 | 平台 |
|------|----------|------|---------------------|------|------|

- 序号：自增编号
- 情感偏向：正面 / 负面 / 中性（agent 分析）
- 标签：内容主题标签，如"装修"、"服务"、"价格"等，无标签填"/"
- 是否与其他酒店比较：是 / 否（agent 分析）
- 内容：评论原文
- 平台：抖音 / 小红书 / 微博

**Sheet 3: 正文内容+标签+情感**（每个帖子一行，agent 负责填写情感偏向、标签、是否与其他酒店比较）

| 排序 | 标题 | 平台 | 内容摘要 | 情感偏向 | 标签 | 是否与其他酒店比较 | 原文/评论链接 |
|------|------|------|----------|----------|------|---------------------|---------------|

- 排序：自增编号
- 标题：帖子标题
- 平台：抖音 / 小红书 / 微博
- 内容摘要：帖子正文内容
- 情感偏向：正面 / 负面 / 中性（agent 分析）
- 标签：内容主题标签，无标签填"/"
- 是否与其他酒店比较：是 / 否（agent 分析）
- 原文/评论链接：帖子原始 URL

## Commands

### --run (一键执行，推荐)

自动检查登录 + 自动登录 + 抓取，一条命令搞定：
```bash
python skills/social-media-scraper/src/main.py --run --input path/to/input.xlsx
python skills/social-media-scraper/src/main.py --run --input path/to/input.xlsx --resume --batch-size 20
```

### --setup (环境检查与安装)

自动检查并安装所需依赖（Playwright, openpyxl）:
```bash
python skills/social-media-scraper/src/main.py --setup
```

### --login-open (打开登录浏览器)

打开浏览器供用户手动登录，不会阻塞 AI 对话:
```bash
python skills/social-media-scraper/src/main.py --login-open
python skills/social-media-scraper/src/main.py --login-open --platforms weibo
```

### --login-save (保存 Cookie)

从已打开的浏览器中保存 Cookie 并关闭浏览器:
```bash
python skills/social-media-scraper/src/main.py --login-save
```

### --login (一次性登录)

同时打开浏览器并等待保存（适用于手动终端使用）:
```bash
python skills/social-media-scraper/src/main.py --login
```

### --input (抓取评论)

从 Excel 文件读取链接并抓取评论:
```bash
python skills/social-media-scraper/src/main.py --input path/to/input.xlsx
python skills/social-media-scraper/src/main.py --input path/to/input.xlsx --platforms douyin
```

### --platforms (指定平台)

可选值: `douyin`, `xiaohongshu`, `weibo` (逗号分隔)

### --resume (断点续抓)

从上次中断处继续抓取，跳过已完成的 URL:
```bash
python skills/social-media-scraper/src/main.py --input path/to/input.xlsx --resume
```
适用于 Bash 工具超时中断后恢复抓取。Scraper 会在每次 URL 抓完后自动保存 checkpoint，全部完成后自动清理。

### --batch-size (批次抓取)

限制每轮抓取数量，配合 `--resume` 避免 Bash 超时:
```bash
python skills/social-media-scraper/src/main.py --input path/to/input.xlsx --resume --batch-size 10
```
每次只抓取 10 条 URL 然后安全退出，agent 循环调用直到全部完成。

## Input Excel Format

Columns (0-indexed):
- Col 0: 序号 (serial number)
- Col 1: 标题 (title)
- Col 2: URL (post link)
- Col 3: 来源 (source)
- Col 4: 平台 (platform)
- Col 5: 日期 (date)
- Col 6: 作者 (author)

## Error Messages

All errors are displayed in Chinese with clear guidance:
- `该视频暂无评论` - No comments available
- `笔记不可用（可能已被删除或设为私密）` - Note unavailable
- `需要重新登录（Cookie 已过期）` - Cookie expired, re-login needed
- `网络连接失败，请检查网络后重试` - Network connection failed
- `页面加载超时，可能网络较慢或平台繁忙` - Page load timeout
- `浏览器启动失败` - Browser launch failure

## Programmatic Usage

```python
import asyncio
from src.scrapers.douyin import DouyinScraper
from src.scrapers.xiaohongshu import XiaohongshuScraper
from src.scrapers.weibo import WeiboScraper

async def scrape_url(url: str, platform: str):
    scraper_map = {
        "douyin": DouyinScraper,
        "xiaohongshu": XiaohongshuScraper,
        "weibo": WeiboScraper,
    }
    scraper = scraper_map[platform]()
    await scraper.start()
    try:
        result = await scraper.scrape(url)
        # result = {"post_content": "...", "comments": [(nickname, content), ...]}
        return result
    finally:
        await scraper.stop()

result = asyncio.run(scrape_url("https://...", "xiaohongshu"))
```

## Architecture

```
src/
  config.py              # Paths, delays, column indices, platform patterns
  base_scraper.py        # Browser lifecycle, cookie persistence, CDP reconnection
  scrapers/
    __init__.py           # Platform URL matching
    douyin.py             # DOM extraction + coordinate-based reply expansion
    xiaohongshu.py        # Anti-detection + interleaved scroll/expand
    weibo.py              # API interception + max_id pagination
  main.py                 # CLI: auto-install, login, scrape (raw JSON output)
```

## Cookie Management

Cookies are stored in `cookies/{platform}.json`. To re-login a platform, delete its cookie file:
```bash
del cookies\xiaohongshu.json
python skills/social-media-scraper/src/main.py --login-open --platforms xiaohongshu
python skills/social-media-scraper/src/main.py --login-save
```

## Debug

Logs are written to `logs/scrape_YYYYMMDD.log`.
Results are saved to `output/scrape_result_YYYYMMDD_HHMMSS.json`.
