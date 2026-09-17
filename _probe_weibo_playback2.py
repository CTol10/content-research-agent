# -*- coding: utf-8 -*-
"""递归定位微博 status JSON 中 playback_list / video_info 的路径。"""
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


def find_paths(obj, needle_path, base=""):
    """返回所有匹配的 (path, value-summary)。needle_path 匹配 dict 键名或列表索引元素键。"""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{base}.{k}"
            if k in ("playback_list", "video_info", "media_info", "page_info"):
                summary = ""
                try:
                    if k == "playback_list" and isinstance(v, list):
                        types = [it.get("play_info", {}).get("type") for it in v if isinstance(it, dict)]
                        summary = f"list(len={len(v)}, types={types})"
                        first_urls = [
                            u.split("?")[0]
                            for it in v[:1] if isinstance(it, dict)
                            for u in ((it.get("play_info") or {}).get("url_list") or [])[:1]
                        ]
                        if first_urls:
                            summary += f" first_url={first_urls}"
                    else:
                        summary = json.dumps(v, ensure_ascii=False)[:200]
                except Exception as e:
                    summary = f"<{e}>"
                hits.append((path, summary))
            hits.extend(find_paths(v, needle_path, path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if i > 12:
                break
            hits.extend(find_paths(v, needle_path, f"{base}[{i}]"))
    return hits


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

        for url in URLS:
            print(f"\n===== {url} =====")
            seg = url.rstrip("/").split("/")[-1]
            r = await page.evaluate(
                """async (u) => {
                    const resp = await fetch(u, {credentials: 'include'});
                    return await resp.json();
                }""",
                f"https://weibo.com/ajax/statuses/show?id={seg}",
            )
            print("top keys:", sorted(r.keys()))
            for path, summary in find_paths(r, None):
                print(f"  {path}\n     => {summary[:300]}")

        await ctx.close()


asyncio.run(main())
