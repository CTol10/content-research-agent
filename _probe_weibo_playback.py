# -*- coding: utf-8 -*-
"""探测微博 statuses/show 的 playback_list 结构。"""
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright

import config

URLS = [
    "http://weibo.com/7621006032/QFPkEFDLs",
    "http://weibo.com/5384087076/QFCtsyf8o",
]


async def main():
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(config.COOKIE_DIR / "_browser_profile"),
            viewport={"width": 1280, "height": 900},
            user_agent=config.DEFAULT_USER_AGENT,
            args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
            headless=True,
        )
        page = await ctx.new_page()

        for url in URLS:
            print(f"\n===== {url} =====")
            seg = url.rstrip("/").split("/")[-1]
            await page.goto("https://weibo.com", timeout=30000, wait_until="domcontentloaded")
            r = await page.evaluate(
                """async (u) => {
                    const resp = await fetch(u, {credentials: 'include'});
                    return await resp.json();
                }""",
                f"https://weibo.com/ajax/statuses/show?id={seg}",
            )
            if not isinstance(r, dict):
                print("unexpected:", str(r)[:200])
                continue
            pi = r.get("page_info") or {}
            media = pi.get("media") or {}
            vi = media.get("video_info") or {}
            pl = vi.get("playback_list") or []
            print(f"page_info.type={pi.get('type')}, media keys={list(media.keys())}")
            print(f"video_info keys={list(vi.keys())}")
            print(f"playback_list entries={len(pl)}")
            for i, item in enumerate(pl):
                info = item.get("play_info") or {}
                urls = info.get("url_list") or []
                short_urls = [u.split("?")[0] for u in urls]
                print(
                    f"  [{i}] type={info.get('type')} quality_label={info.get('quality_label')} "
                    f"mime={info.get('mime')} duration={(vi.get('duration') or 0)/1000:.1f}s "
                    f"short_url={short_urls}"
                )
            # 混合多视频帖子结构（若有）
            mm = r.get("mix_media_info")
            if mm:
                items = mm.get("items") or []
                print(f"mix_media_info.items={len(items)}")
                if items:
                    d0 = items[0].get("data") or {}
                    print("  mix item[0] keys:", list(d0.keys()))
                    m0 = (d0.get("media") or {}).get("video_info") or {}
                    pl0 = m0.get("playback_list") or []
                    for i, item in enumerate(pl0[:3]):
                        info = item.get("play_info") or {}
                        print(f"    mix[{i}] type={info.get('type')} urls={[u.split('?')[0] for u in (info.get('url_list') or [])]}")

        await ctx.close()


asyncio.run(main())
