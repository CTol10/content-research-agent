# WeChat PC Scraper Design (Relative Coordinate Phase 1)

**Date**: 2026-06-08
**Status**: Draft
**Scope**: Windows desktop only; WeChat Official Account (公众号) comments + WeChat Channels (视频号) comments/video via PC WeChat, using configurable relative coordinates in phase 1, with upgrade paths to UIA/CDP.

---

## 1. Problem Statement

The current WeChat scraping work in this project is split across several approaches:

- `scrapers/wechat_official.py` uses Android emulator + Appium for 公众号.
- `scrapers/wechat_channels_emulator.py` uses Android emulator + Appium for 视频号.
- `scrapers/wechat_channels.py` and `wechat_channels_ui.py` use desktop WeChat + fixed absolute coordinates + OCR.
- `probe_wechat_ui.py`, `launch_wechat.py`, and `probe_wechat_cdp.py` show early exploration of desktop WeChat automation via UIA/CDP.

The emulator-based route is now high-risk because WeChat increasingly blocks or restricts usage in Android emulators. The user also requires a solution that runs on the local Windows PC only, without emulator or real phone.

Phase 1 therefore adopts a pragmatic PC-only automation approach:

- use desktop WeChat as the execution environment
- use **configurable relative coordinates** rather than hard-coded absolute screen coordinates
- use OCR as the primary extraction fallback
- keep the architecture ready for later upgrades to Windows UI Automation and CDP

This is not the ideal end-state architecture, but it is the fastest realistic local-only path that can be delivered incrementally.

---

## 2. Goals and Non-Goals

### 2.1 Goals

- Run on **Windows local machine only**
- Support `微信公众号` article comments
- Support `微信视频号` comments
- Preserve optional `video capture` capability for 视频号
- Replace fixed absolute coordinates with **window-relative coordinate configuration**
- Keep the result contract compatible with the current pipeline:
  - `{"post_content": str, "comments": [...]}` for text content
  - optional `video_url` for 视频号
- Build a phase-based foundation that can later migrate toward:
  - UIA element discovery
  - CDP page/DOM/network extraction

### 2.2 Non-Goals

- No Android emulator support in this phase
- No real-device support in this phase
- No promise of zero-maintenance across all WeChat versions
- No attempt to fully reverse-engineer all WeChat internal APIs in phase 1
- No cross-platform desktop support outside Windows

---

## 3. Constraints

### 3.1 User Constraints

- Must be fully automated as much as possible
- Must run only on the local PC
- Cannot depend on an Android phone
- Cannot depend on an Android emulator

### 3.2 Technical Constraints

- Desktop WeChat UI may change between versions
- Some content surfaces may be hybrid/native and not fully available via DOM
- OCR quality depends on screen scale, font rendering, and panel layout
- Multi-monitor and Windows DPI scaling can distort absolute coordinates

### 3.3 Design Implication

Because of these constraints, absolute screen coordinates are too fragile as a primary mechanism. Phase 1 will use:

- **window detection**
- **window-relative coordinates**
- **resolution/scaling-specific config profiles**
- **validation after click**
- **fallbacks when a target action does not succeed**

---

## 4. Recommended Approach

### 4.1 Recommendation

Adopt a **PC WeChat controller with configurable relative coordinates** for phase 1, then layer in UIA/CDP later without breaking the scraper interface.

### 4.2 Why This Approach

- Lowest implementation cost under the local-only constraint
- Most compatible with existing OCR-based desktop exploration
- More stable than fixed absolute coordinates
- Easier to calibrate per machine than full UIA/CDP reverse-engineering
- Allows later replacement of click/locate internals while keeping scraper behavior and output stable

### 4.3 Trade-Offs

- Still less stable than a true element-driven automation system
- Requires config calibration for different window sizes/scales
- May need periodic coordinate updates after WeChat desktop UI changes
- OCR remains a weak point for dense comment layouts

---

## 5. Architecture

### 5.1 Overview

Phase 1 architecture:

```text
main.py
  -> WechatPcBaseScraper
       -> WindowManager
       -> RelativeLayoutProfile
       -> InputController
       -> ScreenCapture
       -> OcrExtractor
       -> ActionValidator
  -> WechatOfficialPcScraper
  -> WechatChannelsPcScraper
```

Future architecture extension:

```text
WechatPcBaseScraper
  -> RelativeCoordinateLocator   (phase 1 primary)
  -> UiaLocator                  (phase 2 secondary/upgrade)
  -> CdpLocator                  (phase 3 secondary/upgrade)
  -> OcrFallback                 (always available)
```

### 5.2 Core Principles

