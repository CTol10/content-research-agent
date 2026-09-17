# -*- coding: utf-8 -*-
"""端到端验证：微博视频下载 -> 提取音轨 -> MiMo 口播转录。"""
import asyncio
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright

import config

BID = "QFPkEFDLs"


def pick_playback_url(status):
    """从 status JSON 收集候选 mp4，选最小的。"""
    candidates = []

    def walk(obj):
        if isinstance(obj, dict):
            pl = obj.get("playback_list")
            if isinstance(pl, list):
                for item in pl:
                    info = (item.get("play_info") if isinstance(item, dict) else None) or {}
                    urls = list(info.get("url_list") or [])
                    if info.get("url"):
                        urls.append(info["url"])
                    if (
                        info.get("type") == 1
                        and info.get("mime") == "video/mp4"
                        and urls
                    ):
                        candidates.append(
                            {
                                "quality_label": info.get("quality_label"),
                                "size": info.get("size") or 0,
                                "url": urls[0],
                            }
                        )
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj[:15]:
                walk(v)

    walk(status)
    return candidates


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

        r = await page.evaluate(
            """async () => {
                const resp = await fetch('/ajax/statuses/show?id=QFPkEFDLs', {credentials:'include'});
                return await resp.json();
            }"""
        )
        cands = pick_playback_url(r)
        print("candidates:")
        for c in cands:
            print(f"   {c['quality_label']} size={c['size']/(1024*1024):.1f}MB {c['url'][:100]}")
        if not cands:
            print("NO CANDIDATES")
            return

        # 只验证口播 -> 选最小文件
        best = min(cands, key=lambda c: c["size"] or float("inf"))
        url = best["url"].replace("http://", "https://", 1)
        print(f"\npicked {best['quality_label']} {best['size']/(1024*1024):.1f}MB")

        resp = await ctx.request.get(url, headers={
            "Referer": "https://weibo.com/",
            "Origin": "https://weibo.com",
        })
        print("download HTTP:", resp.status)
        body = await resp.body()
        print("downloaded:", len(body) / 1024 / 1024, "MB")
        tmp_fd, tmp = tempfile.mkstemp(suffix=".mp4")
        os.close(tmp_fd)
        with open(tmp, "wb") as f:
            f.write(body)

        await ctx.close()

    from video_processor import VideoProcessor

    vp = VideoProcessor()

    audio_path = vp._extract_audio(tmp)
    print("\naudio extracted:", audio_path, "->", audio_path and os.path.getsize(audio_path) // 1024, "KB")

    if not audio_path:
        print("FALLBACK: 手动 AAC 转码")
        import subprocess

        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        out = tmp.rsplit(".", 1)[0] + "_conv.m4a"
        res = subprocess.run(
            [ffmpeg, "-i", tmp, "-vn", "-c:a", "aac", "-b:a", "128k", out, "-y"],
            capture_output=True, text=True, timeout=120,
        )
        print("convert rc:", res.returncode, os.path.exists(out))
        audio_path = out if os.path.exists(out) else None

    b64 = vp._audio_to_base64(audio_path)
    print("audio base64:", b64[:40], "len:", len(b64) // 1024, "KB")

    text = vp._transcribe_audio_with_mimo(b64)
    print("\n===== TRANSCRIPTION =====")
    print(text)
    print("=========================")

    try:
        os.unlink(tmp)
        if audio_path and os.path.exists(audio_path):
            os.unlink(audio_path)
    except Exception:
        pass


asyncio.run(main())
