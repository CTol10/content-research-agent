# scrapers/wechat_calibrator.py
"""Interactive coordinate calibration for WeChat PC desktop scrapers.

Usage:
    python main.py --wechat-calibrate

Walks the user through recording relative positions of UI elements
in the WeChat window. Press F8 to record mouse position, F9 to skip.
Records are saved to config.wechat_pc.json.
"""
import ctypes
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pyautogui

import config
from scrapers.wechat_window_manager import WechatWindowManager, WindowRect

logger = logging.getLogger(__name__)

_CALIBRATION_CONFIG = config.BASE_DIR / "config.wechat_pc.json"
_CALIBRATED_PROFILE = "wechat_pc_calibrated"

# Virtual key codes
VK_F8 = 0x77
VK_F9 = 0x78
VK_ESCAPE = 0x1B


def _wait_for_key(*vk_codes, prompt: str = "按 F8 记录位置，F9 跳过") -> int:
    """Wait for user to press one of the specified keys. Returns the VK code.

    Uses GetAsyncKeyState polling. User can freely move the mouse
    before pressing the key.
    """
    print(f"\n  {prompt}")
    print("  " + "-" * 50)

    # Wait for key release first (debounce)
    time.sleep(0.3)
    # Clear any pending state
    for vk in vk_codes:
        ctypes.windll.user32.GetAsyncKeyState(vk)

    while True:
        for vk in vk_codes:
            state = ctypes.windll.user32.GetAsyncKeyState(vk)
            if state & 0x8000:  # Key is currently pressed
                # Wait for release to avoid double-trigger
                while ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
                    time.sleep(0.05)
                return vk
        time.sleep(0.05)


# ── Calibration step definitions ─────────────────────────────────

CALIBRATION_STEPS = [
    {
        "section": "wechat_main",
        "key": "search_bar",
        "type": "point",
        "title": "搜索栏",
        "instructions": (
            "请将鼠标移动到 微信主界面顶部的【搜索栏/搜索框】位置"
        ),
    },
    {
        "section": "wechat_main",
        "key": "result_open_entry",
        "type": "point",
        "title": "搜索结果入口（访问网页）",
        "instructions": (
            "请在微信搜索栏中搜索一个任意公众号链接 (如 mp.weixin.qq.com/s/xxx)，\n"
            "然后将鼠标移动到搜索结果中【访问网页】按钮的位置"
        ),
    },
    {
        "section": "official",
        "key": "article_scroll_start",
        "type": "point",
        "title": "公众号 - 文章滚动点",
        "instructions": (
            "请先在微信中打开一篇 公众号文章（搜索链接后点击访问网页），\n"
            "然后将鼠标移动到 文章评论区上方（用于滚动的锚点位置）"
        ),
    },
    {
        "section": "official",
        "key": "comment_icon",
        "type": "point",
        "title": "公众号 - 评论区图标",
        "instructions": (
            "在公众号文章页面，\n"
            "将鼠标移动到 文章底部的【评论区图标/按钮】（点击可展开评论区）"
        ),
    },
    {
        "section": "official",
        "key": "comment_region",
        "type": "region",
        "title": "公众号 - 评论区 + 文章正文区",
        "instructions": (
            "请在打开的公众号文章中，\n"
            "第一次按 F8: 记录区域的【左上角】（文章正文上沿到评论区底部）\n"
            "第二次按 F8: 记录区域的【右下角】"
        ),
    },
    {
        "section": "channels",
        "key": "pause_video",
        "type": "point",
        "title": "视频号 - 视频区域中心",
        "instructions": (
            "请先在微信中打开一个 视频号视频（搜索 channels.weixin.qq.com 链接后点击访问网页），\n"
            "然后将鼠标移动到 视频播放区域的中心位置（用于暂停视频）"
        ),
    },
    {
        "section": "channels",
        "key": "comment_button",
        "type": "point",
        "title": "视频号 - 评论按钮",
        "instructions": (
            "在视频号视频页面，\n"
            "将鼠标移动到 右侧或底部的【评论按钮】"
        ),
    },
    {
        "section": "channels",
        "key": "comment_panel_region",
        "type": "region",
        "title": "视频号 - 评论面板区域",
        "instructions": (
            "在视频号页面打开评论面板（点击评论按钮），\n"
            "第一次按 F8: 记录评论面板【左上角】\n"
            "第二次按 F8: 记录评论面板【右下角】"
        ),
    },
    {
        "section": "channels",
        "key": "scroll_anchor",
        "type": "point",
        "title": "视频号 - 评论面板滚动点",
        "instructions": (
            "在评论面板打开的状态下，\n"
            "将鼠标移动到 评论面板中间区域（用于滚动的锚点）"
        ),
    },
]


