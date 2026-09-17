# -*- coding: utf-8 -*-
"""对比音频转录 vs 视频理解转录的效果。"""
import asyncio
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright

import config
from video_processor import VideoProcessor

BID = "QFPkEFDLs"


async def get_url():
    candidates = []
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
        r = await page.evaluate(
            """async () => {
                const resp = await fetch('/ajax/statuses/show?id=' + arguments_ignored_placeholder, {credentials:'include'});
            }"""
        )
    return None


async def main():
    import json

    vp = VideoProcessor()

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
        r = await page.evaluate(
            f"""async () => {{
                const resp = await fetch('/ajax/statuses/show?id={BID}', {{credentials:'include'}});
                return await resp.json();
            }}"""
        )

        # 收集 mp4 候选（最小体积）
        cands = []

        def walk(obj):
            if isinstance(obj, dict):
                pl = obj.get("playback_list")
                if isinstance(pl, list):
                    for item in pl:
                        info = (item.get("play_info") if isinstance(item, dict) else None) or {}
                        urls = list(info.get("url_list") or []) + ([info["url"]] if info.get("url") else [])
                        if info.get("type") == 1 and info.get("mime") == "video/mp4" and urls:
                            cands.append({"size": info.get("size") or 0, "url": urls[0]})
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for v in obj[:15]:
                    walk(v)

        walk(r)
        best = min(cands, key=lambda c: c["size"] or float("inf"))
        url = best["url"].replace("http://", "https://", 1)
        resp = await ctx.request.get(url, headers={"Referer": "https://weibo.com/"})
        body = await resp.body()
        fd, tmp = tempfile.mkstemp(suffix=".mp4")
        os.close(fd)
        with open(tmp, "wb") as f:
            f.write(body)
        await ctx.close()

    print(f"video downloaded {len(body)/(1024*1024):.1f}MB -> {tmp}")

    print("\n--- 路径A: _transcribe_with_mimo（音频优先+视频理解兜底） ---")
    t1 = vp._transcribe_with_mimo(tmp)
    print(t1)

    print("\n--- 路径B: _transcribe_audio_with_mimo 直接调用（第2次观察稳定性） ---")
    ap = vp._extract_audio(tmp)
    b64 = vp._audio_to_base64(ap)
    t2 = vp._transcribe_audio_with_mimo(b64)
    print(t2)

    try:
        os.unlink(tmp)
        if ap and os.path.exists(ap):
            os.unlink(ap)
    except Exception:
        pass


asyncio.run(main())
