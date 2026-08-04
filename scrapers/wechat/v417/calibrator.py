# scrapers/wechat/v417/calibrator.py
"""Interactive coordinate calibration for WeChat 4.1.7 (dual-window).

Usage:
    python -c "from scrapers.wechat.v417.calibrator import WechatCalibratorV417; WechatCalibratorV417().calibrate_all()"

Walks the user through recording relative positions of UI elements
for BOTH the main Qt window and the Chrome_WidgetWin_0 popup.

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
from scrapers.wechat.v417.window_manager import WechatWindowManagerV417
from scrapers.wechat.calibration import (
    CalibrationStep,
    STEPS_V417,
    validate_point,
    validate_region,
    CalibrationOverlay,
)

logger = logging.getLogger(__name__)

_CALIBRATION_CONFIG = config.BASE_DIR / "config.wechat_pc_417.json"

VK_F5 = 0x74
VK_F8 = 0x77
VK_F9 = 0x78
VK_ESCAPE = 0x1B


def _wait_for_key(*vk_codes, prompt: str = "按 F8 记录位置，F9 跳过") -> int:
    """Wait for user to press one of the specified keys."""
    print(f"\n  {prompt}")
    print("  " + "-" * 50)
    # Clear all pending key states thoroughly
    time.sleep(0.5)
    for _ in range(5):
        for vk in vk_codes:
            ctypes.windll.user32.GetAsyncKeyState(vk)
        time.sleep(0.05)
    while True:
        for vk in vk_codes:
            state = ctypes.windll.user32.GetAsyncKeyState(vk)
            if state & 0x8000:
                # Wait for key release
                while ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
                    time.sleep(0.05)
                return vk
        time.sleep(0.05)


def _step_to_screen_point(
    step: CalibrationStep, rect,
) -> tuple[int, int] | None:
    """Convert a step's default ratios to screen coordinates."""
    if step.default_x_ratio is None or step.default_y_ratio is None:
        return None
    x = rect.left + round(rect.width * step.default_x_ratio)
    y = rect.top + round(rect.height * step.default_y_ratio)
    return (x, y)


def _step_to_screen_region(
    step: CalibrationStep, rect,
) -> tuple[int, int, int, int] | None:
    """Convert a step's default region ratios to screen coordinates."""
    if step.default_left_ratio is None or step.default_top_ratio is None:
        return None
    left = rect.left + round(rect.width * step.default_left_ratio)
    top = rect.top + round(rect.height * step.default_top_ratio)
    w = round(rect.width * (step.default_width_ratio or 0.4))
    h = round(rect.height * (step.default_height_ratio or 0.7))
    return (left, top, w, h)


