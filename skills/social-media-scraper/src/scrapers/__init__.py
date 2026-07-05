import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import PLATFORM_PATTERNS


def match_platform(url: str) -> str | None:
    """Return platform key for a URL, or None if unsupported."""
    if not url:
        return None
    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            if pattern in url:
                return platform
    return None
