# -*- coding: utf-8 -*-
"""端到端回归：WeiboScraper.scrape() 在真实帖子上返回 narration。"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from scrapers.weibo import WeiboScraper

URLS = [
    ("视频帖", "http://weibo.com/7621006032/QFPkEFDLs"),
    ("图片帖(阴性)", "http://weibo.com/5384087076/QFCtsyf8o"),
]


async def main():
    scraper = WeiboScraper()
    await scraper.start(headless=True)
    try:
        for label, url in URLS:
            print(f"\n===== {label}: {url} =====")
            result = await scraper.scrape(url)
            print("keys:", sorted(result.keys()))
            print("post_content:", (result.get("post_content") or "")[:80])
            print("comments:", len(result.get("comments") or []))
            print("video_url:", result.get("video_url"), "| audio_url:", result.get("audio_url"))
            narr = result.get("narration")
            print("narration:", (narr[:150] + "...") if narr else narr)
    finally:
        await scraper.stop()


asyncio.run(main())