class WechatCalibratorV417:
    """Interactive calibration for WeChat 4.1.7 dual-window layout."""

    def __init__(self, use_overlay: bool = True):
        self._window_mgr = WechatWindowManagerV417()
        self._data: dict = {
            "wechat_main": {},
            "official": {},
            "channels": {},
        }
        self._skipped: list[str] = []
        self._use_overlay = use_overlay
        self._overlay: CalibrationOverlay | None = None

    def calibrate_all(self) -> bool:
        print("\n" + "=" * 60)
        print("  微信 4.1.7 双窗口坐标校准工具")
        print("=" * 60)
        print()
        print("此工具将校准微信 4.1.7 的 UI 元素坐标。")
        print()
        print("操作方式：")
        print("  F5 — 使用推荐位置（黄色圆点指示）")
        print("  F8 — 记录当前鼠标位置")
        print("  F9 — 跳过当前步骤")
        print("  Esc — 退出校准")
        print()
        print("注意：wechat_main 坐标基于主 Qt 窗口；")
        print("      official/channels 坐标基于弹窗（Chrome_WidgetWin_0）。")
        print()

        # Start overlay
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
        # Find main window
        if not self._ensure_main_window():
            return False

        total = len(STEPS_V417)
        success_count = 0

        for i, step in enumerate(STEPS_V417, 1):
            section = step.section
            key = step.key
            step_type = step.type

            # Determine which rect to use
            if section == "wechat_main":
                rect = self._window_mgr.get_main_rect()
                print(
                    f"\n[主窗口] ({rect.left}, {rect.top}) "
                    f"{rect.width}x{rect.height}"
                )
            else:
                # Ensure popup is open
                print()
                print(f"  [提示] 此步骤需要弹窗（Chrome_WidgetWin_0）已打开。")
                print(f"  请确保公众号/视频号文章/视频已在弹窗中显示。")
                vk = _wait_for_key(VK_F8, VK_ESCAPE, prompt="准备好后按 F8 继续 | Esc 退出")
                if vk == VK_ESCAPE:
                    print("  [退出] 用户取消\n")
                    break

                if not self._window_mgr.wait_for_popup(timeout=2):
                    print("  [错误] 未检测到弹窗！请在微信中打开一个文章或视频后重试。")
                    self._skipped.append(f"{section}.{key}")
                    continue
                rect = self._window_mgr.get_popup_rect()
                print(
                    f"  [弹窗] ({rect.left}, {rect.top}) "
                    f"{rect.width}x{rect.height}"
                )

            # Print step header
            print()
            print("=" * 60)
            print(
                f"  [{i}/{total}] {step.title}  "
                f"({'点击位置' if step_type == 'point' else '矩形区域'})"
            )
            print("=" * 60)
            print(f"  {step.instructions}")

            if step_type == "point":
                result = self._record_point(i, total, step, rect)
            else:
                result = self._record_region(i, total, step, rect)

            if result == "quit":
                break
            elif result == "skip":
                self._skipped.append(f"{section}.{key}")
                continue
            elif result == "ok":
                success_count += 1

        if self._overlay:
            self._overlay.hide()

        if success_count == 0:
            print("\n没有记录任何坐标，取消保存。")
            return False

        self._save()
        return True

    def _record_point(self, i: int, total: int, step: CalibrationStep, rect) -> str:
        """Record a point calibration step."""
        rec = _step_to_screen_point(step, rect)
        rec_x, rec_y = rec if rec else (None, None)

        mx, my = pyautogui.position()

        label = f"[{i}/{total}] {step.title} | F8 记录 · "
        if rec:
            label += "F5 接受推荐 · "
        label += "F9 跳过"

        if self._overlay:
            self._overlay.show_point_mode(mx, my, rec_x, rec_y, label)

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

            result = validate_point(x_ratio, y_ratio, step)
            if result.level != "ok":
                print(f"\n  {result.message}")

        except Exception as e:
            print(f"  [错误] {e}")
            return "skip"

        return "ok"

    def _record_region(self, i: int, total: int, step: CalibrationStep, rect) -> str:
        """Record a region calibration step."""
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
            prompt="移动鼠标到【左上角】→ 按 F8 | F9 跳过",
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
                prompt="移动鼠标到【右下角】→ 按 F8 记录",
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

            result = validate_region(
                left_ratio, top_ratio, width_ratio, height_ratio, step,
            )
            if result.level != "ok":
                print(f"\n  {result.message}")

        except Exception as e:
            print(f"  [错误] {e}")
            return "skip"

        return "ok"

    def _ensure_main_window(self) -> bool:
        print("正在查找微信主窗口...")
        if not self._window_mgr.find_main():
            print()
            print("  [错误] 找不到微信 4.1.7 主窗口！")
            print("  请确保微信 PC 客户端已启动并登录。")
            return False

        rect = self._window_mgr.get_main_rect()
        print(f"  找到主窗口: ({rect.left}, {rect.top}) {rect.width}x{rect.height}")
        self._window_mgr.move_to_primary_screen()
        self._window_mgr.activate()
        return True

    def _save(self) -> None:
        main_rect = self._window_mgr.get_main_rect()

        existing = {}
        if _CALIBRATION_CONFIG.exists():
            try:
                existing = json.loads(_CALIBRATION_CONFIG.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        profiles = existing.get("profiles", {})
        prev = profiles.get("v417_default", {})

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

        profiles["v417_default"] = {
            "meta": {
                "wechat_version": "4.1.7",
                "dpi_scale": 1.0,
                "calibrated_at": datetime.now(timezone.utc).isoformat(),
                "window_rect": {
                    "left": main_rect.left,
                    "top": main_rect.top,
                    "width": main_rect.width,
                    "height": main_rect.height,
                },
            },
            **merged,
        }

        output = {
            "active_profile": "v417_default",
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
