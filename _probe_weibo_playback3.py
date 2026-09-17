# -*- coding: utf-8 -*-
"""dump playback_list 条目字段，并寻找一条纯视频微博帖。"""
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright

import config


def iter_video_infos(status):
    """收集所有 media_info dict（含 stream_url/playback_list）。"""
    out = []

    def walk(obj):
        if isinstance(obj, dict):
            if "playback_list" in obj or ("stream_url" in obj and obj.get("stream_url")):
                out.append(obj)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj[:15]:
                walk(v)

    walk(status)
    return out


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
        await page.goto("https://weibo.com", timeout=30000, wait_until="domcontentloaded")

        # ---- 已知混排视频帖的 playback_list 细节 ----
        r = await page.evaluate(
            """async () => {
                const resp = await fetch('/ajax/statuses/show?id=QFPkEFDLs', {credentials:'include'});
                return await resp.json();
            }"""
        )
        infos = iter_video_infos(r)
        print(f"media_info blocks found: {len(infos)}")
        for mi in infos:
            pl = mi.get("playback_list") or []
            print(f"  duration={(mi.get('duration') or 0)/1000:.1f}s name={mi.get('name')}")
            for i, item in enumerate(pl):
                info = item.get("play_info") or {}
                print(
                    f"    [{i}] play_info keys={sorted(info.keys())}\n"
                    f"        type={info.get('type')} quality_label={info.get('quality_label')} "
                    f"mime={info.get('mime')} bitrate={info.get('bitrate')} size={info.get('size')}"
                )
                for u in (info.get("url_list") or [])[:2]:
                    print(f"        url: {u[:150]}")
            break

        # ---- 从首页/热门里找纯视频帖子试标准路径 ----
        video_post = await page.evaluate(
            """async () => {
                const resp = await fetch('/ajax/statuses/hot_band', {credentials:'include'});
                const j = await resp.json();
                const cards = ((j.data || {}).band_list || []);
                for (const c of cards) {
                    const mb = c.mblogger || c.mblog || {};
                    if (mb.page_info && mb.page_info.type === 'video' && mb.idstr) {
                        return {id: mb.idstr, bid: mb.mblogid, title: (mb.text_raw || '').slice(0, 40)};
                    }
                }
                return null;
            }"""
        )
        print("\nHOT BAND video post:", video_post)
        if video_post:
            r2 = await page.evaluate(
                """async (bid) => {
                    const resp = await fetch('/ajax/statuses/show?id=' + bid, {credentials:'include'});
                    return await resp.json();
                }""",
                video_post["bid"],
            )
            infos2 = iter_video_infos(r2)
            print(f"video post media_info blocks: {len(infos2)}")
            for mi in infos2[:2]:
                pl = mi.get("playback_list") or []
                print(f"  stream_url={bool(mi.get('stream_url'))} duration={(mi.get('duration') or 0)/1000:.1f}s")
                for i, item in enumerate(pl):
                    info = item.get("play_info") or {}
                    print(
                        f"    [{i}] type={info.get('type')} label={info.get('quality_label')} "
                        f"mime={info.get('mime')} url={[u.split('?')[0] for u in (info.get('url_list') or [])[:1]]}"
                    )

        await ctx.close()


asyncio.run(main())
