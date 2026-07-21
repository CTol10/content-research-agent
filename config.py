import os
import platform
import re
import sys
from pathlib import Path

# Paths — in frozen exe, resolve relative to the exe location
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent

# Playwright browser path — use exe-adjacent dir so browsers survive exe updates
if getattr(sys, 'frozen', False):
    _playwright_browser_path = BASE_DIR / "playwright_browsers"
    _playwright_browser_path.mkdir(exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_playwright_browser_path)
INPUT_DIR = BASE_DIR / "input"
INPUT_FILE = BASE_DIR / "example.xlsx"  # fallback if input/ is empty
if INPUT_DIR.exists():
    for f in sorted(INPUT_DIR.glob("*.xlsx")):
        INPUT_FILE = f
        break
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
    "wechat": ["mp.weixin.qq.com"],
    "wechat_channels": ["channels.weixin.qq.com", "weixin.qq.com/sph"],
    "toutiao": ["toutiao.com"],
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

# WeChat CDP (Chrome DevTools Protocol) connection
# Launch WeChat with: WeChat.exe --remote-debugging-port=9222
WECHAT_CDP_URL = "http://127.0.0.1:9222"

# Chrome CDP — use system Chrome for WeChat scraping
CHROME_CDP_PORT = 9230
CHROME_USER_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
# Separate profile for scraping (avoids conflict with running Chrome)
CHROME_SCRAPE_PROFILE = BASE_DIR / "cookies" / "_chrome_profile"

# === WeChat PC Desktop Configuration ===
WECHAT_PC_CONFIG_PATH = BASE_DIR / "config.wechat_pc.json"
WECHAT_PC_DEBUG_DIR = OUTPUT_DIR / "debug" / "wechat_pc"

# API key loader — reads from env var, then config.ini
def _load_api_key(env_name: str, ini_name: str = "") -> str:
    key = os.getenv(env_name, "")
    if key:
        return key
    ini_key = ini_name or env_name
    ini_path = BASE_DIR / "config.ini"
    if ini_path.exists():
        try:
            for line in ini_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(ini_key) and "=" in line:
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
        except Exception:
            pass
    return ""


# MiMo-v2.5 (Xiaomi) — used for both classification and video understanding
MIMO_API_KEY = _load_api_key("MIMO_API_KEY")
MIMO_BASE_URL = "https://api.xiaomimimo.com/v1"
MIMO_MODEL = "mimo-v2.5"

# 视频号在线解析（sph 分享链接 -> 元宝解析 -> finder-preview 下载视频，仅口播，不碰评论）
# 元宝 cookie 不走 config.ini：由 `python main.py --login-yuanbao` 登录后存入
# Playwright 持久化 profile (cookies/_yuanbao_profile)，解析时 headless 重开读取。

# Video processing
VIDEO_TEMP_DIR = OUTPUT_DIR / "video_temp"
MAX_VIDEO_SIZE_MB = 200
VIDEO_DOWNLOAD_TIMEOUT = 60
VIDEO_BASE64_MAX_MB = 35  # base64 limit for MiMo (~50MB encoded)
ENABLE_VIDEO_PROCESSING = True

# Video URL filtering
VIDEO_URL_EXCLUDE_KEYWORDS = ["ad", "tracker", "analytics", "beacon", "log."]
VIDEO_EXTENSIONS = [".mp4", ".m3u8", ".webm", ".ts"]


def sanitize_filename(name: str, max_len: int = 20) -> str:
    """Remove illegal filesystem chars and truncate.

    Handles Windows-illegal chars (\\/:"<>|) and Unix edge cases (leading dots, null bytes).
    """
    cleaned = ILLEGAL_CHARS.sub("_", name).replace("\x00", "").strip()
    if cleaned.startswith("."):
        cleaned = "_" + cleaned
    return cleaned[:max_len] if cleaned else "untitled"
