# scrapers/wechat/calibration/models.py
"""Calibration step definitions with smart defaults and validation ranges.

The default ratios are derived from empirical measurements across DPI
100%, 125%, and 150% profiles, plus real calibrated data from v411
(single-window, window ~1474x920) and v417 (dual-window, popup ~930x780).

Key insight: DPI scaling does NOT change window-relative ratios, so one
set of defaults works across all DPI settings.  Layout differences
between v411 (content as right-side panel) and v417 (content as separate
Chrome_WidgetWin_0 popup) DO affect ratios, hence separate step lists.

Validation ranges are deliberately wide (≈ ±15–20% of expected) to avoid
false alarms while still catching gross mis-clicks.
"""

from dataclasses import dataclass, field


@dataclass
class CalibrationStep:
    """A single calibration step (point or region recording)."""

    section: str          # "wechat_main" | "official" | "channels"
    key: str              # "search_bar", "comment_icon", etc.
    type: str             # "point" | "region"
    title: str            # Chinese display name
    instructions: str     # Text instructions (fallback when overlay unavailable)

    # ── Smart defaults (Layer 1) ──
    # For "point" steps:
    default_x_ratio: float | None = None
    default_y_ratio: float | None = None
    # For "region" steps:
    default_left_ratio: float | None = None
    default_top_ratio: float | None = None
    default_width_ratio: float | None = None
    default_height_ratio: float | None = None

    # ── Validation ranges (Layer 1) ──
    # (min, max) tuples.  None means "no validation for this axis".
    valid_x_range: tuple[float, float] | None = None
    valid_y_range: tuple[float, float] | None = None
    valid_w_range: tuple[float, float] | None = None   # width_ratio
    valid_h_range: tuple[float, float] | None = None   # height_ratio

    # ── Visual hint (Layer 2) ──
    hint_annotation: str = ""   # short Chinese description for overlay label


# ═════════════════════════════════════════════════════════════════════
#  Step definitions for v411 (single-window, content as right panel)
# ═════════════════════════════════════════════════════════════════════

STEPS_V411: list[CalibrationStep] = [
    CalibrationStep(
        section="wechat_main",
        key="search_bar",
        type="point",
        title="搜索栏",
        instructions="请将鼠标移动到 微信主界面顶部的【搜索栏/搜索框】位置",
        default_x_ratio=0.15,
        default_y_ratio=0.06,
        valid_x_range=(0.08, 0.30),
        valid_y_range=(0.02, 0.12),
        hint_annotation="主窗口顶部搜索框",
    ),
    CalibrationStep(
        section="wechat_main",
        key="result_open_entry",
        type="point",
        title="搜索结果入口（访问网页）",
        instructions=(
            "请在微信搜索栏中搜索一个任意公众号链接 (如 mp.weixin.qq.com/s/xxx)，\n"
            "然后将鼠标移动到搜索结果中【访问网页】按钮的位置"
        ),
        default_x_ratio=0.21,
        default_y_ratio=0.24,
        valid_x_range=(0.12, 0.35),
        valid_y_range=(0.14, 0.35),
        hint_annotation="搜索结果中「访问网页」按钮",
    ),
    # ── 公众号 评论区 ──
    CalibrationStep(
        section="official",
        key="comment_icon",
        type="point",
        title="公众号 - 评论区图标",
        instructions=(
            "在公众号文章页面，\n"
            "将鼠标移动到 文章底部的【评论区图标/按钮】（点击可展开评论区）"
        ),
        default_x_ratio=0.50,
        default_y_ratio=0.94,
        valid_x_range=(0.40, 0.60),
        valid_y_range=(0.85, 0.98),
        hint_annotation="文章底部评论区图标（推荐: 居中偏下）",
    ),
    CalibrationStep(
        section="official",
        key="comment_region",
        type="region",
        title="公众号 - 评论区 + 文章正文区",
        instructions=(
            "请在打开的公众号文章中，\n"
            "第一次按 F8: 记录区域的【左上角】（文章正文上沿到评论区底部）\n"
            "第二次按 F8: 记录区域的【右下角】"
        ),
        default_left_ratio=0.50,
        default_top_ratio=0.12,
        default_width_ratio=0.46,
        default_height_ratio=0.80,
        valid_w_range=(0.25, 0.58),
        valid_h_range=(0.65, 0.90),
        hint_annotation="文章正文+评论区矩形（推荐: 右半边面板）",
    ),
    # ── 视频号 评论区 ──
    CalibrationStep(
        section="channels",
        key="pause_video",
        type="point",
        title="视频号 - 视频区域中心",
        instructions=(
            "请先在微信中打开一个 视频号视频（搜索 channels.weixin.qq.com 链接后点击访问网页），\n"
            "然后将鼠标移动到 视频播放区域的中心位置（用于暂停视频）"
        ),
        default_x_ratio=0.46,
        default_y_ratio=0.44,
        valid_x_range=(0.35, 0.55),
        valid_y_range=(0.30, 0.58),
        hint_annotation="视频播放区域中心",
    ),
    CalibrationStep(
        section="channels",
        key="comment_button",
        type="point",
        title="视频号 - 评论按钮",
        instructions=(
            "在视频号视频页面，\n"
            "将鼠标移动到 右侧或底部的【评论按钮】"
        ),
        default_x_ratio=0.90,
        default_y_ratio=0.93,
        valid_x_range=(0.80, 0.96),
        valid_y_range=(0.85, 0.97),
        hint_annotation="视频页右下角评论按钮（推荐: 近右下角）",
    ),
    CalibrationStep(
        section="channels",
        key="comment_panel_region",
        type="region",
        title="视频号 - 评论面板区域",
        instructions=(
            "在视频号页面打开评论面板（点击评论按钮），\n"
            "第一次按 F8: 记录评论面板【左上角】\n"
            "第二次按 F8: 记录评论面板【右下角】"
        ),
        default_left_ratio=0.52,
        default_top_ratio=0.04,
        default_width_ratio=0.44,
        default_height_ratio=0.82,
        valid_w_range=(0.20, 0.52),
        valid_h_range=(0.70, 0.90),
        hint_annotation="评论面板矩形（推荐: 右半侧滑动面板）",
    ),
    CalibrationStep(
        section="channels",
        key="video_area",
        type="region",
        title="视频号 - 视频播放区域",
        instructions=(
            "在视频号页面（评论面板关闭状态），\n"
            "第一次按 F8: 记录视频播放区域【左上角】\n"
            "第二次按 F8: 记录视频播放区域【右下角】\n"
            "（用于录屏提取口播，请尽量框住完整的视频画面）"
        ),
        default_left_ratio=0.04,
        default_top_ratio=0.06,
        default_width_ratio=0.92,
        default_height_ratio=0.80,
        valid_w_range=(0.60, 0.98),
        valid_h_range=(0.50, 0.90),
        hint_annotation="视频画面矩形（推荐: 完整视频播放区）",
    ),
]