- Use **relative coordinates against the active WeChat window**, not against the full screen
- Separate **layout config** from scraper logic
- Treat OCR as a reusable extraction module
- Always verify that a click/navigation succeeded before continuing
- Save diagnostics on every failure path

---

## 6. Relative Coordinate Design

### 6.1 Why Relative Coordinates

Current code uses absolute values like:

- search bar `(140, 60)`
- comment button `(585, 899)`
- comment panel region `(350, 0, 350, 700)`

These values break when:

- the WeChat window moves
- the window size changes
- DPI scale changes
- a second monitor changes coordinate origin assumptions

Relative coordinates fix the first two problems directly and reduce the impact of the others when used with layout profiles.

### 6.2 Coordinate Model

Each clickable point or region is defined relative to the target window:

```json
{
  "search_bar": { "x_ratio": 0.14, "y_ratio": 0.06 },
  "visit_webpage": { "x_ratio": 0.20, "y_ratio": 0.22 },
  "comment_button": { "x_ratio": 0.84, "y_ratio": 0.76 },
  "comment_panel_region": {
    "left_ratio": 0.50,
    "top_ratio": 0.00,
    "width_ratio": 0.45,
    "height_ratio": 0.78
  }
}
```

Resolved runtime coordinates:

```text
abs_x = window.left + round(window.width * x_ratio)
abs_y = window.top + round(window.height * y_ratio)
```

Resolved runtime region:

```text
left   = window.left + round(window.width  * left_ratio)
top    = window.top  + round(window.height * top_ratio)
width  = round(window.width  * width_ratio)
height = round(window.height * height_ratio)
```

### 6.3 Layout Profiles

Coordinate config must be grouped by a layout profile rather than a single global set of values.

Suggested profile keys:

- `wechat_pc_default_100`
- `wechat_pc_default_125`
- `wechat_pc_default_150`
- `wechat_pc_compact_100`

Each profile corresponds to:

- WeChat desktop version range
- Windows DPI scale
- expected window size or minimum window size
- coordinate presets for 公众号 / 视频号

### 6.4 Validation Rules

After any coordinate-based action, the script must validate success using one of:

- screenshot keyword OCR
- expected window title change
- expected panel region appearing
- expected number of OCR lines increasing

If validation fails:

1. retry current action with alternate coordinates
2. retry after activating/refocusing the window
3. save diagnostic screenshot
4. abort current item with structured error

---

## 7. Components

### 7.1 `WechatPcBaseScraper`

Responsibilities:

- locate and activate WeChat main window
- load the correct layout profile
- provide helpers for relative click, scroll, paste, screenshot
- unify diagnostics and retries

Proposed interface:

```python
class WechatPcBaseScraper:
    platform_name: str = "wechat_pc_base"

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def scrape(self, url: str) -> dict: ...

    def activate_wechat_window(self) -> None: ...
    def resolve_point(self, key: str) -> tuple[int, int]: ...
    def resolve_region(self, key: str) -> tuple[int, int, int, int]: ...
    def click_key(self, key: str) -> None: ...
    def screenshot_region(self, key: str, name: str) -> str: ...
    def validate_state(self, expected: str) -> bool: ...
```

### 7.2 `WindowManager`

Responsibilities:

- discover WeChat main window
- get window rectangle
- bring window to foreground
- optionally normalize window size

Phase 1 implementation can use `pywinauto.Desktop(backend="uia")` only for window discovery and activation, not full control-tree automation.

### 7.3 `RelativeLayoutProfile`

Responsibilities:

- load layout config from file
- choose profile based on machine settings
- expose named points and regions

### 7.4 `InputController`

Responsibilities:

- click resolved points
- paste URL into search box
- send keyboard shortcuts
- scroll comment panel

Implementation can continue to use `pyautogui` in phase 1.

### 7.5 `OcrExtractor`

Responsibilities:

- screenshot a region
- run OCR
- parse comments from OCR lines
- deduplicate results across scroll batches

Existing parsing logic from `scrapers/wechat_channels.py` can be extracted and generalized here.

### 7.6 `ActionValidator`

Responsibilities:

- verify that navigation succeeded
- verify comment panel opened
- verify scroll progressed
- verify comment extraction is not stuck

---

## 8. Platform Flows

### 8.1 WeChat Official Account (`WechatOfficialPcScraper`)

Phase 1 flow:

```text
1. Activate WeChat main window
2. Click search bar (relative point)
3. Clear previous content
4. Paste article URL
5. Press Enter
6. Wait for search result / page open
7. Click "访问网页" or equivalent configured entry point
8. Wait for article content window/panel
9. Scroll to comment section
10. Capture content region / comment region
11. OCR parse article text and comments
12. Continue scrolling until no new comments
13. Return {"post_content": ..., "comments": ...}
```

