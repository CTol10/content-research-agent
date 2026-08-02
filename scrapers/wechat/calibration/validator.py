# scrapers/wechat/calibration/validator.py
"""Coordinate range validation for calibration recordings.

Checks that recorded window-relative ratios fall within expected
ranges for each calibration step.  Ranges are deliberately wide —
they exist to catch gross mis-clicks (e.g. pointing at the window
title bar instead of the comment icon), not to enforce pixel-perfect
positioning.
"""

from dataclasses import dataclass

from scrapers.wechat.calibration.models import CalibrationStep


@dataclass
class ValidationResult:
    ok: bool
    level: str          # "ok" | "warn" | "error"
    message: str = ""


def validate_point(
    x_ratio: float,
    y_ratio: float,
    step: CalibrationStep,
) -> ValidationResult:
    """Validate a recorded point against the step's expected ranges.

    Returns ValidationResult with level:
      - "ok":    within range
      - "warn":  outside range but still plausible (e.g. x_ratio within [0,1])
      - "error": nonsensical (ratio outside [0,1], which means the mouse
                 was outside the window)
    """
    issues: list[str] = []

    # Sanity: ratios must be in [0, 1]
    for name, val in [("x_ratio", x_ratio), ("y_ratio", y_ratio)]:
        if val < -0.05 or val > 1.05:
            return ValidationResult(
                ok=False,
                level="error",
                message=(
                    f"{name}={val:.4f} 超出窗口范围 [0, 1]！\n"
                    f"  请确认微信窗口在前台，且鼠标在窗口内点击。"
                ),
            )

    # Range checks
    if step.valid_x_range:
        lo, hi = step.valid_x_range
        if x_ratio < lo or x_ratio > hi:
            issues.append(
                f"x_ratio={x_ratio:.4f} 超出推荐范围 [{lo:.2f}, {hi:.2f}]"
            )

    if step.valid_y_range:
        lo, hi = step.valid_y_range
        if y_ratio < lo or y_ratio > hi:
            issues.append(
                f"y_ratio={y_ratio:.4f} 超出推荐范围 [{lo:.2f}, {hi:.2f}]"
            )

    if issues:
        msg = (
            f"⚠ 坐标偏差较大:\n"
            + "\n".join(f"  • {i}" for i in issues)
            + f"\n  推荐位置: x≈{step.default_x_ratio:.2f}, y≈{step.default_y_ratio:.2f}"
            + f"\n  按 F8 确认保存当前坐标，F9 重新录制"
        )
        return ValidationResult(ok=True, level="warn", message=msg)

    return ValidationResult(ok=True, level="ok")


def validate_region(
    left_ratio: float,
    top_ratio: float,
    width_ratio: float,
    height_ratio: float,
    step: CalibrationStep,
) -> ValidationResult:
    """Validate a recorded region against the step's expected ranges."""
    issues: list[str] = []

    # Sanity: all ratios must be in [0, 1]
    for name, val in [
        ("left_ratio", left_ratio),
        ("top_ratio", top_ratio),
        ("width_ratio", width_ratio),
        ("height_ratio", height_ratio),
    ]:
        if val < -0.05 or val > 1.05:
            return ValidationResult(
                ok=False,
                level="error",
                message=(
                    f"{name}={val:.4f} 超出窗口范围 [0, 1]！\n"
                    f"  请确认微信窗口在前台，且鼠标在窗口内点击。"
                ),
            )

    # Width must be positive
    if width_ratio <= 0:
        return ValidationResult(
            ok=False,
            level="error",
            message=f"width_ratio={width_ratio:.4f} 不能为负数或零（请先记录左上角再记录右下角）",
        )
    if height_ratio <= 0:
        return ValidationResult(
            ok=False,
            level="error",
            message=f"height_ratio={height_ratio:.4f} 不能为负数或零（请先记录左上角再记录右下角）",
        )

    # Range checks
    if step.valid_w_range:
        lo, hi = step.valid_w_range
        if width_ratio < lo or width_ratio > hi:
            issues.append(
                f"width_ratio={width_ratio:.4f} 超出推荐范围 [{lo:.2f}, {hi:.2f}]"
            )

    if step.valid_h_range:
        lo, hi = step.valid_h_range
        if height_ratio < lo or height_ratio > hi:
            issues.append(
                f"height_ratio={height_ratio:.4f} 超出推荐范围 [{lo:.2f}, {hi:.2f}]"
            )

    if issues:
        msg = (
            f"⚠ 区域尺寸偏差较大:\n"
            + "\n".join(f"  • {i}" for i in issues)
            + f"\n  推荐区域: left≈{step.default_left_ratio:.2f} top≈{step.default_top_ratio:.2f} "
            + f"w≈{step.default_width_ratio:.2f} h≈{step.default_height_ratio:.2f}"
            + f"\n  按 F8 确认保存当前区域，F9 重新录制"
        )
        return ValidationResult(ok=True, level="warn", message=msg)

    return ValidationResult(ok=True, level="ok")
