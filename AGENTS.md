# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

Multi-platform Chinese social media comment scraper. Reads post URLs from an Excel file, uses Playwright browser automation to scrape all comments (including replies) from 4 platforms, and outputs per-post Excel files plus a summary report.

Supported platforms: Douyin (抖音), Xiaohongshu (小红书), Toutiao (今日头条), Weibo (新浪微博).

WeChat platforms use PC desktop automation: WeChat Official Accounts (微信公众号) and WeChat Channels (微信视频号) — require WeChat PC client running with `--wechat-mode pc` (default).

## Commands

### Setup
```bash
pip install -r requirements.txt
playwright install chromium
```

### Run Tests
```bash
python -m pytest tests/ -v                    # all tests
python -m pytest tests/test_config.py -v      # single test file
```

### Login (first-time, per platform)
```bash
python main.py --login                                    # all platforms
python main.py --login --platforms xiaohongshu,weibo      # specific platforms
```

### WeChat PC Setup
```bash
# 1. Start WeChat PC client and log in
# 2. Run with WeChat platforms
python main.py --run --platforms wechat,wechat_channels
# Or use --wechat-mode to specify mode (pc is default)
python main.py --run --wechat-mode pc
```

### Scrape
```bash
python main.py                                # default input (example.xlsx)
python main.py --input path/to/custom.xlsx    # custom input
```

## Architecture

### Scraper Pattern
`BaseScraper` (scrapers/base.py) is the abstract base class providing browser lifecycle, cookie persistence, scrolling, and dialog handling. Each platform scraper subclasses it and implements:
- `platform_name` class attribute
- `_get_login_url()` → login/home page URL
- `scrape(url)` → `list[tuple[str, str]]` of `(nickname, comment_text)`

### Platform-Specific Strategies
- **Douyin**: DOM extraction with `[data-e2e="comment-item"]` selectors, coordinate-based reply expansion
- **Xiaohongshu**: Persistent browser context, interleaved scroll-and-expand cycles
- **Toutiao**: Comment panel button click, multi-strategy DOM extraction
- **Weibo**: API response interception (buildComments endpoint), pagination via max_id
- **WeChat Official Accounts (PC)**: Window-relative coordinates + OCR extraction via `WechatOfficialPcScraper`
- **WeChat Channels (PC)**: Window-relative coordinates + OCR extraction via `WechatChannelsPcScraper`

### Orchestrator (main.py)
URLs are grouped by platform so one browser session serves multiple URLs. Output goes to `output/{platform}_{title}_{date}/comments.xlsx` with a summary report at `output/scrape_report_{date}.xlsx`.

### Key Config (config.py)
All paths, delays, Excel column indices, and platform URL patterns are centralized here.

## Conventions
- Cookie files: `cookies/{platform}.json`
- Debug screenshots: `output/debug/`
- Logs: `logs/scrape_YYYYMMDD.log`
- Custom exceptions: `NoCommentsError` (Douyin), `NoteUnavailableError` (Xiaohongshu)
