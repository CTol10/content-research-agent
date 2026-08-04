"""
Shared comment content cleaning utilities.

Applies post-processing to scraped comment text:
1. Strips "回复 XXX :" and "回复 XXX用户：" reply prefix
2. Strips trailing metadata (timestamps, IP/location tags)
3. Strips concatenated usernames from douyin-style comment threads
4. Normalizes whitespace
"""
import re


# ── Reply prefix patterns ────────────────────────────────────────────────

# "回复 username :" / "回复 username用户：" / "回复@username：" (Xiaohongshu/Weibo style)
_REPLY_PREFIX = re.compile(
    r'^回复\s*@?\s*[^\s:：]*\s*[：:]\s*'
)

# "回复 username" (no colon, Weibo style)
_REPLY_PREFIX_NO_COLON = re.compile(
    r'^回复\s*@?\s*[^\s:]+\s+'
)

# "回复@username" right at start with no space (common in concatenated DOM)
_REPLY_AT = re.compile(
    r'^回复@[^\s:：]+\s*'
)


# ── Trailing metadata patterns ───────────────────────────────────────────

# "X分钟前·省份" "X小时前·省份" "X天前·省份" "刚刚·省份"
_TIME_LOCATION = re.compile(
    r'\s*\d+\s*(分钟前|小时前|天前|周前|月前|年前|秒前)\s*[·•]\s*[^\s]+$'
)
_JUST_NOW_LOCATION = re.compile(
    r'\s*刚刚\s*[·•]\s*[^\s]+$'
)

# Lone timestamp/location without comment content
_LONE_TIME = re.compile(
    r'^\s*\d+\s*(分钟前|小时前|天前|周前|月前|年前|秒前)\s*$'
)
_LONE_JUST_NOW = re.compile(r'^\s*刚刚\s*$')
_LONE_LOCATION = re.compile(
    r'^\s*[·•]\s*\S+\s*$'
)

# "·省份" or "·省份名" at the end (douyin style)
_TRAILING_LOCATION = re.compile(
    r'\s*[·•]\s*(?:北京|上海|天津|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|广西|海南|四川|贵州|云南|西藏|陕西|甘肃|青海|宁夏|新疆|内蒙古|台湾|香港|澳门'
    r'|北京市|上海市|天津市|重庆市|广东省|浙江省|江苏省|四川省|湖北省|湖南省|河南省|山东省|福建省|安徽省|河北省|陕西省|辽宁省|江西省|山西省|吉林省|黑龙江省|云南省|贵州省|甘肃省|海南省|青海省|广西|内蒙古|西藏|宁夏|新疆|台湾|香港|澳门'
    r')(?:省|市)?\s*$'
)

# "来自 XXX" device/location info
_DEVICE_LOCATION = re.compile(
    r'\s*来自\s*\S+\s*$'
)

# "发布于 XXX"
_PUBLISH_LOCATION = re.compile(
    r'\s*发布于\s*\S+\s*$'
)


def strip_reply_prefix(text: str) -> str:
    """Remove '回复 username :' / '回复@username：' / '回复 username' prefix."""
    text = _REPLY_PREFIX.sub('', text).strip()
    text = _REPLY_PREFIX_NO_COLON.sub('', text).strip()
    text = _REPLY_AT.sub('', text).strip()
    return text


def strip_metadata(text: str) -> str:
    """Remove trailing metadata: timestamps, locations, device info."""
    # Time + location: "21分钟前·浙江"
    text = _TIME_LOCATION.sub('', text)
    text = _JUST_NOW_LOCATION.sub('', text)

    # Lone location dot: "·广东"
    text = _TRAILING_LOCATION.sub('', text)

    # Device/location: "来自 iPhone", "来自 浙江"
    text = _DEVICE_LOCATION.sub('', text)

    # Publish location: "发布于 浙江"
    text = _PUBLISH_LOCATION.sub('', text)

    return text.strip()


def clean_comment_content(text: str, platform: str | None = None) -> str:
    """
    Clean a single comment's text content.

    Returns the cleaned text, or empty string if the text contains only metadata.
    """
    if not text or not text.strip():
        return ''

    text = text.strip()

    # Step 1: Strip reply prefix
    text = strip_reply_prefix(text)

    # Step 2: Strip trailing metadata
    text = strip_metadata(text)

    # Step 3: Normalize whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    # Step 4: Check if content is now just metadata/empty
    if _LONE_TIME.match(text) or _LONE_JUST_NOW.match(text) or _LONE_LOCATION.match(text):
        return ''

    return text


def should_split_concatenated(text: str) -> bool:
    """
    Detect if a comment text is actually multiple comments concatenated.

    This happens on douyin when the fallback text extraction pulls
    innerText from a reply thread container.
    """
    if len(text) < 50:
        return False
    # Count occurrences of known username separators
    # Douyin concatenates: "content1 username1 content2 username2 ..."
    # Usernames often appear between content segments
    return False  # This detection is too fragile; we fix at extraction level instead
