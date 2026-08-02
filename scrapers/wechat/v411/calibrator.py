# scrapers/wechat/v411/calibrator.py
"""Interactive coordinate calibration for WeChat PC desktop scrapers (v4.1.11 single-window).

Usage:
    python main.py --wechat-calibrate

Walks the user through recording relative positions of UI elements
in the WeChat window.  Press F8 to record mouse position, F5 to accept
recommended position, F9 to skip.

Features:
  Layer 1 — Smart defaults + coordinate range validation
  Layer 2 — Transparent overlay with crosshair / rectangle preview
"""
import ctypes
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pyautogui

import config
from scrapers.wechat.v411.window_manager import WechatWindowManager, WindowRect
from scrapers.wechat.calibration import (
    CalibrationStep,
    STEPS_V411,
    validate_point,
    validate_region,
    CalibrationOverlay,
)

logger = logging.getLogger(__name__)

_CALIBRATION_CONFIG = config.BASE_DIR / "config.wechat_pc.json"
_CALIBRATED_PROFILE = "wechat_pc_calibrated"

# Virtual key codes
VK_F5 = 0x74      # Accept recommendation
VK_F8 = 0x77      # Record position
VK_F9 = 0x78      # Skip
VK_ESCAPE = 0x1B


def _wait_for_key(*vk_codes, prompt: str = "按 F8 记录位置，F9 跳过") -> int:
    """Wait for user to press one of the specified keys. Returns the VK code.

    Uses GetAsyncKeyState polling.  User can freely move the mouse
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


def _step_to_screen_point(
    step: CalibrationStep, rect: WindowRect,
) -> tuple[int, int] | None:
    """Convert a step's default ratios to screen coordinates.

    Returns (screen_x, screen_y) or None if the step has no defaults.
    """
    if step.default_x_ratio is None or step.default_y_ratio is None:
        return None
    x = rect.left + round(rect.width * step.default_x_ratio)
    y = rect.top + round(rect.height * step.default_y_ratio)
    return (x, y)


def _step_to_screen_region(
    step: CalibrationStep, rect: WindowRect,
) -> tuple[int, int, int, int] | None:
    """Convert a step's default region ratios to screen coordinates.

    Returns (left, top, width, height) or None if the step has no defaults.
    """
    if step.default_left_ratio is None or step.default_top_ratio is None:
        return None
    left = rect.left + round(rect.width * step.default_left_ratio)
    top = rect.top + round(rect.height * step.default_top_ratio)
    w = round(rect.width * (step.default_width_ratio or 0.4))
    h = round(rect.height * (step.default_height_ratio or 0.7))
    return (left, top, w, h)


class WechatCalibrator:
    """Interactive calibration tool for WeChat PC coordinates (v4.1.11).

    Records mouse positions relative to the WeChat window and saves
    them as a coordinate profile in config.wechat_pc.json.

    Controls:
      F5 — accept recommended position (when available)
      F8 — record current mouse position
      F9 — skip current step
      Esc — quit calibration
    """

    def __init__(self, use_overlay: bool = True):
        self._window_mgr = WechatWindowManager()
        self._rect: WindowRect | None = None
        self._data: dict = {
            "wechat_main": {},
            "official": {},
            "channels": {},
        }
        self._skipped: list[str] = []
        self._use_overlay = use_overlay
        self._overlay: CalibrationOverlay | None = None

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
        print("  3. 按 F5 使用推荐位置（黄色圆点指示）")
        print("  4. 按 F9 跳过当前步骤")
        print("  5. 按 Esc 退出校准")
        print()

        # Start overlay (if enabled)
        if self._use_overlay:
            self._overlay = CalibrationOverlay()
            if not self._overlay.start():
                print("  [提示] Overlay 创建失败，将使用纯文字模式。")
                self._overlay = None
            else:
                print("  [提示] 屏幕叠加层已启用（绿色十字线 + 黄色推荐点）")

        try:
            return self._run_steps()
        finally:
            if self._overlay:
                self._overlay.stop()

    def _run_steps(self) -> bool:
        """Internal: run all calibration steps."""
        # Step 0: Find WeChat window
        if not self._ensure_window():
            return False

        total = len(STEPS_V411)
        success_count = 0

        for i, step in enumerate(STEPS_V411, 1):
            section = step.section
            key = step.key
            step_type = step.type

            # Refresh window rect BEFORE each step — the window may have
            # resized (e.g. article view widens from 942→1386 px)
            if self._window_mgr.is_found:
                self._rect = self._window_mgr.get_rect()
                print(
                    f"\n[窗口] ({self._rect.left}, {self._rect.top}) "
                    f"{self._rect.width}x{self._rect.height}"
                )

            # Print step header
            print()
            print("=" * 60)
            print(f"  [{i}/{total}] {step.title}  ({'点击位置' if step_type == 'point' else '矩形区域'})")
            print("=" * 60)
            print(f"  {step.instructions}")

            if step_type == "point":
                result = self._record_point(i, total, step)
            else:
                result = self._record_region(i, total, step)

            if result == "quit":
                break
            elif result == "skip":
                self._skipped.append(f"{section}.{key}")
                continue
            elif result == "ok":
                success_count += 1

            # Refresh window rect in case it moved
            if self._window_mgr.is_found:
                self._rect = self._window_mgr.get_rect()

        # Hide overlay before save
        if self._overlay:
            self._overlay.hide()

        if success_count == 0:
            print("\n没有记录任何坐标，取消保存。")
            return False

        self._save()
        return True

    def _record_point(self, i: int, total: int, step: CalibrationStep) -> str:
        """Record a point calibration step. Returns 'ok', 'skip', or 'quit'."""
        rect = self._rect

        # Compute recommendation screen coords
        rec = _step_to_screen_point(step, rect)
        rec_x, rec_y = rec if rec else (None, None)

        # Track mouse for overlay
        mx, my = pyautogui.position()

        # Label for overlay
        label = f"[{i}/{total}] {step.title} | F8 记录 · "
        if rec:
            label += "F5 接受推荐 · "
        label += "F9 跳过"

        # Show overlay in point mode
        if self._overlay:
            self._overlay.show_point_mode(mx, my, rec_x, rec_y, label)

        # Wait for key — include F5 if recommendation is available
        if rec:
            vk_keys = (VK_F5, VK_F8, VK_F9, VK_ESCAPE)
            prompt = "移动鼠标 → F8 记录 | F5 使用推荐位置 | F9 跳过 | Esc 退出"
        else:
            vk_keys = (VK_F8, VK_F9, VK_ESCAPE)
            prompt = "移动鼠标 → F8 记录 | F9 跳过 | Esc 退出"

        vk = _wait_for_key(*vk_keys, prompt=prompt)

        if vk == VK_ESCAPE:
            print("  [退出] 用户取消\n")
            return "quit"
        if vk == VK_F9:
            print(f"  [跳过] {step.title}")
            return "skip"

        try:
            if vk == VK_F5 and rec:
                # Use recommended position
                abs_x, abs_y = rec
                print(f"  [推荐] 使用推荐坐标: 屏幕({abs_x}, {abs_y})")
            else:
                abs_x, abs_y = pyautogui.position()

            x_ratio = (abs_x - rect.left) / rect.width
            y_ratio = (abs_y - rect.top) / rect.height

            self._data[step.section][step.key] = {
                "x_ratio": round(x_ratio, 4),
                "y_ratio": round(y_ratio, 4),
            }
            print(
                f"  [OK] 屏幕({abs_x}, {abs_y}) → "
                f"相对({x_ratio:.4f}, {y_ratio:.4f})"
            )

            # ── Validate ──
            result = validate_point(x_ratio, y_ratio, step)
            if result.level != "ok":
                print(f"\n  {result.message}")

        except Exception as e:
            print(f"  [错误] {e}")
            return "skip"

        return "ok"

    def _record_region(self, i: int, total: int, step: CalibrationStep) -> str:
        """Record a region calibration step. Returns 'ok', 'skip', or 'quit'."""
        rect = self._rect

        # Compute recommendation screen region
        rec_reg = _step_to_screen_region(step, rect)

        # ── First corner ──
        mx, my = pyautogui.position()
        label = f"[{i}/{total}] {step.title} | 第1点: 左上角"

        if self._overlay:
            self._overlay.show_region_first(
                mx, my,
                rec_reg[0] if rec_reg else None,
                rec_reg[1] if rec_reg else None,
                rec_reg[2] if rec_reg else None,
                rec_reg[3] if rec_reg else None,
                label,
            )

        vk1 = _wait_for_key(
            VK_F8, VK_F9, VK_ESCAPE,
            prompt="移动鼠标到【左上角】→ 按 F8 记录 | F9 跳过",
        )

        if vk1 == VK_ESCAPE:
            print("  [退出] 用户取消\n")
            return "quit"
        if vk1 == VK_F9:
            print(f"  [跳过] {step.title}")
            return "skip"

        try:
            left_x, top_y = pyautogui.position()
            print(f"  左上角: 屏幕({left_x}, {top_y})")

            # ── Second corner ──
            label2 = (
                f"[{i}/{total}] {step.title} | 第2点: 右下角 "
                f"(框选区域 {abs(left_x)}..→ , {abs(top_y)}..↓)"
            )

            if self._overlay:
                self._overlay.show_region_second(
                    left_x, top_y,
                    mx, my,
                    rec_reg[0] if rec_reg else None,
                    rec_reg[1] if rec_reg else None,
                    rec_reg[2] if rec_reg else None,
                    rec_reg[3] if rec_reg else None,
                    label2,
                )

            vk2 = _wait_for_key(
                VK_F8, VK_ESCAPE,
                prompt="移动鼠标到【右下角】→ 按 F8 记录 | Esc 退出",
            )

            if vk2 == VK_ESCAPE:
                print("  [退出] 用户取消\n")
                return "quit"

            right_x, bottom_y = pyautogui.position()
            print(f"  右下角: 屏幕({right_x}, {bottom_y})")

            left_ratio = (left_x - rect.left) / rect.width
            top_ratio = (top_y - rect.top) / rect.height
            width_ratio = (right_x - left_x) / rect.width
            height_ratio = (bottom_y - top_y) / rect.height

            self._data[step.section][step.key] = {
                "left_ratio": round(left_ratio, 4),
                "top_ratio": round(top_ratio, 4),
                "width_ratio": round(width_ratio, 4),
                "height_ratio": round(height_ratio, 4),
            }
            print(
                f"  [OK] 区域: left={left_ratio:.4f} top={top_ratio:.4f} "
                f"w={width_ratio:.4f} h={height_ratio:.4f}"
            )

            # ── Validate ──
            result = validate_region(
                left_ratio, top_ratio, width_ratio, height_ratio, step,
            )
            if result.level != "ok":
                print(f"\n  {result.message}")

        except Exception as e:
            print(f"  [错误] {e}")
            return "skip"

        return "ok"

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
        """Write calibrated profile to config.wechat_pc.json.

        Merges with any existing calibrated profile so that running
        calibration for a single new step does not wipe out previously
        recorded coordinates.
        """
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

        # Merge with existing calibrated profile — keep old keys
        prev = profiles.get(_CALIBRATED_PROFILE, {})
        merged = {
            "wechat_main": {
                **prev.get("wechat_main", {}),
                **self._data.get("wechat_main", {}),
            },
            "official": {
                **prev.get("official", {}),
                **self._data.get("official", {}),
            },
            "channels": {
                **prev.get("channels", {}),
                **self._data.get("channels", {}),
            },
        }

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
            **merged,
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
