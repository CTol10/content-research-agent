# scrapers/wechat/v417/__init__.py
"""WeChat 4.1.7 scrapers — dual-window, popup-based layout.

The article/video content opens as a SEPARATE Chrome_WidgetWin_0 popup
window alongside the Qt main window. Coordinates in ``wechat_main``
resolve against the main window; ``official`` / ``channels`` resolve
against the popup.
"""

from scrapers.wechat.v417.base import WechatPcBaseScraperV417
from scrapers.wechat.v417.official_pc import WechatOfficialPcScraperV417
from scrapers.wechat.v417.channels_pc import WechatChannelsPcScraperV417
from scrapers.wechat.v417.window_manager import WechatWindowManagerV417

__all__ = [
    "WechatPcBaseScraperV417",
    "WechatOfficialPcScraperV417",
    "WechatChannelsPcScraperV417",
    "WechatWindowManagerV417",
]
