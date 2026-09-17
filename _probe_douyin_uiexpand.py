"""用「纯 UI 逐条展开」的方式跑一遍抖音这条视频，看能识别到多少条评论。

策略（按需求）：
  1. 打开浏览器 -> 进评论区 -> 滚回顶部；
  2. 当前屏里反复点「最靠前的那一个」展开控件（展开N条回复 / 展开更多），
     直到这一条评论再没有展开控件（= 它的回复已全部展开），才算这一条做完；
  3. 这一屏再没有可展开的，才向下滚一屏；
  4. 重复到滚到底、且全列表再无展开控件为止；
  5. 最后回滚到第一条，做识别（DOM 抽取），统计条数。

全程只用真实鼠标事件驱动 UI（不调接口直连），与「接口直连」方案交叉对照。

用法: python _probe_douyin_uiexpand.py [url]
"""
import asyncio
import json
import sys
import time
from pathlib import Path

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.iesdouyin.com/share/video/7683754809844976948"
MAX_CLICKS = 1200
MAX_SCROLLS = 250
SCROLL_STEP = 700
STALL_LIMIT = 12
FAIL_LIMIT = 15
NO_PROGRESS_LIMIT = 30   # 连续这么多次点击都没有新评论 -> 已经饱和，收工


def _f(*a):
    print(*a, flush=True)


async def _first_visible_expand_info(page):
    """当前视口内 DOM 顺序最靠前的展开控件信息（只看，不点）。"""
    return await page.evaluate(r"""
        () => {
            const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
            const isExpand = (t) => {
                if (!t) return false;
                if (t.includes('收起') || t.includes('全文')) return false;
                return /^展开\s*\d+\s*条回复$/.test(t)
                    || /^展开更多$/.test(t)
                    || /^点击加载更多$/.test(t);
            };
            const vh = window.innerHeight || 900;
            for (const btn of Array.from(document.querySelectorAll('button'))) {
                const text = norm(btn.textContent);
                if (!isExpand(text)) continue;
                const rect = btn.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) continue;
                if (rect.top < -4 || rect.top > vh - 4) continue;
                const item = btn.closest('[data-e2e="comment-item"]');
                const nickEl = item ? item.querySelector('[data-click-from="title"]') : null;
                return {
                    ok: true,
                    text: text,
                    nick: nickEl ? norm(nickEl.textContent) : '',
                };
            }
            return { ok: false };
        }
    """)


async def _click_first_visible_expand(page, visible_js):
    """点掉当前视口里最靠前的一个展开控件。返回被点控件的信息或 None。"""
    try:
        handles = await page.evaluate_handle(visible_js)
        count = await handles.evaluate("(arr) => arr.length")
    except Exception:
        return None
    if not count:
        return None

    info = await _first_visible_expand_info(page)
    try:
        handle = await handles.evaluate_handle("(arr) => arr[0]")
        element = handle.as_element()
        if element is None:
            return None
        await element.click(force=True, timeout=3000)
    except Exception:
        return None
    return info if info.get("ok") else {"text": "?", "nick": "?"}


async def _has_visible_expand(page, visible_js) -> bool:
    try:
        handles = await page.evaluate_handle(visible_js)
        return bool(await handles.evaluate("(arr) => arr.length"))
    except Exception:
        return False


