# WeChat Emulator Scraper Design

**Date**: 2026-06-05
**Status**: Draft
**Scope**: WeChat Official Account article comments + WeChat Channels comments/video, via Android emulator (LDPlayer) + Appium

---

## 1. Problem Statement

The project currently has two suspended WeChat scrapers:
- `scrapers/wechat.py` — Playwright + UA spoofing for 公众号文章评论, unreliable due to anti-bot detection
- `scrapers/wechat_channels.py` — pyautogui + OCR for 视频号评论, fragile due to fixed coordinates and OCR errors

We need a reliable approach that works consistently. The solution: run WeChat inside an Android emulator (LDPlayer), control it via ADB + Appium (UIAutomator2), and scrape comments from both 公众号文章 and 视频号.

---

## 2. Architecture

### 2.1 Class Hierarchy

```
BaseScraper (Playwright, existing)     AppiumBaseScraper (new)
  ├── DouyinScraper                      ├── WechatOfficialScraper
  ├── XiaohongshuScraper                 └── WechatChannelsEmulatorScraper
  ├── ToutiaoScraper
  └── WeiboScraper
```

`AppiumBaseScraper` is a parallel base class to `BaseScraper`, designed for Android app automation via Appium instead of web browser automation via Playwright. Both share the same interface contract (`start()`, `stop()`, `scrape(url) -> dict`) so they integrate into `main.py` identically.

### 2.2 New Files

| File | Purpose |
|------|---------|
| `scrapers/appium_base.py` | AppiumBaseScraper — ADB connection, Appium session lifecycle, common helpers |
| `scrapers/wechat_official.py` | WechatOfficialScraper — 公众号文章评论抓取 |
| `scrapers/wechat_channels_emulator.py` | WechatChannelsEmulatorScraper — 视频号评论 + 视频抓取 |

### 2.3 Modified Files

| File | Changes |
|------|---------|
| `config.py` | Add emulator/ADB/Appium config constants, add wechat_channels platform pattern |
| `main.py` | Register new scrapers, add `--emulator` flag |
| `requirements.txt` | Add `Appium-Python-Client>=3.0.0` |
| `scrapers/__init__.py` | Update `match_platform()` to handle `wechat_channels` URLs |

---

## 3. AppiumBaseScraper (`scrapers/appium_base.py`)

### 3.1 Responsibilities

- ADB connection management (connect/disconnect, port detection)
- Appium WebDriver session lifecycle
- Common mobile interaction helpers (tap, swipe, find element, screenshot)
- WeChat app launch and URL opening via Intent
- Login state verification

### 3.2 Interface

```python
class AppiumBaseScraper:
    platform_name: str = "base_emulator"

    # Config (class-level, overridable)
    ADB_HOST = "127.0.0.1"
    ADB_PORT = 5555
    APPIUM_URL = "http://127.0.0.1:4723"
    WECHAT_PACKAGE = "com.tencent.mm"
    WECHAT_ACTIVITY = "com.tencent.mm.ui.LauncherUI"

    def start(self) -> None:
        """Connect ADB → create Appium session → verify WeChat installed & logged in."""

    def stop(self) -> None:
        """Quit Appium session. ADB connection stays alive (emulator keeps running)."""

    def scrape(self, url: str) -> dict:
        """Abstract. Return {"post_content": str, "comments": [(nickname, text), ...]}."""

    # --- Helpers ---
    def open_url_in_wechat(self, url: str) -> None:
        """am start -a android.intent.action.VIEW -d "{url}" com.tencent.mm"""

    def swipe_up(self, duration_ms=800) -> None:
        """ADB swipe upward to scroll content."""

    def swipe_down(self, duration_ms=800) -> None:
        """ADB swipe downward."""

    def find_element(self, by, value, timeout=10):
        """Appium WebDriverWait + find_element wrapper."""

    def find_elements(self, by, value, timeout=10):
        """Appium WebDriverWait + find_elements wrapper."""

    def take_screenshot(self, name: str) -> str:
        """ADB screenshot, save to output/debug/{name}.png, return path."""

    def wait_for_element(self, by, value, timeout=30):
        """Wait for element to appear, return element."""

    def is_wechat_logged_in(self) -> bool:
        """Check if WeChat is at the main screen (logged in)."""

    def switch_to_webview_context(self) -> bool:
        """Switch Appium context from NATIVE_APP to WEBVIEW_com.tencent.mm."""

    def switch_to_native_context(self) -> None:
        """Switch back to NATIVE_APP context."""
```

### 3.3 ADB Connection Flow

```
1. Check if ADB server is running: adb start-server
2. Connect to emulator: adb connect 127.0.0.1:5555
3. Verify device connected: adb devices
4. Create Appium session with capabilities:
   - platformName: Android
   - deviceName: emulator-5554
   - appPackage: com.tencent.mm
   - appActivity: com.tencent.mm.ui.LauncherUI
   - automationName: UiAutomator2
   - noReset: true  (preserve login state)
   - autoGrantPermissions: true
```

---