Phase 1 extraction priority:

1. OCR from article/comment region
2. optional keyword heuristics from visible window text

Future upgrade path:

1. CDP DOM content extraction
2. UIA comment-region detection
3. OCR fallback

### 8.2 WeChat Channels (`WechatChannelsPcScraper`)

Phase 1 flow:

```text
1. Activate WeChat main window
2. Click search bar
3. Paste 视频号 URL
4. Press Enter
5. Click "访问网页" or configured content entry
6. Wait for video/content window
7. Optionally pause video
8. Click comment button using relative coordinates
9. Validate comment panel opened
10. OCR the comment panel region
11. Scroll the panel region
12. Expand replies if configured
13. Continue until no new comments
14. Optionally start/stop window recording
15. Return {"post_content": "", "comments": ..., "video_url": ...}
```

Phase 1 extraction priority:

1. OCR comment panel
2. keyword-based validation of panel state

Future upgrade path:

1. CDP network capture of comment/media requests
2. UIA controls for panel/button detection
3. OCR fallback

---

## 9. Configuration Design

### 9.1 New Config File

Add a dedicated config file, for example:

- `config.wechat_pc.json`

Suggested structure:

```json
{
  "active_profile": "wechat_pc_default_125",
  "profiles": {
    "wechat_pc_default_125": {
      "meta": {
        "wechat_version": "4.x",
        "dpi_scale": 1.25,
        "window_min_width": 900,
        "window_min_height": 800
      },
      "wechat_main": {
        "search_bar": { "x_ratio": 0.14, "y_ratio": 0.06 },
        "result_open_entry": { "x_ratio": 0.21, "y_ratio": 0.22 }
      },
      "official": {
        "article_scroll_start": { "x_ratio": 0.50, "y_ratio": 0.72 },
        "article_scroll_end": { "x_ratio": 0.50, "y_ratio": 0.32 },
        "comment_region": {
          "left_ratio": 0.48,
          "top_ratio": 0.20,
          "width_ratio": 0.47,
          "height_ratio": 0.70
        }
      },
      "channels": {
        "pause_video": { "x_ratio": 0.43, "y_ratio": 0.42 },
        "comment_button": { "x_ratio": 0.84, "y_ratio": 0.76 },
        "comment_panel_region": {
          "left_ratio": 0.50,
          "top_ratio": 0.00,
          "width_ratio": 0.45,
          "height_ratio": 0.78
        },
        "scroll_anchor": { "x_ratio": 0.72, "y_ratio": 0.45 }
      }
    }
  }
}
```

### 9.2 Config Principles

- no absolute screen coordinates in config
- all points are window-relative
- all regions are window-relative
- platform-specific settings are separated
- profile-level metadata allows future auto-selection

### 9.3 Alternate Coordinates

Some actions should support multiple candidate points:

```json
{
  "comment_button_candidates": [
    { "x_ratio": 0.84, "y_ratio": 0.76 },
    { "x_ratio": 0.88, "y_ratio": 0.74 }
  ]
}
```

This improves resilience after minor UI layout shifts.

---

## 10. File Plan

### 10.1 New Files

| File | Purpose |
|------|---------|
| `scrapers/wechat_pc_base.py` | Base class for desktop WeChat automation |
| `scrapers/wechat_official_pc.py` | 公众号 PC scraper |
| `scrapers/wechat_channels_pc.py` | 视频号 PC scraper |
| `scrapers/wechat_ocr_fallback.py` | OCR extraction and parsing helpers |
| `scrapers/wechat_window_manager.py` | Window discovery and activation |
| `config.wechat_pc.json` | Relative coordinate layout profiles |
| `tests/test_wechat_pc_layout.py` | Config and relative-coordinate resolution tests |
| `tests/test_wechat_ocr_fallback.py` | OCR parsing tests |

### 10.2 Modified Files

| File | Changes |
|------|---------|
| `main.py` | add `--wechat-mode pc` and register PC scrapers |
| `config.py` | add PC WeChat config path/constants |
| `scrapers/__init__.py` | route WeChat platforms to selected implementation mode |

### 10.3 Legacy Files

| File | Action |
|------|--------|
| `scrapers/wechat_channels.py` | keep temporarily; mine parsing logic and then deprecate |
| `wechat_channels_ui.py` | keep as manual diagnostic script |
| `probe_wechat_ui.py` | keep as diagnostic tool |
| `probe_wechat_cdp.py` | keep as future upgrade/diagnostic tool |
| `launch_wechat.py` | keep for future PC-CDP integration |

---

## 11. CLI and Mode Design

### 11.1 New CLI

Current `--emulator` flag is too narrow. Introduce:

