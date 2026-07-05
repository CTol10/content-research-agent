import os
import platform
import re
from pathlib import Path

# Paths — relative to this config file's directory
BASE_DIR = Path(__file__).parent
INPUT_FILE = BASE_DIR / "example.xlsx"
OUTPUT_DIR = BASE_DIR / "output"
COOKIE_DIR = BASE_DIR / "cookies"
LOG_DIR = BASE_DIR / "logs"

# Scraping
MIN_DELAY = 1.0
MAX_DELAY = 3.0
MAX_SCROLL = 50
PAGE_TIMEOUT = 30000  # ms

# Excel input columns (0-indexed)
COL_SERIAL = 0       # 序号
COL_TITLE = 1        # 标题/微博内容
COL_URL = 2          # 原文/评论链接
COL_SOURCE = 3       # 来源网站
COL_PLATFORM = 4     # 媒体平台
COL_DATE = 5         # 日期
COL_AUTHOR = 6       # 原文作者

# Platform URL patterns -> platform key
PLATFORM_PATTERNS: dict[str, list[str]] = {
    "douyin": ["iesdouyin.com", "douyin.com"],
    "xiaohongshu": ["xiaohongshu.com"],
    "weibo": ["weibo.com"],
}

# OS detection
SYSTEM = platform.system()  # "Windows", "Darwin", "Linux"

# User-agent per OS (spoofed to mask Playwright fingerprint)
_USER_AGENTS = {
    "Windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Darwin": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Linux": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}
DEFAULT_USER_AGENT = _USER_AGENTS.get(SYSTEM, _USER_AGENTS["Windows"])

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')


def sanitize_filename(name: str, max_len: int = 20) -> str:
    """Remove illegal filesystem chars and truncate.

    Handles Windows-illegal chars (\\/:"<>|) and Unix edge cases (leading dots, null bytes).
    """
    cleaned = ILLEGAL_CHARS.sub("_", name).replace("\x00", "").strip()
    if cleaned.startswith("."):
        cleaned = "_" + cleaned
    return cleaned[:max_len] if cleaned else "untitled"