async def phase1_expand(scraper, page, collector, visible_js):
    """逐条展开：一直点最靠前的那一个，点不动了才下滚一屏。

    卡死保护：有些「点击加载更多」是已经点到底的线程残留控件，点它什么都不会变。
    用「接口侧唯一 cid 数 + DOM 渲染数」作为进度指标，连续 STALL_LIMIT 次点击
    都毫无进展就把这一屏判为枯竭、强制下滚，避免在死控件上空转。
    """
    _f("\n" + "=" * 62)
    _f("阶段 1：逐条展开回复（真实鼠标点击，点完一条再下一条）")
    _f("=" * 62)

    await scraper._scroll_comment_container(page, 0)
    await asyncio.sleep(1.5)

    total_clicks = 0
    scrolls = 0
    per_owner = {}
    last_owner = None
    streak = 0
    stall = 0
    fail_rounds = 0
    no_progress = 0
    last_progress = len(collector.nodes) + len(collector.top_order)
    t0 = time.time()

    def _progress():
        return len(collector.nodes) + len(collector.top_order)

    while total_clicks < MAX_CLICKS and scrolls < MAX_SCROLLS:
        info = await _click_first_visible_expand(page, visible_js)

        if info is None:
            # 两种情况：真没有按钮；或按钮在点击瞬间被抖音重渲染销毁了（这次算没点着）
            if await _has_visible_expand(page, visible_js):
                fail_rounds += 1
                if fail_rounds >= FAIL_LIMIT:
                    _f(f"  [i] 连续 {fail_rounds} 次点击未成功，判定本屏枯竭，强制下滚")
                    fail_rounds = 0
                    if not await scraper._scroll_comment_container_relative(page, SCROLL_STEP):
                        _f("  [i] 已到底，展开阶段结束")
                        break
                    scrolls += 1
                await asyncio.sleep(0.3)
                continue

            # 真没有按钮：先等渲染，再决定是否下滚（避免滚太快漏掉还没渲染出来的控件）
            for _ in range(3):
                await asyncio.sleep(0.6)
                if await _has_visible_expand(page, visible_js):
                    break
            else:
                fail_rounds = 0
                stall = 0
                if not await scraper._scroll_comment_container_relative(page, SCROLL_STEP):
                    await asyncio.sleep(1.0)
                    if await _has_visible_expand(page, visible_js):
                        continue
                    _f("  [i] 已滚到底且视口内再无展开控件，展开阶段结束")
                    break
                scrolls += 1
                if scrolls % 10 == 0:
                    _f(f"  ... 已下滚 {scrolls} 屏, 点击 {total_clicks} 次, 接口条数={_progress()}")
                await asyncio.sleep(0.5)
            continue

        # ── 点着了一个 ────────────────────────────────────────────
        fail_rounds = 0
        total_clicks += 1
        owner = (info.get("nick") or "")[:16] or "(未知)"
        per_owner[owner] = per_owner.get(owner, 0) + 1
        if owner == last_owner:
            streak += 1
        else:
            streak = 1
            last_owner = owner
        await scraper._wait_api_settle(collector, timeout=3.0)

        progress = _progress()
        if progress > last_progress:
            last_progress = progress
            stall = 0
            no_progress = 0
        else:
            stall += 1
            no_progress += 1

        _f(f"  #{total_clicks:<4} 《{owner}》 {info.get('text')}  [本条第 {streak} 次]"
           f"  dom_items={await scraper._count_comment_items(page)}"
           f"  接口条数={progress}  空转={stall}")

        if no_progress >= NO_PROGRESS_LIMIT:
            _f(f"  [i] 连续 {no_progress} 次点击都没有新评论，判定已饱和，展开阶段结束")
            break

        if stall >= STALL_LIMIT:
            # 这一屏只剩点不动的死控件（已经点到底的「点击加载更多」），强制往下走
            _f(f"  [i] 连续 {stall} 次点击无新增，判定本屏枯竭，强制下滚")
            stall = 0
            if not await scraper._scroll_comment_container_relative(page, SCROLL_STEP):
                _f("  [i] 已到底且再无新增，展开阶段结束")
                break
            scrolls += 1
            await asyncio.sleep(0.4)
        await asyncio.sleep(0.1)

    _f(f"\n阶段 1 完成：共点击 {total_clicks} 次，下滚 {scrolls} 屏，"
       f"耗时 {time.time() - t0:.0f}s")
    _f(f"  被展开过的主楼数（按昵称计）: {len(per_owner)}")
    top = sorted(per_owner.items(), key=lambda x: -x[1])[:12]
    _f(f"  展开次数最多的主楼: {top}")
    return {"clicks": total_clicks, "scrolls": scrolls, "owners": len(per_owner)}


async def phase3_extract(scraper, page, expected_count):
    """回滚到第一条，重新识别一遍。

    展开阶段结束后，所有评论（含回复）都已经渲染在 DOM 里、不会再被回收
    （实测 dom_items 单调增到 757 后保持不变），所以这里只需要**单趟**读取，
    不必再边滚边抽 —— 后者在"某条主楼挂了 170+ 条回复"时会克隆出巨型子树，
    150 趟能把 CPU 拖死。
    """
    _f("\n" + "=" * 62)
    _f("阶段 3：回滚到第一条，重新识别（DOM 抽取，单趟）")
    _f("=" * 62)
    await scraper._scroll_comment_container(page, 0)
    await asyncio.sleep(1.5)
    dom_items = await scraper._count_comment_items(page)
    _f(f"  已回滚到顶部，当前渲染的 comment-item 数 = {dom_items}")

    t0 = time.time()
    comments = await scraper._extract_all_comments(
        page, expected_count=expected_count, max_scrolls=1
    )
    _f(f"  DOM 抽取完成：{len(comments)} 条，耗时 {time.time() - t0:.0f}s")

    uniq = len({(n, t) for n, t in comments})
    empty = sum(1 for n, t in comments if not (t or "").strip())
    _f(f"  唯一(昵称,正文)组合 = {uniq}，空正文 = {empty}")
    return comments


