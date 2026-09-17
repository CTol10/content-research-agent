"""诊断抖音评论抓取为何丢评论。

用法: python _diag_douyin.py [url]

思路:
  1. 拦截 /aweme/v1/web/comment/list/ 与 .../list/reply/ 接口响应，
     记录每次请求的 cursor / 返回条数 / has_more / total。
     -> 判断"接口本身能否拿到全部评论"
  2. 分阶段打印 DOM 中 [data-e2e="comment-item"] 数量。
     -> 判断"滚动加载是否把列表拉到底"
  3. 打印页面内所有"展开"按钮的文案统计（展开前后各一次）。
     -> 判断"哪些展开按钮没被点"
  4. 统计接口累计拿到的唯一评论 id 数，与 DOM 抽取值对比。
"""
import asyncio
import json
import sys
import time
from collections import Counter

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.iesdouyin.com/share/video/7683754809844976948"

api_events = []          # 每次 comment 接口调用
api_comment_ids = set()  # 接口返回的全部唯一 cid
api_top_ids = set()
api_reply_ids = set()
api_raw_counts = Counter()


def _flush(*a):
    print(*a, flush=True)


async def on_response(resp):
    url = resp.url
    if "/aweme/v1/web/comment/list" not in url:
        return
    kind = "reply" if "/reply" in url else "list"
    try:
        data = await resp.json()
    except Exception as e:
        api_events.append({"kind": kind, "err": str(e), "url": url})
        return
    comments = data.get("comments") or []
    cursors = data.get("cursor")
    has_more = data.get("has_more")
    total = data.get("total")
    for c in comments:
        cid = c.get("cid")
        if cid:
            api_comment_ids.add(cid)
            (api_reply_ids if kind == "reply" else api_top_ids).add(cid)
    api_raw_counts[kind] += 1
    api_events.append({
        "kind": kind, "n": len(comments), "cursor": cursors,
        "has_more": has_more, "total": total,
    })
    _flush(f"    [API] {kind:5s} n={len(comments):4d} has_more={has_more} cursor={cursors} total={total}")


async def dom_stats(page, tag):
    stats = await page.evaluate("""
        () => {
            const items = document.querySelectorAll('[data-e2e="comment-item"]');
            const btns = Array.from(document.querySelectorAll('button'))
                .map(b => (b.textContent || '').replace(/\\s+/g, ' ').trim())
                .filter(t => t && (t.includes('展开') || t.includes('更多')));
            const c = document.querySelector('[data-e2e="comment-list"]');
            const getContainer = () => {
                const seed = c || items[0];
                if (seed) {
                    let el = seed;
                    while (el && el !== document.body) {
                        const cs = window.getComputedStyle(el);
                        if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll')
                            && el.scrollHeight > el.clientHeight + 20) return el;
                        el = el.parentElement;
                    }
                }
                for (const cmc of document.querySelectorAll('.comment-mainContent')) {
                    if (cmc.scrollHeight > cmc.clientHeight + 20) return cmc;
                }
                return document.querySelector('div.route-scroll-container')
                    || document.querySelector('main.ICt0G60V');
            };
            const ct = getContainer();
            return {
                items: items.length,
                scrollHeight: ct ? ct.scrollHeight : -1,
                clientHeight: ct ? ct.clientHeight : -1,
                scrollTop: ct ? Math.round(ct.scrollTop) : -1,
                containerClass: ct ? (ct.className || '').toString().slice(0, 60) : null,
                btnStats: btns.reduce((acc, t) => { acc[t] = (acc[t] || 0) + 1; return acc; }, {}),
            };
        }
    """)
    _flush(f"[{tag}] DOM items={stats['items']} "
           f"scroll={stats['scrollTop']}/{stats['scrollHeight']} (client={stats['clientHeight']}) "
           f"container={stats['containerClass']!r}")
    btns = stats["btnStats"]
    if btns:
        _flush(f"       展开按钮统计: {json.dumps(btns, ensure_ascii=False)}")
    else:
        _flush("       展开按钮统计: (无)")
    return stats


async def main():
    from pathlib import Path
    from scrapers.douyin import DouyinScraper

    # 复用 exe 运行时的登录态（与 dist 版完全一致的 profile）
    dist_profile = Path(__file__).parent / "dist" / "cookies" / "_browser_profile"
    if dist_profile.exists():
        DouyinScraper._USER_DATA_DIR = dist_profile
        _flush(f"[i] 使用登录态 profile: {dist_profile}")

    scraper = DouyinScraper()
    await scraper.start(headless=False)
    context = await scraper.new_context()
    page = await context.new_page()
    page.on("response", lambda r: asyncio.ensure_future(on_response(r)))

    try:
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
        scraper._setup_video_capture(page)
        await scraper._install_media_pause_guard(page)
        t0 = time.time()
        await page.goto(URL, timeout=30000, wait_until="domcontentloaded")
        await scraper._pause_video(page)
        await asyncio.sleep(2)
        await scraper._dismiss_popups(page)
        await scraper._click_comment_tab(page)
        await asyncio.sleep(3)
        _flush(f"\n===== 页面就绪 ({time.time()-t0:.0f}s) =====")
        await dom_stats(page, "初始")

        expected = await scraper._get_expected_comment_count(page)
        _flush(f"页面显示评论总数 = {expected}")

        _flush("\n===== 阶段1: _scroll_to_load_all =====")
        await scraper._scroll_to_load_all(page, expected_count=expected)
        await dom_stats(page, "滚动后")
        _flush(f"接口累计唯一 cid: 主楼={len(api_top_ids)} 回复={len(api_reply_ids)} 合计={len(api_comment_ids)}")

        _flush("\n===== 阶段2: _expand_all_replies =====")
        await scraper._expand_all_replies(page)
        await dom_stats(page, "展开后")
        _flush(f"接口累计唯一 cid: 主楼={len(api_top_ids)} 回复={len(api_reply_ids)} 合计={len(api_comment_ids)}")

        _flush("\n===== 阶段3: _extract_all_comments =====")
        comments = await scraper._extract_all_comments(page, expected_count=expected)
        _flush(f"DOM 抽取结果 = {len(comments)}")

        _flush("\n===== 接口调用汇总 =====")
        for e in api_events:
            _flush("  " + json.dumps(e, ensure_ascii=False))
        _flush(f"\n>>> 页面总数={expected}  接口唯一cid={len(api_comment_ids)} "
               f"(主楼{len(api_top_ids)} + 回复{len(api_reply_ids)})  DOM抽取={len(comments)}")

        await page.screenshot(path="output/debug/_diag_final.png", full_page=False)
    finally:
        await scraper.stop()


if __name__ == "__main__":
    asyncio.run(main())
