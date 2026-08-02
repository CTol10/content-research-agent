# scrapers/wechat_calibrator.py
"""WeChat PC coordinate calibration (v4.1.11 single-window).

Re-exports WechatCalibrator from scrapers.wechat.v411.calibrator.

Usage:
    python main.py --wechat-calibrate
    python main.py --wechat-calibrate --no-overlay    (text-only mode)

Features:
  Layer 1 — Smart defaults + coordinate range validation
  Layer 2 — Transparent overlay with crosshair / rectangle preview
"""

from scrapers.wechat.v411.calibrator import WechatCalibrator

__all__ = ["WechatCalibrator"]