class WechatCalibrator:
    """Interactive calibration tool for WeChat PC coordinates.

    Records mouse positions relative to the WeChat window and saves
    them as a coordinate profile in config.wechat_pc.json.

    Controls:
      F8 — record current mouse position
      F9 — skip current step
      Esc — quit calibration
    """

    def __init__(self):
        self._window_mgr = WechatWindowManager()
        self._rect: WindowRect | None = None
        self._data: dict = {
            "wechat_main": {},
            "official": {},
            "channels": {},
        }
        self._skipped: list[str] = []

    # ── Public API ────────────────────────────────────────────────

    def calibrate_all(self) -> bool:
        """Run the full calibration workflow. Returns True on success."""
        print("\n" + "=" * 60)
        print("  微信 PC 坐标校准工具")
        print("=" * 60)
        print()
        print("此工具将帮助你校准微信客户端中各个 UI 元素的坐标位置。")
        print()
        print("操作方式：")
        print("  1. 阅读提示，将鼠标移动到目标位置")
        print("  2. 按 F8 记录当前鼠标位置")
        print("  3. 按 F9 跳过当前步骤")
        print("  4. 按 Esc 退出校准")
        print()

        # Step 0: Find WeChat window
        if not self._ensure_window():
            return False

        total = len(CALIBRATION_STEPS)
        success_count = 0

        for i, step in enumerate(CALIBRATION_STEPS, 1):
            section = step["section"]
            key = step["key"]
            step_type = step["type"]

            # Print step header
            print()
            print("=" * 60)
            print(f"  [{i}/{total}] {step['title']}  ({'点击位置' if step_type == 'point' else '矩形区域'})")
            print("=" * 60)
            print(f"  {step['instructions']}")

            if step_type == "point":
                vk = _wait_for_key(
                    VK_F8, VK_F9, VK_ESCAPE,
                    prompt="移动鼠标 → 按 F8 记录 | F9 跳过 | Esc 退出",
                )

                if vk == VK_ESCAPE:
                    print("  [退出] 用户取消\n")
                    break
                if vk == VK_F9:
                    print(f"  [跳过] {step['title']}")
                    self._skipped.append(f"{section}.{key}")
                    continue

                try:
                    abs_x, abs_y = pyautogui.position()
                    x_ratio = (abs_x - self._rect.left) / self._rect.width
                    y_ratio = (abs_y - self._rect.top) / self._rect.height
                    self._data[section][key] = {
                        "x_ratio": round(x_ratio, 4),
                        "y_ratio": round(y_ratio, 4),
                    }
                    print(
                        f"  [OK] 屏幕({abs_x}, {abs_y}) → "
                        f"相对({x_ratio:.4f}, {y_ratio:.4f})"
                    )
                    success_count += 1
                except Exception as e:
                    print(f"  [错误] {e}")
                    self._skipped.append(f"{section}.{key}")

            else:
                # Region: two F8 presses
                vk1 = _wait_for_key(
                    VK_F8, VK_F9, VK_ESCAPE,
                    prompt="移动鼠标到【左上角】→ 按 F8 记录 | F9 跳过",
                )

                if vk1 == VK_ESCAPE:
                    print("  [退出] 用户取消\n")
                    break
                if vk1 == VK_F9:
                    print(f"  [跳过] {step['title']}")
                    self._skipped.append(f"{section}.{key}")
                    continue

                try:
                    left_x, top_y = pyautogui.position()
                    print(f"  左上角: 屏幕({left_x}, {top_y})")

                    vk2 = _wait_for_key(
                        VK_F8, VK_ESCAPE,
                        prompt="移动鼠标到【右下角】→ 按 F8 记录 | Esc 退出",
                    )

                    if vk2 == VK_ESCAPE:
                        print("  [退出] 用户取消\n")
                        break

                    right_x, bottom_y = pyautogui.position()
                    print(f"  右下角: 屏幕({right_x}, {bottom_y})")

                    left_ratio = (left_x - self._rect.left) / self._rect.width
                    top_ratio = (top_y - self._rect.top) / self._rect.height
                    width_ratio = (right_x - left_x) / self._rect.width
                    height_ratio = (bottom_y - top_y) / self._rect.height

                    self._data[section][key] = {
                        "left_ratio": round(left_ratio, 4),
                        "top_ratio": round(top_ratio, 4),
                        "width_ratio": round(width_ratio, 4),
                        "height_ratio": round(height_ratio, 4),
                    }
                    print(
                        f"  [OK] 区域: left={left_ratio:.4f} top={top_ratio:.4f} "
                        f"w={width_ratio:.4f} h={height_ratio:.4f}"
                    )
                    success_count += 1
                except Exception as e:
                    print(f"  [错误] {e}")
                    self._skipped.append(f"{section}.{key}")

            # Refresh window rect in case it moved
            if self._window_mgr.is_found:
                self._rect = self._window_mgr.get_rect()

        # Save
        if success_count == 0:
            print("\n没有记录任何坐标，取消保存。")
            return False

        self._save()
        return True

    # ── Internal ──────────────────────────────────────────────────

    def _ensure_window(self) -> bool:
        """Find and prepare the WeChat window."""
        print("正在查找微信窗口...")
        if not self._window_mgr.find_window():
            print()
            print("  [错误] 找不到微信窗口！")
            print("  请确保微信 PC 客户端已启动并登录。")
            return False

        self._rect = self._window_mgr.get_rect()
        print(
            f"  找到微信窗口: ({self._rect.left}, {self._rect.top}) "
            f"{self._rect.width}x{self._rect.height}"
        )

        # Move to primary monitor
        self._window_mgr.move_to_primary_screen()
        self._window_mgr.activate()
        self._rect = self._window_mgr.get_rect()
        return True

    def _save(self) -> None:
        """Write calibrated profile to config.wechat_pc.json."""
        # Load existing config or create new
        existing = {}
        if _CALIBRATION_CONFIG.exists():
            try:
                existing = json.loads(
                    _CALIBRATION_CONFIG.read_text(encoding="utf-8")
                )
            except Exception:
                existing = {}

        profiles = existing.get("profiles", {})

        # Add/update calibrated profile
        profiles[_CALIBRATED_PROFILE] = {
            "meta": {
                "wechat_version": "calibrated",
                "dpi_scale": 1.0,
                "calibrated_at": datetime.now(timezone.utc).isoformat(),
                "window_rect": {
                    "left": self._rect.left,
                    "top": self._rect.top,
                    "width": self._rect.width,
                    "height": self._rect.height,
                },
            },
            **self._data,
        }

        output = {
            "active_profile": _CALIBRATED_PROFILE,
            "profiles": profiles,
        }

        _CALIBRATION_CONFIG.write_text(
            json.dumps(output, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n配置已保存到: {_CALIBRATION_CONFIG}")

        if self._skipped:
            print(f"跳过的步骤: {', '.join(self._skipped)}")
            print("你可以重新运行校准来补充这些坐标。")