# ═════════════════════════════════════════════════════════════════════
#  Step definitions for v417 (dual-window, content as popup)
# ═════════════════════════════════════════════════════════════════════

STEPS_V417: list[CalibrationStep] = [
    CalibrationStep(
        section="wechat_main",
        key="search_bar",
        type="point",
        title="搜索栏（主窗口）",
        instructions="在主窗口中，将鼠标移动到顶部的【搜索栏/搜索框】位置",
        default_x_ratio=0.22,
        default_y_ratio=0.05,
        valid_x_range=(0.10, 0.35),
        valid_y_range=(0.02, 0.12),
        hint_annotation="主窗口顶部搜索框",
    ),
    CalibrationStep(
        section="official",
        key="result_open_entry",
        type="point",
        title="搜索结果 — 访问网页（弹窗）",
        instructions=(
            "在微信主窗口搜索栏搜索一个任意公众号/视频号链接，\n"
            "搜索后会出现搜索结果弹窗，\n"
            "将鼠标移动到弹窗中【访问网页】按钮的位置"
        ),
        default_x_ratio=0.88,
        default_y_ratio=0.16,
        valid_x_range=(0.70, 0.95),
        valid_y_range=(0.08, 0.30),
        hint_annotation="搜索结果弹窗中「访问网页」按钮",
    ),
    # ── 公众号 评论区（弹窗内） ──
    CalibrationStep(
        section="official",
        key="comment_icon",
        type="point",
        title="公众号 - 评论区图标（弹窗）",
        instructions=(
            "请在弹窗中打开一个公众号文章，\n"
            "将鼠标移动到文章底部的【评论区图标/按钮】"
        ),
        default_x_ratio=0.88,
        default_y_ratio=0.95,
        valid_x_range=(0.78, 0.95),
        valid_y_range=(0.88, 0.98),
        hint_annotation="弹窗中文章底部评论区图标",
    ),
    CalibrationStep(
        section="official",
        key="comment_region",
        type="region",
        title="公众号 - 评论区 + 正文区（弹窗）",
        instructions=(
            "在弹窗中的公众号文章里，\n"
            "第一次按 F8: 记录区域【左上角】\n"
            "第二次按 F8: 记录区域【右下角】"
        ),
        default_left_ratio=0.30,
        default_top_ratio=0.10,
        default_width_ratio=0.68,
        default_height_ratio=0.80,
        valid_w_range=(0.45, 0.80),
        valid_h_range=(0.65, 0.90),
        hint_annotation="弹窗中文章正文+评论区矩形",
    ),
    # ── 视频号 评论区（弹窗内） ──
    CalibrationStep(
        section="channels",
        key="pause_video",
        type="point",
        title="视频号 - 视频区域中心（弹窗）",
        instructions=(
            "在弹窗中打开一个视频号视频，\n"
            "将鼠标移动到视频播放区域的中心位置（用于暂停视频）"
        ),
        default_x_ratio=0.50,
        default_y_ratio=0.44,
        valid_x_range=(0.35, 0.60),
        valid_y_range=(0.30, 0.58),
        hint_annotation="弹窗中视频播放区域中心",
    ),
    CalibrationStep(
        section="channels",
        key="comment_button",
        type="point",
        title="视频号 - 评论按钮（弹窗）",
        instructions=(
            "在弹窗的视频号页面，\n"
            "将鼠标移动到右侧或底部的【评论按钮】"
        ),
        default_x_ratio=0.90,
        default_y_ratio=0.93,
        valid_x_range=(0.82, 0.96),
        valid_y_range=(0.87, 0.97),
        hint_annotation="弹窗中视频页评论按钮（推荐: 近右下角）",
    ),
    CalibrationStep(
        section="channels",
        key="comment_panel_region",
        type="region",
        title="视频号 - 评论面板区域（弹窗）",
        instructions=(
            "在弹窗中打开评论面板，\n"
            "第一次按 F8: 记录评论面板【左上角】\n"
            "第二次按 F8: 记录评论面板【右下角】"
        ),
        default_left_ratio=0.40,
        default_top_ratio=0.12,
        default_width_ratio=0.58,
        default_height_ratio=0.80,
        valid_w_range=(0.30, 0.65),
        valid_h_range=(0.70, 0.88),
        hint_annotation="弹窗中评论面板矩形",
    ),
]
