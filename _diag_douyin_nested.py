"""排除最后一个可能：是否存在"回复的回复"（三层），导致我们的覆盖不完整。

如果某些"回复"自己也带 reply_comment_total > 0，而回复接口只返回直接子级，
那么那些嵌套回复会被计入视频 total、却不在我们的 474 条里 -> 那部分是可补救的真缺口。

用法: python _diag_douyin_nested.py [url]
"""
import asyncio
import json
import sys
from pathlib import Path

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.iesdouyin.com/share/video/7683754809844976948"

_TPL = {"list": None, "reply": None}


def _f(*a):
    print(*a, flush=True)


CRAWL_JS = r"""
async ([mode, tpl, opts]) => {
    const count = opts.count || 50;
    const maxPages = opts.maxPages || 60;
    const seen = new Map();
    let cursor = 0, total = null, lastHasMore = null, lastStatus = null;
    for (let i = 0; i < maxPages; i++) {
        const u = new URL(tpl, location.origin);
        if (mode === 'reply') {
            if (opts.cid) u.searchParams.set('comment_id', opts.cid);
            if (opts.itemId) u.searchParams.set('item_id', opts.itemId);
        }
        u.searchParams.set('cursor', String(cursor));
        u.searchParams.set('count', String(count));
        let j;
        try { j = await (await fetch(u.toString(), { credentials: 'include' })).json(); }
        catch (e) { return { error: String(e), list: [...seen.values()] }; }
        lastStatus = j.status_code;
        const cs = j.comments || [];
        if (total === null) total = j.total;
        for (const c of cs) {
            if (!c.cid || seen.has(c.cid)) continue;
            seen.set(c.cid, {
                cid: c.cid, text: (c.text || '').slice(0, 40),
                replies: c.reply_comment_total || 0,
                level: c.reply_id ? 2 : 1,
            });
        }
        lastHasMore = j.has_more;
        if (j.status_code !== 0 || !j.has_more || cs.length === 0) break;
        const nc = j.cursor;
        cursor = (typeof nc === 'number' && nc > cursor) ? nc : cursor + count;
        await new Promise(r => setTimeout(r, 180));
    }
    return { total, has_more: lastHasMore, status_code: lastStatus, list: [...seen.values()] };
}
"""


async def main():
    from scrapers.douyin import DouyinScraper

    dist_profile = Path(__file__).parent / "dist" / "cookies" / "_browser_profile"
    if dist_profile.exists():
        DouyinScraper._USER_DATA_DIR = dist_profile

    async def on_response(resp):
        u = resp.url
        if "/aweme/v1/web/comment/list" not in u:
            return
        key = "reply" if "/reply" in u else "list"
        if _TPL[key] is None:
            _TPL[key] = u

    scraper = DouyinScraper()
    await scraper.start(headless=False)
    context = await scraper.new_context()
    page = await context.new_page()
    page.on("response", lambda r: asyncio.ensure_future(on_response(r)))

    try:
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
        scraper._setup_video_capture(page)
        await scraper._install_media_pause_guard(page)
        await page.goto(URL, timeout=30000, wait_until="domcontentloaded")
        await scraper._pause_video(page)
        await asyncio.sleep(2)
        await scraper._dismiss_popups(page)
        await scraper._click_comment_tab(page)
        await asyncio.sleep(3)
        await scraper._scroll_to_load_all(page)

        if not _TPL["list"]:
            _f("!! 未捕获主楼接口")
            return
        # 触发一次"展开N条回复"以捕获回复接口模板
        if not _TPL["reply"]:
            for _ in range(12):
                try:
                    h = await page.evaluate_handle(
                        "() => Array.from(document.querySelectorAll('button'))"
                        ".filter(b => /^展开\\s*\\d+\\s*条回复$/.test((b.textContent||'').replace(/\\s+/g,' ').trim()))"
                    )
                    n = await h.evaluate("a => a.length")
                except Exception:
                    n = 0
                if n:
                    el = (await h.evaluate_handle("a => a[0]")).as_element()
                    if el is not None:
                        await el.click(force=True, timeout=5000)
                        await asyncio.sleep(2.5)
                    if _TPL["reply"]:
                        break
                if not await scraper._scroll_comment_container_relative(page, 700):
                    break
                await asyncio.sleep(0.5)

        top = await page.evaluate(CRAWL_JS, ["list", _TPL["list"], {"count": 50, "maxPages": 80}])
        tops = top["list"]
        _f(f"\n[主楼] {len(tops)} 条  total={top.get('total')}  has_more={top.get('has_more')}")

        threads = [c for c in tops if c.get("replies")]
        _f(f"[主楼] 带回复的主楼 {len(threads)} 个，声明回复总数 {sum(c['replies'] for c in threads)}")

        if not _TPL["reply"]:
            _f("!! 未捕获回复接口模板，放弃")
            return

        item_id = _TPL["reply"].split("item_id=")[1].split("&")[0] if "item_id=" in _TPL["reply"] else ""
        all_replies = {}
        declared_sum = 0
        for c in threads:
            r = await page.evaluate(CRAWL_JS, ["reply", _TPL["reply"],
                                               {"cid": c["cid"], "itemId": item_id,
                                                "count": 50, "maxPages": 80}])
            for it in (r.get("list") or []):
                all_replies.setdefault(it["cid"], it)
            declared_sum += c["replies"]
            await asyncio.sleep(0.12)

        _f(f"[回复] 实际拉到 {len(all_replies)} 条（主楼声明合计 {declared_sum}）")

        # 关键统计：回复里有多少"自己也带回复"的
        nested = [it for it in all_replies.values() if it["replies"] > 0]
        _f(f"\n===== 关键判定 =====")
        _f(f"  回复对象中 reply_comment_total > 0 的条数 = {len(nested)}")
        _f(f"  这些回复自报的下级回复总数            = {sum(it['replies'] for it in nested)}")
        for it in sorted(nested, key=lambda x: -x["replies"])[:8]:
            _f(f"     cid={it['cid']} 自报下级={it['replies']}  文本={it['text']!r}")

        if nested:
            probe = max(nested, key=lambda x: x["replies"])
            _f(f"\n  对最大的一条做嵌套请求: comment_id={probe['cid']} (自报 {probe['replies']})")
            r2 = await page.evaluate(CRAWL_JS, ["reply", _TPL["reply"],
                                                {"cid": probe["cid"], "itemId": item_id,
                                                 "count": 50, "maxPages": 20}])
            got = r2.get("list") or []
            _f(f"     -> status={r2.get('status_code')} 返回 {len(got)} 条 has_more={r2.get('has_more')}")
            for it in got[:5]:
                _f(f"        {it['cid']} lvl={it['level']} 自报={it['replies']} {it['text']!r}")
            if got:
                _f("  >>> 存在可单独拉取的嵌套回复 -> 这部分是之前没抓到的")
            else:
                _f("  >>> 嵌套请求返回空 -> 没有更深的层级")
        else:
            _f("  >>> 没有任何回复再带下级，层级只有两层")

        Path("output/debug").mkdir(parents=True, exist_ok=True)
        Path("output/debug/_nested_report.json").write_text(
            json.dumps({"top": len(tops), "total": top.get("total"),
                        "declared_replies": declared_sum, "loaded_replies": len(all_replies),
                        "nested_count": len(nested),
                        "nested_declared": sum(it["replies"] for it in nested),
                        "nested": nested[:50]}, ensure_ascii=False, indent=1), encoding="utf-8")
        _f("\n>>> 明细: output/debug/_nested_report.json")
    finally:
        await scraper.stop()


if __name__ == "__main__":
    asyncio.run(main())
