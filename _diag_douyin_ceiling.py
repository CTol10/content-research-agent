"""核实抖音这条视频"到底有多少评论是能拿到的"。

产出三组数字，用来解释"页面显示 900+ / 实际只拿到 720"：
  A. 主楼接口自己上报的 total（页面计数器同源）
  B. 主楼接口实际翻页返回的条数（翻到 has_more=0 为止）
  C. 每条主楼声明 reply_comment_total 之和 vs 实际拉到条数（逐线程对比）

用法: python _diag_douyin_ceiling.py [url]
"""
import asyncio
import sys
from pathlib import Path

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.iesdouyin.com/share/video/7683754809844976948"

list_totals = []      # 主楼接口每次返回的 total 字段
list_pages = []       # (n, has_more, total)


def _f(*a):
    print(*a, flush=True)


async def on_response(resp):
    url = resp.url
    if "/aweme/v1/web/comment/list" not in url or "/reply" in url:
        return
    try:
        data = await resp.json()
    except Exception:
        return
    n = len(data.get("comments") or [])
    total = data.get("total")
    has_more = data.get("has_more")
    list_totals.append(total)
    list_pages.append((n, has_more, total))
    _f(f"    [list] n={n} has_more={has_more} total={total}")


async def main():
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])

    from scrapers.douyin import DouyinScraper, DouyinCommentApiCollector

    dist_profile = Path(__file__).parent / "dist" / "cookies" / "_browser_profile"
    if dist_profile.exists():
        DouyinScraper._USER_DATA_DIR = dist_profile
        _f(f"[i] 使用登录态 profile: {dist_profile}")

    scraper = DouyinScraper()
    await scraper.start(headless=False)
    context = await scraper.new_context()
    page = await context.new_page()
    collector = DouyinCommentApiCollector()
    page.on("request", lambda r: collector.note_request(r.url))

    try:
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
        page.on("response", lambda r: asyncio.ensure_future(on_response(r)))
        scraper._setup_video_capture(page)
        await scraper._install_media_pause_guard(page)

        await page.goto(URL, timeout=30000, wait_until="domcontentloaded")
        await scraper._pause_video(page)
        await asyncio.sleep(2)
        await scraper._dismiss_popups(page)
        await scraper._click_comment_tab(page)
        await asyncio.sleep(3)

        expected = await scraper._get_expected_comment_count(page)
        _f(f"\n[页面显示评论总数] = {expected}")

        # 先滚一屏，让页面自己发出一次主楼接口请求，用于取签名 URL 模板
        await scraper._scroll_comment_container_relative(page, 600)
        await asyncio.sleep(3)
        _f(f"[签名URL] list={'有' if collector.api_urls.get('list') else '无'} "
           f"reply={'有' if collector.api_urls.get('reply') else '无'}")

        video_id = URL.rstrip("/").split("/")[-1]
        ok = await scraper._crawl_comments_via_api(page, collector, video_id)
        _f(f"\n[接口直连] {'成功' if ok else '失败'}")

        # ── 汇总 ────────────────────────────────────────────────
        top_n = len(collector.top_order)
        declared = sum(collector.reply_totals.values())
        loaded = sum(len(v) for v in collector.children.values())
        pending = sorted(collector.pending_threads)

        gaps = []
        for cid, total in collector.reply_totals.items():
            got = len(collector.children.get(cid, []))
            if got < total:
                gaps.append((cid, total, got))
        gaps.sort(key=lambda x: x[1] - x[2], reverse=True)

        _f("\n" + "=" * 62)
        _f("【结论：抖音网页端到底能拿到多少条】")
        _f("=" * 62)
        _f(f"A. 页面/接口上报的 total           : {sorted(set(t for t in list_totals if t is not None))}")
        _f(f"B. 主楼接口实际返回（翻到 has_more=0）: {top_n} 条")
        _f(f"   主楼分页明细: {list_pages}")
        _f(f"C. 主楼自报回复总数之和            : {declared} 条")
        _f(f"   实际拉到回复数                  : {loaded} 条")
        _f(f"D. 真实可达上限 = 主楼 + 自报回复   : {top_n + declared} 条")
        _f(f"E. 未被覆盖（= A - D）             : "
           f"{(max(list_totals) - (top_n + declared)) if list_totals else 'n/a'} 条")
        _f("")
        _f(f"仍未拉完的线程数: {len(pending)}")
        _f(f"回复数对不上的线程: {len(gaps)} 个")
        for cid, total, got in gaps[:15]:
            _f(f"    cid={cid}  声明={total}  实得={got}  差={total - got}")
        _f("=" * 62)

        out = Path("output/debug/_ceiling_report.txt")
        out.parent.mkdir(parents=True, exist_ok=True)
        _f(f"\n>>> 明细见 {out}")
    finally:
        await scraper.stop()


if __name__ == "__main__":
    asyncio.run(main())
