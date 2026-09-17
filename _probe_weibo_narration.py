# -*- coding: utf-8 -*-
"""探测微博视频地址获取途径：DOM <video> / 内嵌数据 / Ajax API。"""
import asyncio
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from playwright.async_api import async_playwright

import config

URLS = [
    "http://weibo.com/7621006032/QFPkEFDLs",
]


async def main():
    captured = []
    async with async_playwright() as p:
        launch_args = {
            "user_data_dir": str(config.COOKIE_DIR / "_browser_profile"),
            "viewport": {"width": 1280, "height": 900},
            "user_agent": config.DEFAULT_USER_AGENT,
            "args": ["--disable-blink-features=AutomationControlled", "--no-first-run"],
            "headless": True,
        }
        if not hasattr(config, "_probe_channel"):
            pass
        ctx = await p.chromium.launch_persistent_context(**launch_args)
        page = await ctx.new_page()

        def on_response(resp):
            u = resp.url
            ul = u.lower()
            if any(k in ul for k in [".mp4", ".m3u8", "mediaboost", "sinavideo", "weibocdn"]):
                captured.append((resp.status, resp.headers.get("content-type", ""), u))

        page.on("response", on_response)

        for url in URLS:
            print(f"\n===== {url} =====")
            await page.goto(url, timeout=45000, wait_until="domcontentloaded")
            await asyncio.sleep(12)

            print("TITLE:", await page.title())

            # 1) $render_data 内嵌数据
            rd = await page.evaluate(
                """() => {
                    const out = {};
                    try {
                        if (window.$render_data) {
                            out.keys = Object.keys(window.$render_data);
                            const st = window.$render_data['status'];
                            if (st && typeof st === 'object') {
                                out.statusKeys = Object.keys(st).slice(0, 60);
                                if (st.page_info) out.page_info_preview = JSON.stringify(st.page_info).slice(0, 1200);
                            }
                            out.raw = JSON.stringify(window.$render_data).slice(0, 400);
                        }
                    } catch (e) { out.err = String(e); }
                    return out;
                }"""
            )
            print("RENDER_DATA:", json.dumps(rd, ensure_ascii=False)[:2500])

            # 2) <video> 元素
            vids = await page.evaluate(
                """() => Array.from(document.querySelectorAll('video')).map(v => ({
                    src: v.currentSrc || v.src || '',
                    sources: Array.from(v.querySelectorAll('source')).map(s => s.src),
                    visibleW: v.getBoundingClientRect().width,
                }))"""
            )
            print("VIDEO ELEMENTS:", json.dumps(vids, ensure_ascii=False)[:1500])

            # 3) ajax API 尝试（用 URL 末段 base62 bid）
            seg = url.rstrip("/").split("/")[-1]
            for ep in [
                f"/ajax/statuses/show?id={seg}",
            ]:
                r = await page.evaluate(
                    """async (u) => {
                        try {
                            const resp = await fetch(u, {credentials: 'include'});
                            const body = await resp.json();
                            const txt = JSON.stringify(body);
                            return {
                                status: resp.status,
                                topKeys: typeof body === 'object' ? Object.keys(body).slice(0, 20) : null,
                                code: body.code, ok: body.ok,
                                hasPlaybackList: txt.includes('playback_list'),
                                len: txt.length,
                                preview: txt.slice(0, 600),
                            };
                        } catch (e) { return {err: String(e)}; }
                    }""",
                    ep,
                )
                print(f"API GET {ep}\n  ->", json.dumps(r, ensure_ascii=False)[:2000])

            # 4) 页面上抓到的媒体响应
            print("CAPTURED MEDIA RESPONSES:", len(captured))
            for s, ct, u in captured[:15]:
                print("   ", s, ct, u[:160])

        await ctx.close()


asyncio.run(main())
