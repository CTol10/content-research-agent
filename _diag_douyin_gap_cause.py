"""追查抖音评论"缺口"的成因：主楼接口到底是不是真的穷尽了。

核心疑点：
  页面/接口 total=996，但我们只拿到 主楼269 + 回复474 = 743，差 253。
  两种可能：
    (a) total 是虚高计数器（含删除/审核/折叠/仅App可见）-> 抓不到是正常的
    (b) 主楼列表被"热度排序 + cursor 不稳定"提前截断（翻页出现重复项、has_more 提前变 0）
        -> 那是真 bug，还有 253 条主楼没拿到

判定方法：**从 cursor=0 独立爬两遍，比较两次的主楼集合**
  - 若 B 里有 A 没有的 cid  -> 翻页有损，269 不是全集 -> (b) 成立
  - 若 B ⊆ A              -> 269 就是服务端愿意给的全部主楼 -> (a) 成立

用法: python _diag_douyin_gap_cause.py [url]
"""
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.iesdouyin.com/share/video/7683754809844976948"

_LIST_RESPONSES = []      # 页面自己发出的 list 响应
_DETAIL_RESPONSES = []    # 视频详情（含 stat.comment_count）
_LIST_URL = {"tpl": None}


def _f(*a):
    print(*a, flush=True)


CRAWL_JS = r"""
async ([tpl, count, maxPages]) => {
    const seen = new Map();
    const pages = [];
    let cursor = 0;
    let total = null;
    let lastHasMore = null;
    for (let i = 0; i < maxPages; i++) {
        const u = new URL(tpl, location.origin);
        u.searchParams.set('cursor', String(cursor));
        u.searchParams.set('count', String(count));
        let j;
        try {
            const r = await fetch(u.toString(), { credentials: 'include' });
            j = await r.json();
        } catch (e) {
            pages.push({ cursor_sent: cursor, error: String(e) });
            break;
        }
        const cs = j.comments || [];
        if (total === null) total = j.total;
        let nNew = 0;
        const cids = [];
        for (const c of cs) {
            if (!c.cid) continue;
            cids.push(c.cid);
            if (!seen.has(c.cid)) { seen.set(c.cid, true); nNew++; }
        }
        lastHasMore = j.has_more;
        pages.push({
            cursor_sent: cursor, cursor_returned: j.cursor, n: cs.length,
            n_new: nNew, dup: cs.length - nNew, has_more: j.has_more,
            status_code: j.status_code, first_cid: cids[0] || null, last_cid: cids[cids.length-1] || null,
        });
        if (j.status_code !== 0 || !j.has_more || cs.length === 0) break;
        const nc = j.cursor;
        cursor = (typeof nc === 'number' && nc > cursor) ? nc : cursor + count;
        await new Promise(r => setTimeout(r, 200));
    }
    return { total, has_more: lastHasMore, pages, cids: [...seen.keys()] };
}
"""


async def main():
    from scrapers.douyin import DouyinScraper

    dist_profile = Path(__file__).parent / "dist" / "cookies" / "_browser_profile"
    if dist_profile.exists():
        DouyinScraper._USER_DATA_DIR = dist_profile
        _f(f"[i] 使用登录态 profile: {dist_profile}")

    async def on_response(resp):
        u = resp.url
        if "/aweme/v1/web/comment/list" not in u:
            if "/aweme/v1/web/aweme/detail" in u:
                try:
                    j = await resp.json()
                    st = ((j.get("aweme_detail") or {}).get("statistics") or {})
                    if st:
                        _DETAIL_RESPONSES.append(st)
                except Exception:
                    pass
            return
        if "/reply" in u:
            return
        if _LIST_URL["tpl"] is None:
            _LIST_URL["tpl"] = u
        try:
            j = await resp.json()
            _LIST_RESPONSES.append({
                "n": len(j.get("comments") or []), "cursor": j.get("cursor"),
                "has_more": j.get("has_more"), "total": j.get("total"),
                "status_code": j.get("status_code"),
            })
        except Exception:
            pass

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

        tpl = _LIST_URL["tpl"]
        if not tpl:
            _f("!! 没捕获到主楼接口 URL")
            return

        _f("\n===== 1. 页面自己发出的主楼接口 URL（含签名）=====")
        parts = urlparse(tpl)
        _f(f"path: {parts.path}")
        for k, v in parse_qs(parts.query).items():
            val = v[0]
            if len(val) > 90:
                val = val[:90] + f"...(len={len(val)})"
            _f(f"   {k} = {val}")

        _f("\n===== 2. 页面自己发出的 list 响应 =====")
        for r in _LIST_RESPONSES:
            _f("  " + json.dumps(r, ensure_ascii=False))

        if _DETAIL_RESPONSES:
            st = _DETAIL_RESPONSES[0]
            _f(f"\n===== 3. 视频详情 statistics =====")
            _f(f"   comment_count = {st.get('comment_count')}  "
               f"digg_count = {st.get('digg_count')}  share_count = {st.get('share_count')}")

        _f("\n===== 4. 第 1 遍：从 cursor=0 独立翻页（count=50）=====")
        a = await page.evaluate(CRAWL_JS, [tpl, 50, 80])
        for p in a["pages"]:
            _f("  " + json.dumps(p, ensure_ascii=False))
        _f(f"  -> 第1遍 唯一主楼 = {len(a['cids'])}  total={a['total']}  has_more={a['has_more']}")

        _f("\n===== 5. 第 2 遍：再次从 cursor=0 独立翻页 =====")
        b = await page.evaluate(CRAWL_JS, [tpl, 50, 80])
        for p in b["pages"]:
            _f("  " + json.dumps(p, ensure_ascii=False))
        _f(f"  -> 第2遍 唯一主楼 = {len(b['cids'])}  total={b['total']}  has_more={b['has_more']}")

        sa, sb = set(a["cids"]), set(b["cids"])
        only_b = sb - sa
        only_a = sa - sb
        _f("\n===== 6. 判定 =====")
        _f(f"  第1遍={len(sa)}  第2遍={len(sb)}  并集={len(sa|sb)}  交集={len(sa&sb)}")
        _f(f"  只有第2遍有(第2遍新增) = {len(only_b)}")
        _f(f"  只有第1遍有            = {len(only_a)}")
        _f(f"  total(接口自报)        = {a['total']}")
        _f(f"  主楼 + 回复声明总数     = {len(sa)} + 474")
        if only_b:
            _f("  >>> 结论: 翻页有损！还存在未被第1遍覆盖的主楼 -> 属于真缺口")
            _f("      示例 cid: " + ", ".join(list(only_b)[:8]))
        else:
            _f("  >>> 结论: 两次完全一致，269 就是服务端愿意给的全部主楼 -> 缺口来自 total 虚高")

        Path("output/debug").mkdir(parents=True, exist_ok=True)
        Path("output/debug/_gap_cause_report.json").write_text(
            json.dumps({"crawl_a": a["pages"], "crawl_b": b["pages"],
                        "only_b": list(only_b), "only_a": list(only_a),
                        "total": a["total"], "list_url": tpl},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        _f("\n>>> 明细已写入 output/debug/_gap_cause_report.json")
    finally:
        await scraper.stop()


if __name__ == "__main__":
    asyncio.run(main())
