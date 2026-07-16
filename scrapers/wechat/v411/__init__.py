# scrapers/wechat/v411/__init__.py
"""WeChat 4.1.11 scrapers — single-window, side-panel layout.

The article/video content opens as a right-side panel within the same
WeChat window. Coordinates are all relative to a single window rect.
"""

from scrapers.wechat.v411.base import WechatPcBaseScraper
from scrapers.wechat.v411.official_pc import WechatOfficialPcScraper
from scrapers.wechat.v411.channels_pc import WechatChannelsPcScraper
from scrapers.wechat.v411.window_manager import WechatWindowManager

__all__ = [
    "WechatPcBaseScraper",
    "WechatOfficialPcScraper",
    "WechatChannelsPcScraper",
    "WechatWindowManager",
]