async def main():
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])

    from scrapers.douyin import (
        DouyinScraper,
        DouyinCommentApiCollector,
        _VISIBLE_EXPAND_BUTTONS_JS,
    )

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

    report = {"url": URL}
    try:
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
        page.on("response", lambda r: asyncio.ensure_future(collector.add_response(r)))
        scraper._setup_video_capture(page)
        await scraper._install_media_pause_guard(page)

        await page.goto(URL, timeout=30000, wait_until="domcontentloaded")
        await scraper._pause_video(page)
        await asyncio.sleep(2)
        await scraper._dismiss_popups(page)
        await scraper._ensure_expected_video(page, URL, scraper._extract_video_id(page.url or URL))
        await asyncio.sleep(2)
        await scraper._click_comment_tab(page)
        await asyncio.sleep(3)

        expected_count = await scraper._get_expected_comment_count(page)
        _f(f"[页面显示评论总数] = {expected_count}")
        report["page_count"] = expected_count

        # 先让页面发出一次主楼接口请求，供拦截器认得 URL
        await scraper._scroll_comment_container_relative(page, 600)
        await asyncio.sleep(2)
        await scraper._scroll_comment_container(page, 0)
        await asyncio.sleep(1)

        exp = await phase1_expand(scraper, page, collector, _VISIBLE_EXPAND_BUTTONS_JS)
        report.update(exp)

        # 阶段 2：展开完静置，等接口请求收尾
        _f("\n阶段 2：静置等接口收尾 ...")
        for _ in range(20):
            await asyncio.sleep(0.5)
            if collector.pending_requests == 0:
                break
        await asyncio.sleep(2)

        # 阶段 3：回滚 + 识别
        comments = await phase3_extract(scraper, page, expected_count)
        report["dom_extracted"] = len(comments)
        report["dom_unique"] = len({(n, t) for n, t in comments})

        # 交叉对照：拦截到的接口数据
        top_n = len(collector.top_order)
        declared = sum(collector.reply_totals.values())
        loaded = sum(len(v) for v in collector.children.values())
        api_nodes = len(collector.nodes)

        reachable = top_n + declared
        _f("\n" + "=" * 62)
        _f("【结果汇总：UI 逐条展开 vs 接口侧】")
        _f("=" * 62)
        _f(f"页面显示的 total                    : {expected_count}")
        _f(f"UI 展开点击次数                     : {exp['clicks']}")
        _f(f"UI 展开覆盖到的主楼数(按昵称)        : {exp['owners']}")
        _f(f"DOM 识别出的评论条数                 : {len(comments)}")
        _f(f"接口侧唯一 cid 数（交叉对照）         : {api_nodes}")
        _f(f"  其中 主楼 {top_n} + 回复 {loaded}")
        _f(f"主楼自报回复总数之和                 : {declared}")
        _f(f"可达上限 = 主楼 + 全部回复           : {reachable}")
        _f(f"仍未拉完的线程数                     : {len(collector.pending_threads)}")
        gap = [f"{c}({t},实得{len(collector.children.get(c, []))})"
               for c, t in collector.reply_totals.items()
               if len(collector.children.get(c, [])) < t]
        _f(f"回复数对不上的线程                   : {len(gap)} {gap[:10]}")
        _f("=" * 62)

        report.update({
            "api_nodes": api_nodes,
            "api_top": top_n,
            "api_replies": loaded,
            "api_declared_replies": declared,
            "reachable": reachable,
            "pending_threads": len(collector.pending_threads),
            "mismatch_threads": len(gap),
        })

        debug_dir = Path("output/debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(debug_dir / "uiexpand_final.png"))
        (debug_dir / "_uiexpand_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (debug_dir / "_uiexpand_comments.json").write_text(
            json.dumps(comments, ensure_ascii=False, indent=2), encoding="utf-8")
        _f(f"\n>>> 报告: {debug_dir / '_uiexpand_report.json'}")
        _f(f">>> 评论: {debug_dir / '_uiexpand_comments.json'}")
    finally:
        await scraper.stop()


if __name__ == "__main__":
    asyncio.run(main())