```bash
python main.py --run --wechat-mode pc
python main.py --run --platforms wechat,wechat_channels --wechat-mode pc
```

Suggested accepted values:

- `browser`
- `emulator`
- `pc`
- `auto`

Behavior:

- `browser`: use existing browser-based attempts where applicable
- `emulator`: use Appium emulator scrapers
- `pc`: use desktop WeChat relative-coordinate scrapers
- `auto`: prefer `pc` for WeChat on Windows local desktop

### 11.2 Selection Rules

- `wechat` + `wechat-mode=pc` -> `WechatOfficialPcScraper`
- `wechat_channels` + `wechat-mode=pc` -> `WechatChannelsPcScraper`
- if mode unsupported, print an explicit skip reason

---

## 12. Failure Handling and Diagnostics

### 12.1 Failure Scenarios

- WeChat window not found
- window found but wrong size or minimized
- search/open action fails
- comment panel does not open
- OCR returns noisy or empty text
- repeated scrolls produce no new comments
- a popup or modal obscures the target area

### 12.2 Required Diagnostics

On every hard failure save:

- active window rectangle
- full window screenshot
- target region screenshot
- current profile name
- current action name
- OCR raw lines

Suggested output directory:

- `output/debug/wechat_pc/`

### 12.3 Retry Strategy

For click actions:

1. re-activate WeChat window
2. retry with same coordinate
3. retry with alternate candidate coordinate
4. abort current item

For OCR extraction:

1. retry screenshot
2. slightly expand capture region if configured
3. continue with partial result if enough comments were already collected

---

## 13. Testing Strategy

### 13.1 Unit Tests

- relative coordinate resolution
- region resolution from window rect
- profile loading and validation
- OCR comment parsing
- comment deduplication

### 13.2 Manual Integration Tests

Test matrix:

- Windows scale: `100%`, `125%`, `150%`
- WeChat window sizes: normal, maximized
- Content types:
  - 公众号 article with comments
  - 公众号 article with few/no comments
  - 视频号 with dense comments
  - 视频号 with replies

### 13.3 Acceptance Criteria

Phase 1 is acceptable when:

- local PC can run without emulator
- script can open target links in desktop WeChat
- script can extract at least visible comments reliably on a calibrated machine
- comment extraction is repeatable across multiple runs with the same profile
- diagnostics are sufficient to recalibrate a broken profile

---

## 14. Phase Plan

### Phase 1: Relative Coordinate Foundation

Goal:

- replace fixed absolute coordinates with configurable relative coordinates
- build a usable local-PC scraping path quickly

Tasks:

1. create window manager and layout profile loader
2. implement point/region resolution from active WeChat window
3. refactor old OCR-based 视频号 code into reusable base modules
4. add PC-specific 公众号 and 视频号 scrapers
5. save diagnostics and add validation/retry hooks

Deliverable:

- calibrated PC-only scraper on a target Windows machine

### Phase 2: UIA Upgrade

Goal:

- reduce dependence on coordinates for discovery and validation

Tasks:

1. use UIA to discover WeChat window and important child regions
2. replace some configured click points with anchor-driven offsets
3. validate panel state via control tree instead of OCR only

Deliverable:

- hybrid UIA + relative-coordinate scraper

### Phase 3: CDP Upgrade

Goal:

- improve extraction quality for article content, comments, and media URLs

Tasks:

1. launch or attach to WeChat with remote debugging
2. enumerate internal pages/targets
3. capture DOM/network for 公众号 and 视频号
4. use OCR only as fallback

Deliverable:

- CDP-enhanced desktop WeChat scraper with richer diagnostics and better media handling

---

## 15. Risks

### 15.1 Main Risks

- WeChat desktop UI changes can invalidate layout profiles
- OCR can miss or merge adjacent comments
- video comment panels may have animations or overlays that reduce OCR quality
- some page types may open in windows whose structure differs from the calibrated profile

### 15.2 Mitigations

- keep profile config external and editable
- support alternate points and regions
- always save screenshots on failure
- add profile-specific calibration script later
- preserve UIA/CDP upgrade path from the beginning

---

## 16. Recommendation

Proceed with a **phase 1 PC-only implementation based on configurable relative coordinates**, not fixed absolute coordinates. Treat this as a delivery-focused bridge solution:

- practical enough to ship on a local Windows machine
- structured enough to avoid locking the project into brittle hard-coded clicks
- compatible with a later migration toward UIA/CDP-based automation

This is the recommended path under the current constraints:

- local PC only
- no emulator
- no real phone
- automation required

---

## 17. Next Step

After this spec is approved, the next step should be a concrete implementation plan covering:

- exact file-by-file edits
- config schema and sample values
- CLI changes
- testing order
- calibration workflow for the first machine profile