## 4. WechatOfficialScraper (`scrapers/wechat_official.py`)

### 4.1 Purpose

Scrape comments from WeChat Official Account articles (mp.weixin.qq.com). Replaces existing `scrapers/wechat.py`.

### 4.2 Scraping Flow

```
1. open_url_in_wechat(url)  — Intent opens article in WeChat's built-in browser

2. Wait for page load
   - Switch to WEBVIEW context
   - Wait for #js_content or .rich_media_content element
   - If timeout: switch to native, check for "verification required" dialog

3. Extract post content
   - find_element(By.ID, "js_content").text
   - Or find_element(By.CSS_SELECTOR, ".rich_media_content").text

4. Scroll to comments section
   - Switch to native context
   - Swipe up until "写评论" button or comment section visible
   - Max 20 swipes

5. Load all comments
   - Loop: swipe up, check if new comments loaded
   - Stop when no new comments after 3 swipes

6. Expand replies
   - Find elements matching "x条回复" or "展开回复"
   - Click each, wait for reply list to load
   - Swipe within reply list to load more

7. Extract comments
   - Parse comment elements: nickname + text + optional reply_to
   - Return list of (nickname, comment_text) tuples

8. Return {"post_content": str, "comments": [...]}
```

### 4.3 Element Location Strategy

The comments section in WeChat articles can be either:
- **WebView rendered**: Use CSS selectors (`.discuss_item`, `.comment_item`, etc.)
- **Native UI overlay**: Use UIAutomator2 resource-ids or class names

Strategy: try WebView first (switch context, attempt CSS selectors). If that fails, fall back to native UI element location.

### 4.4 Edge Cases

| Scenario | Handling |
|----------|----------|
| Article requires verification | Screenshot, prompt user to complete verification in emulator, retry |
| No comments on article | Return empty comments list, log warning |
| Rate limited by WeChat | Random delay between URLs (2-5s), retry with exponential backoff |
| WebView context unavailable | Fall back to native UI parsing |

---

## 5. WechatChannelsEmulatorScraper (`scrapers/wechat_channels_emulator.py`)

### 5.1 Purpose

Scrape comments and download videos from WeChat Channels (视频号). Replaces existing `scrapers/wechat_channels.py` and `wechat_channels_ui.py`.

### 5.2 Scraping Flow

```
1. Open 视频号 content
   Option A: Intent open share link (https://channels.weixin.qq.com/...)
   Option B: Navigate within WeChat (发现 → 视频号 → search)

2. Wait for video to load
   - Detect video player element in native UI

3. Open comment panel
   - Find and tap comment button (💬 icon at bottom)
   - Wait for comment list to appear

4. Extract comments
   - Comments are in a RecyclerView (native UI)
   - Iterate comment item elements
   - Extract: nickname, comment text, timestamp

5. Load more comments
   - Swipe up within comment panel
   - Detect "no more comments" state

6. Expand replies
   - Find "展开回复" elements
   - Tap each, wait for reply list
   - Extract reply comments

7. Video download (optional)
   Priority: intercept video URL
   - Check Appium logs for media URLs
   - Or: ADB proxy + mitmproxy intercept

   Fallback: ADB screenrecord
   - adb shell screenrecord --time-limit 180 /sdcard/video.mp4
   - adb pull /sdcard/video.mp4
   - ffmpeg post-process (trim, transcode)

8. Return {"post_content": str, "comments": [...], "video_url": str|None}
```

### 5.3 Video Download Strategy

```
Priority 1: Network interception
  - Set up mitmproxy on host
  - Configure emulator to use host proxy (adb shell settings put global http_proxy ...)
  - Filter intercepted requests for .mp4/.m3u8
  - Download with original headers (Referer, Cookie)

Priority 2: ADB screenrecord
  - adb shell screenrecord --time-limit 180 /sdcard/ch_video.mp4
  - adb pull /sdcard/ch_video.mp4 output/
  - ffmpeg -i ch_video.mp4 -vf "crop=..." -c:a copy output_final.mp4
  - Limitations: max 3 min, quality depends on emulator resolution

Priority 3: Skip video
  - If both fail, log warning, return video_url=None
  - Comments are still scraped successfully
```

### 5.4 Element Location for 视频号

视频号 is a native Android UI (not WebView). Key elements:
- Video player: `android.widget.VideoView` or custom player class
- Comment button: accessibility description "评论" or resource-id pattern
- Comment list: `androidx.recyclerview.widget.RecyclerView`
- Comment item: contains `TextView` for nickname and text

Exact element identifiers need to be discovered via Appium Inspector during development. The scraper should have configurable element selectors (not hardcoded) to adapt to WeChat version changes.

---

## 6. Integration with main.py

### 6.1 Scraper Registration

```python
scraper_classes = {
    "douyin": DouyinScraper,
    "xiaohongshu": XiaohongshuScraper,
    "wechat": WechatOfficialScraper,
    "wechat_channels": WechatChannelsEmulatorScraper,
    "toutiao": ToutiaoScraper,
    "weibo": WeiboScraper,
}
```

