# scrapers/wechat/calibration/__init__.py
"""Calibration helpers for WeChat PC coordinate recording.

Layers:
  1. Smart defaults + range validation (models.py, validator.py)
  2. Win32 transparent overlay guidance (overlay.py)
  3. OpenCV template matching (matcher.py, optional — not in this phase)
"""

from scrapers.wechat.calibration.models import (
    CalibrationStep,
    STEPS_V411,
    STEPS_V417,
)
from scrapers.wechat.calibration.validator import (
    validate_point,
    validate_region,
    ValidationResult,
)
from scrapers.wechat.calibration.overlay import CalibrationOverlay

__all__ = [
    "CalibrationStep",
    "STEPS_V411",
    "STEPS_V417",
    "validate_point",
    "validate_region",
    "ValidationResult",
    "CalibrationOverlay",
]