### 6.2 CLI Changes

New flag: `--emulator`

```bash
# Run only WeChat platforms (requires emulator)
python main.py --run --platforms wechat,wechat_channels --emulator

# Run all platforms (emulator + browser)
python main.py --run --emulator
```

When `--emulator` is present:
- Appium-Python-Client dependency is checked
- ADB connection is established before scraping
- Emulator scrapers are included in the run

When `--emulator` is absent:
- Emulator scrapers are skipped with a log message
- Existing Playwright scrapers run as before

### 6.3 Output Format

Identical to existing platforms:
- Per-post Excel: `output/{platform}_{title}_{date}/comments.xlsx`
- Summary report: `output/scrape_report_{date}.xlsx`
- Sheet 1: Post metadata
- Sheet 2: Comments with classification tags
- Sheet 3: Content classification

---

## 7. Configuration (`config.py` additions)

```python
# === Emulator Configuration ===
EMULATOR_ADB_HOST = "127.0.0.1"
EMULATOR_ADB_PORT = 5555
APPIUM_SERVER_URL = "http://127.0.0.1:4723"
EMULATOR_DEVICE_NAME = "emulator-5554"
WECHAT_PACKAGE = "com.tencent.mm"
WECHAT_ACTIVITY = "com.tencent.mm.ui.LauncherUI"

# Emulator scraping delays
EMULATOR_MIN_DELAY = 2.0
EMULATOR_MAX_DELAY = 5.0

# Video capture
EMULATOR_SCREENRECORD_MAX_SECONDS = 180
EMULATOR_PROXY_HOST = "127.0.0.1"
EMULATOR_PROXY_PORT = 8080

# Platform patterns (updated)
PLATFORM_PATTERNS = {
    "douyin": ["iesdouyin.com", "douyin.com"],
    "xiaohongshu": ["xiaohongshu.com"],
    "wechat": ["mp.weixin.qq.com"],
    "wechat_channels": ["channels.weixin.qq.com"],
    "toutiao": ["toutiao.com"],
    "weibo": ["weibo.com"],
}
```

---

## 8. Error Handling

| Scenario | Detection | Recovery |
|----------|-----------|----------|
| ADB not installed | `adb version` fails | Error message with install instructions |
| Emulator not running | `adb devices` returns empty | Prompt to start LDPlayer |
| Appium Server not running | Session creation timeout | Error message: `npm i -g appium && appium` |
| WeChat not installed | Appium session fails with package error | Prompt to install WeChat in emulator |
| WeChat not logged in | Detection via UI state check | Prompt to log in manually in emulator |
| Article requires verification | Page content check | Screenshot, prompt user, retry after delay |
| Comment section not found | Element timeout | Return partial results, log warning |
| Video download fails | All priorities fail | Skip video, return comments only |
| Emulator crashes mid-scrape | ADB disconnect detected | Attempt reconnect, abort if fails |
| Rate limiting | HTTP 429 or UI throttle message | Exponential backoff, max 3 retries |

---

## 9. Prerequisites & Setup

### User Setup Steps

1. **Install LDPlayer** (雷电模拟器)
   - Download from official site
   - Create Android 9+ instance
   - Recommended resolution: 1080x1920

2. **Install WeChat in emulator**
   - Open Google Play or APK install
   - Log in manually (first time, requires phone verification)

3. **Install Appium Server**
   ```bash
   npm install -g appium
   appium driver install uiautomator2
   ```

4. **Start services**
   ```bash
   # Terminal 1: Appium Server
   appium

   # Terminal 2: Verify ADB
   adb devices

   # Terminal 3: Run scraper
   python main.py --run --platforms wechat,wechat_channels --emulator
   ```

### Dependencies

```
# requirements.txt additions
Appium-Python-Client>=3.0.0
```

ADB is bundled with LDPlayer (typically at `LDPlayer安装目录/adb.exe`). If not found, the scraper falls back to system `adb`.

---

## 10. Testing Strategy

### Unit Tests (mock Appium)

- `tests/test_appium_base.py` — Test ADB connection logic, session creation params, helper methods
- `tests/test_wechat_official.py` — Test comment parsing, post content extraction (mock Appium elements)
- `tests/test_wechat_channels_emulator.py` — Test comment extraction, video URL detection

### Integration Tests (real emulator)

- Marked with `@pytest.mark.emulator` (skipped in CI)
- Test full scrape flow with a known WeChat article URL
- Test video download with a known 视频号 URL

### Manual Testing

- Use Appium Inspector to discover element identifiers
- Test with real WeChat articles and 视频号 posts
- Verify output Excel format matches existing platforms

---

## 11. Migration Plan

1. Implement `AppiumBaseScraper` first, verify ADB + Appium connection works
2. Implement `WechatOfficialScraper`, test with a few articles
3. Implement `WechatChannelsEmulatorScraper`, test comment scraping
4. Add video download (intercept → screenrecord fallback)
5. Integrate into `main.py`, update config
6. Write tests
7. Update CLAUDE.md documentation
