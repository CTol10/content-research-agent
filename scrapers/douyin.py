import asyncio
import logging
import time
from urllib.parse import parse_qs, urlparse

import config
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

SCROLL_CONTAINER = 'main.ICt0G60V'

# 抖音评论接口（主楼列表 / 回复列表）
COMMENT_API_MARK = "/aweme/v1/web/comment/list"


class NoCommentsError(Exception):
    """Raised when a video has no comments."""


class DouyinCommentApiCollector:
    """采集抖音评论接口返回的评论。

    抖音网页版把评论全部放在两个接口里：
      - /aweme/v1/web/comment/list/        主楼评论（cursor 分页）
      - /aweme/v1/web/comment/list/reply/  某条评论的回复（comment_id + cursor 分页）

    直接读接口 JSON 比从 DOM 抽文本更可靠：不受虚拟列表回收、行数折叠
    (-webkit-line-clamp)、昵称/正文混淆类名等影响，还能拿到回复归属关系。
    """

    def __init__(self):
        self.nodes: dict[str, dict] = {}          # cid -> {nickname, text}
        self.children: dict[str, list[str]] = {}  # parent_cid -> [child_cid, ...]
        self.top_order: list[str] = []            # 主楼出现顺序
        self.reply_totals: dict[str, int] = {}    # 主楼 cid -> 官方回复总数
        self.pending_threads: set[str] = set()    # 官方还有下一页但没拉完的线程
        self.page_count = 0
        self.pending_requests = 0
        self.api_urls: dict[str, str] = {}        # "list"/"reply" -> 页面自己发出的接口 URL

    @staticmethod
    def _parse_comment(raw: dict) -> tuple[str, str, str, int]:
        cid = str(raw.get("cid") or "")
        user = raw.get("user") or {}
        nickname = (user.get("nickname") or "").strip()
        text = (raw.get("text") or "").strip()
        total = int(raw.get("reply_comment_total") or 0)
        return cid, nickname, text, total

    def note_request(self, url: str) -> None:
        if COMMENT_API_MARK not in url:
            return
        self.pending_requests += 1
        # 记下页面自己发出的接口 URL 作为"模板"：里面带着 a_bogus / msToken 等签名，
        # 后续只改 cursor / count / comment_id 复刻请求，服务端依然认（已实测）。
        key = "reply" if "/reply" in url else "list"
        self.api_urls.setdefault(key, url)

    def absorb_top(self, items: list[dict]) -> None:
        """吸收一条接口返回的主楼列表。"""
        for raw in items:
            cid = str(raw.get("cid") or "")
            if not cid:
                continue
            self.nodes.setdefault(cid, {
                "nickname": raw.get("nick") or "",
                "text": raw.get("text") or "",
            })
            if cid not in self.top_order:
                self.top_order.append(cid)
            total = int(raw.get("replies") or 0)
            if total:
                self.reply_totals[cid] = total

    def absorb_replies(self, parent_cid: str, items: list[dict], has_more: bool) -> None:
        """吸收一条接口返回的回复列表。"""
        bucket = self.children.setdefault(parent_cid, [])
        for raw in items:
            cid = str(raw.get("cid") or "")
            if not cid:
                continue
            self.nodes.setdefault(cid, {
                "nickname": raw.get("nick") or "",
                "text": raw.get("text") or "",
            })
            if cid not in bucket:
                bucket.append(cid)
        if has_more:
            self.pending_threads.add(parent_cid)
        else:
            self.pending_threads.discard(parent_cid)

    async def add_response(self, response) -> None:
        try:
            data = await response.json()
        except Exception:
            data = None
        finally:
            self.pending_requests = max(0, self.pending_requests - 1)

        if not isinstance(data, dict):
            return

        url = response.url
        is_reply = "/reply" in url
        parent_cid = ""
        if is_reply:
            query = parse_qs(urlparse(url).query)
            parent_cid = (query.get("comment_id") or [""])[0]

        comments = data.get("comments") or []
        has_more = bool(data.get("has_more"))
        self.page_count += 1

        for raw in comments:
            if not isinstance(raw, dict):
                continue
            cid, nickname, text, total = self._parse_comment(raw)
            if not cid:
                continue
            self.nodes.setdefault(cid, {"nickname": nickname, "text": text})
            if not is_reply:
                if cid not in self.top_order:
                    self.top_order.append(cid)
                if total:
                    self.reply_totals[cid] = total
            elif parent_cid:
                bucket = self.children.setdefault(parent_cid, [])
                if cid not in bucket:
                    bucket.append(cid)

        if is_reply and parent_cid:
            if has_more:
                self.pending_threads.add(parent_cid)
            else:
                self.pending_threads.discard(parent_cid)

    def to_comments(self) -> list[tuple[str, str]]:
        """按「主楼 -> 其回复 -> 回复的回复」层级顺序展开为 (昵称, 正文)。"""
        out: list[tuple[str, str]] = []
        emitted: set[str] = set()

        def walk(cid: str):
            if cid in emitted:
                return
            emitted.add(cid)
            node = self.nodes.get(cid)
            if node and (node["nickname"] or node["text"]):
                out.append((node["nickname"], node["text"]))
            for child in self.children.get(cid, []):
                walk(child)

        for cid in self.top_order:
            walk(cid)
        # 兜底：起点不在主楼里的（例如被折叠的一级回复）
        for cid in list(self.nodes.keys()):
            if cid not in emitted:
                walk(cid)
        return out

    @property
    def loaded_replies(self) -> int:
        return sum(len(v) for v in self.children.values())

    def missing_summary(self) -> list[tuple[str, int]]:
        """官方声明还有更多回复、但没能拉完的线程。"""
        return [(cid, self.reply_totals.get(cid, 0)) for cid in self.pending_threads]

    def incomplete_threads(self) -> list[tuple[str, int, int]]:
        """(cid, 已加载, 官方总数)：已加载 < 官方总数的线程。"""
        rows = []
        for cid, total in self.reply_totals.items():
            loaded = len(self.children.get(cid, []))
            if loaded < total:
                rows.append((cid, loaded, total))
        return sorted(rows, key=lambda r: r[1] - r[2])


# 找出当前视口内所有"展开N条回复 / 展开更多 / 点击加载更多"按钮，返回元素集合。
#
# 注意：这些控件虽然都是 <button>，但页面里的 element.click() 是无效的
# （抖音绑定的不是普通 click 语义），必须由 Playwright 派发真实鼠标事件才能展开。
# 另外只取"当前视口内"的按钮，避免一次性并发几百个请求被风控。
_VISIBLE_EXPAND_BUTTONS_JS = r"""
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
    const out = [];
    for (const btn of Array.from(document.querySelectorAll('button'))) {
        const text = norm(btn.textContent);
        if (!isExpand(text)) continue;
        const rect = btn.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) continue;
        if (rect.top < -4 || rect.top > vh - 4) continue;   // 只点视口内的
        out.push(btn);
    }
    return out;
}
"""

# 不限制视口：任意一个「展开N条回复」按钮即可（只为触发一次回复接口拿 URL 模板）
_ANY_EXPAND_BUTTON_JS = r"""
() => {
    const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const isExpand = (t) => t && !t.includes('收起') && !t.includes('全文')
        && (/^展开\s*\d+\s*条回复$/.test(t) || /^展开更多$/.test(t));
    const out = [];
    for (const btn of Array.from(document.querySelectorAll('button'))) {
        if (isExpand(norm(btn.textContent))) out.push(btn);
    }
    return out;
}
"""

# 直接用页面上下文 fetch 复刻评论接口，按 cursor 翻页直到 has_more=0。
# 只要把页面自己发出的 URL 当作模板（带 a_bogus / msToken），
# 仅修改 cursor / count / comment_id 服务端依然照常返回 —— 比驱动 UI 快得多也完整得多。
_CRAWL_COMMENTS_JS = r"""
async ([mode, tpl, opts]) => {
    const count = opts.count || 50;
    const maxPages = opts.maxPages || 60;
    const seen = new Map();
    const pages = [];
    let cursor = 0;
    let total = null;
    let lastStatus = null;
    let lastHasMore = null;
    for (let i = 0; i < maxPages; i++) {
        const u = new URL(tpl, location.origin);
        if (mode === 'reply') {
            if (opts.cid) u.searchParams.set('comment_id', opts.cid);
            if (opts.itemId) u.searchParams.set('item_id', opts.itemId);
        }
        u.searchParams.set('cursor', String(cursor));
        u.searchParams.set('count', String(count));
        let j;
        try {
            const r = await fetch(u.toString(), { credentials: 'include' });
            j = await r.json();
        } catch (e) {
            pages.push({ cursor, error: String(e) });
            break;
        }
        lastStatus = j.status_code;
        const cs = j.comments || [];
        if (total === null) total = j.total;
        for (const c of cs) {
            if (!c.cid || seen.has(c.cid)) continue;
            seen.set(c.cid, {
                cid: c.cid,
                text: c.text || '',
                nick: ((c.user || {}).nickname || ''),
                replies: c.reply_comment_total || 0,
            });
        }
        const hasMore = j.has_more;
        lastHasMore = hasMore;
        pages.push({ cursor, n: cs.length, has_more: hasMore, status_code: lastStatus });
        if (lastStatus !== 0 || !hasMore || cs.length === 0) break;
        const nc = j.cursor;
        cursor = (typeof nc === 'number' && nc > cursor) ? nc : cursor + count;
        await new Promise(r => setTimeout(r, 180));
    }
    return { total, status_code: lastStatus, has_more: lastHasMore, pages, list: [...seen.values()] };
}
"""


class DouyinScraper(BaseScraper):
    platform_name = "douyin"
    CDP_PORT = 9233

    def _get_login_url(self) -> str:
        return "https://www.douyin.com"

    async def scrape(self, url: str) -> dict:
        context = await self.new_context()
        page = await context.new_page()
        collector = DouyinCommentApiCollector()

        try:
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            # 拦截评论接口：主楼 + 回复的真实数据从这里拿，DOM 抽取只作兜底
            page.on("request", lambda r: collector.note_request(r.url))
            page.on("response", lambda r: asyncio.ensure_future(collector.add_response(r)))
            self._setup_video_capture(page)
            await self._install_media_pause_guard(page)
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
            await self._pause_video(page)
            await asyncio.sleep(2)
            expected_video_id = self._extract_video_id(page.url or url)

            await self._dismiss_popups(page)
            await self._ensure_expected_video(page, url, expected_video_id)
            await self._pause_video(page)
            await asyncio.sleep(3)

            # Check if this is an image/text post (hideXgVideo indicates non-video content)
            is_image_post = await page.evaluate("""
                () => {
                    const xgContainer = document.querySelector('.xg-video-container');
                    if (xgContainer && xgContainer.classList.contains('hideXgVideo')) {
                        return true;
                    }
                    // Also check for note-detail which indicates image/text post
                    if (document.querySelector('[data-e2e="note-detail"]')) {
                        return true;
                    }
                    return false;
                }
            """)

            if is_image_post:
                logger.info(f"[douyin] Image/text post detected (hideXgVideo or note-detail), skipping video extraction")
            else:
                await self._check_video_element(page)
                await self._try_extract_video_from_dom(page)
                await self._ensure_expected_video(page, url, expected_video_id)
                # Re-extract after potential page reload in _ensure_expected_video
                await self._check_video_element(page)
                await self._try_extract_video_from_dom(page)

            post_content = ""
            try:
                post_content = await self._extract_post_content(page)
            except Exception as e:
                logger.warning(f"[douyin] Failed to extract post content: {e}")

            await self._click_comment_tab(page)
            await asyncio.sleep(3)

            no_comments = await page.evaluate(
                '() => document.body.innerText.includes(\'暂无评论\')'
            )
            if no_comments:
                raise NoCommentsError("暂无评论")

            expected_count = await self._get_expected_comment_count(page)
            if expected_count:
                logger.info(f"[douyin] Expected comment count: {expected_count}")

            # 首选：直接按 cursor 调评论接口翻页（快且完整，不受虚拟列表/点击限制）
            api_ok = False
            try:
                api_ok = await self._crawl_comments_via_api(
                    page, collector, self._extract_video_id(page.url or url)
                )
            except Exception as e:
                logger.warning(f"[douyin] 接口直连异常，回退 UI 抓取: {e}")

            # 兜底：滚动 + 点开所有「展开更多」，评论从拦截到的接口响应里取
            if not api_ok:
                await self._scroll_to_load_all(page, expected_count=expected_count)
                await asyncio.sleep(2)
                await self._expand_all_replies(
                    page, collector=collector, expected_count=expected_count
                )
                await asyncio.sleep(1)

            comments = collector.to_comments()
            logger.info(
                f"[douyin] 接口评论: {len(comments)} 条 "
                f"(主楼 {len(collector.top_order)} + 回复 {collector.loaded_replies})"
            )

            if not comments:
                comments = await self._extract_all_comments(page, expected_count=expected_count)
                logger.info(f"[douyin] DOM 抽取兜底: {len(comments)} 条")

            if collector.reply_totals:
                reachable = len(collector.top_order) + sum(collector.reply_totals.values())
                logger.info(
                    f"[douyin] 可达上限估算 = 主楼 {len(collector.top_order)} + "
                    f"全部回复 {sum(collector.reply_totals.values())} = {reachable} "
                    f"(页面显示 {expected_count or '?'})"
                )

            logger.info(f"[douyin] Total comments extracted: {len(comments)}")

            debug_dir = config.OUTPUT_DIR / "debug"
            debug_dir.mkdir(parents=True, exist_ok=True)
            serial = url.split("/")[-1].split("?")[0]
            await page.screenshot(path=str(debug_dir / f"douyin_{serial}_final.png"), full_page=False)

            # Only return video_url for actual video posts
            video_url = None if is_image_post else self._get_primary_video_url()
            audio_url = None if is_image_post else self._get_primary_audio_url()
            return {"post_content": post_content, "comments": comments, "video_url": video_url, "audio_url": audio_url}

        except Exception as e:
            logger.error(f"[douyin] Failed to scrape {url}: {e}")
            raise
        finally:
            await page.close()

    @staticmethod
    async def _get_scroll_container_js():
        """Return JavaScript that finds the comment scroll container."""
        return """
            () => {
                const getContainer = () => {
                    const list = document.querySelector('[data-e2e="comment-list"]');
                    const firstItem = document.querySelector('[data-e2e="comment-item"]');
                    const seed = list || firstItem;
                    if (seed) {
                        let el = seed;
                        while (el && el !== document.body) {
                            const cs = window.getComputedStyle(el);
                            const oy = cs.overflowY;
                            if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 20) {
                                return el;
                            }
                            el = el.parentElement;
                        }
                    }
                    for (const cmc of document.querySelectorAll('.comment-mainContent')) {
                        if (cmc.scrollHeight > cmc.clientHeight + 20) return cmc;
                    }
                    return document.querySelector('div.route-scroll-container') ||
                           document.querySelector('main.ICt0G60V');
                };
                return getContainer();
            }
        """

    async def _scroll_comment_container(self, page, position: int):
        """Scroll comment container to absolute position."""
        js = await self._get_scroll_container_js()
        await page.evaluate(f"""
            {js.replace('return getContainer();', f'''
                const c = getContainer();
                if (c) c.scrollTop = {position};
                return c;
            ''')}
        """)

    async def _scroll_comment_container_relative(self, page, delta: int) -> bool:
        """Scroll comment container by delta pixels. Returns True if scrolled."""
        js = await self._get_scroll_container_js()
        result = await page.evaluate(f"""
            {js.replace('return getContainer();', f'''
                const c = getContainer();
                if (!c) return false;
                const before = c.scrollTop;
                c.scrollTop += {delta};
                return c.scrollTop > before + 5;
            ''')}
        """)
        return bool(result)

    async def _scroll_to_load_all(self, page, max_scrolls=120, expected_count: int | None = None):
        stuck_count = 0
        prev_items = 0
        for i in range(max_scrolls):
            state = await page.evaluate(f"""
                () => {{
                    const getContainer = () => {{
                        const list = document.querySelector('[data-e2e="comment-list"]');
                        const firstItem = document.querySelector('[data-e2e="comment-item"]');
                        const seed = list || firstItem;
                        if (seed) {{
                            let el = seed;
                            while (el && el !== document.body) {{
                                const cs = window.getComputedStyle(el);
                                const oy = cs.overflowY;
                                if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 20) {{
                                    return el;
                                }}
                                el = el.parentElement;
                            }}
                        }}
                        // Fallback: comment-mainContent (image/text posts have a dedicated
                        // scrollable .comment-mainContent with overflow:scroll)
                        for (const cmc of document.querySelectorAll('.comment-mainContent')) {{
                            if (cmc.scrollHeight > cmc.clientHeight + 20) return cmc;
                        }}
                        return document.querySelector('div.route-scroll-container') || document.querySelector('{SCROLL_CONTAINER}');
                    }};

                    const container = getContainer();
                    if (!container) return {{ moved: false, atBottom: true }};
                    const beforeTop = container.scrollTop;
                    container.scrollTop = beforeTop + Math.max(300, Math.floor(container.clientHeight * 0.9));
                    const afterTop = container.scrollTop;
                    const atBottom = container.scrollTop >= container.scrollHeight - container.clientHeight - 10;
                    return {{ moved: afterTop > beforeTop + 5, atBottom }};
                }}
            """)

            current_items = await page.evaluate("() => document.querySelectorAll('[data-e2e=\"comment-item\"]').length")
            if expected_count and current_items >= expected_count:
                break

            if (not state.get("moved")) and current_items <= prev_items:
                stuck_count += 1
                if stuck_count >= 6:
                    break
            else:
                stuck_count = 0
            prev_items = max(prev_items, current_items)
            await asyncio.sleep(1.2)

    async def _capture_reply_url_template(self, page, collector) -> bool:
        """点开任意一个「展开N条回复」按钮，捕获回复接口 URL 模板（带签名）。

        回复接口的 URL 没法靠主楼 URL 改路径造出来（实测会一直挂住），
        但用它自己的签名去换 comment_id / cursor 是可行的，所以只需要触发一次。
        """
        for _ in range(10):
            try:
                handles = await page.evaluate_handle(_ANY_EXPAND_BUTTON_JS)
                count = await handles.evaluate("(arr) => arr.length")
            except Exception:
                return False
            if count:
                try:
                    handle = await handles.evaluate_handle("(arr) => arr[0]")
                    element = handle.as_element()
                    if element is not None:
                        await element.click(force=True, timeout=5000)
                        await self._wait_api_settle(collector, timeout=5.0)
                except Exception:
                    pass
                if collector.api_urls.get("reply"):
                    logger.info("[douyin] 已捕获回复接口 URL 模板")
                    return True
                return False
            if not await self._scroll_comment_container_relative(page, 700):
                break
            await asyncio.sleep(0.6)
        return False

    async def _crawl_comments_via_api(self, page, collector, video_id: str) -> bool:
        """直接用评论接口翻页抓全量评论（主楼 + 回复）。成功返回 True。"""
        list_tpl = collector.api_urls.get("list")
        if not list_tpl:
            logger.warning("[douyin] 未捕获到评论接口 URL，回退 UI 抓取")
            return False

        query = parse_qs(urlparse(list_tpl).query)
        video_id = (query.get("aweme_id") or [""])[0] or video_id

        top = await page.evaluate(
            _CRAWL_COMMENTS_JS, ["list", list_tpl, {"count": 50, "maxPages": 60}]
        )
        if top.get("status_code") != 0 or not top.get("list"):
            logger.warning(
                f"[douyin] 主楼接口直连失败 (status={top.get('status_code')})，回退 UI 抓取"
            )
            return False

        collector.absorb_top(top["list"])
        logger.info(
            f"[douyin] 主楼接口直连: {len(top['list'])} 条 "
            f"({len(top.get('pages') or [])} 页, 官方 total={top.get('total')})"
        )

        threads = [c for c in top["list"] if c.get("replies")]
        if not threads:
            return True

        if not collector.api_urls.get("reply"):
            await self._capture_reply_url_template(page, collector)
        reply_tpl = collector.api_urls.get("reply")
        if not reply_tpl:
            logger.warning("[douyin] 未捕获到回复接口 URL，改由 UI 展开补回复")
            await self._expand_all_replies(page, collector=collector, expected_count=None)
            return True

        loaded_threads = 0
        loaded_replies = 0
        for item in threads:
            try:
                res = await page.evaluate(
                    _CRAWL_COMMENTS_JS,
                    ["reply", reply_tpl, {"cid": item["cid"], "itemId": video_id,
                                          "count": 50, "maxPages": 60}],
                )
            except Exception as e:
                logger.warning(f"[douyin] 回复直连异常 cid={item['cid']}: {e}")
                continue
            if res.get("status_code") == 0 and res.get("list") is not None:
                collector.absorb_replies(item["cid"], res["list"], bool(res.get("has_more")))
                loaded_threads += 1
                loaded_replies += len(res["list"])
            else:
                logger.warning(
                    f"[douyin] 回复直连失败 cid={item['cid']} status={res.get('status_code')}"
                )
            await asyncio.sleep(0.15)

        for item in threads:
            collector.reply_totals.setdefault(item["cid"], int(item.get("replies") or 0))

        logger.info(
            f"[douyin] 回复接口直连: {loaded_threads}/{len(threads)} 个线程, "
            f"{loaded_replies} 条回复 (官方声明共 {sum(collector.reply_totals.values())} 条)"
        )
        return True

    async def _count_comment_items(self, page) -> int:
        try:
            return await page.evaluate(
                "() => document.querySelectorAll('[data-e2e=\"comment-item\"]').length"
            )
        except Exception:
            return 0

    async def _wait_api_settle(self, collector, timeout: float = 6.0) -> None:
        """等评论接口不再有新请求（或超时）。"""
        if collector is None:
            await asyncio.sleep(1.5)
            return
        deadline = time.monotonic() + timeout
        last = -1
        stable = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(0.3)
            cur = collector.page_count + collector.pending_requests
            if cur == last:
                stable += 1
                if stable >= 2:
                    return
            else:
                stable = 0
                last = cur

    async def _click_visible_expand_buttons(self, page) -> int:
        """点击当前视口内所有「展开N条回复 / 展开更多」按钮，返回成功点击次数。

        必须用 Playwright 的真实鼠标事件：页面内 element.click() 对抖音这些
        控件无效（实测点击 60 轮、每轮 61 个按钮，一个回复都没展开）。
        """
        try:
            handles = await page.evaluate_handle(_VISIBLE_EXPAND_BUTTONS_JS)
            count = await handles.evaluate("(arr) => arr.length")
        except Exception:
            return 0

        clicked = 0
        for i in range(count):
            try:
                handle = await handles.evaluate_handle(f"(arr) => arr[{i}]")
                element = handle.as_element()
                if element is None:
                    continue
                await element.click(force=True, timeout=3000)
                clicked += 1
                await asyncio.sleep(0.5)
            except Exception:
                continue
        return clicked

    async def _expand_all_replies(self, page, collector=None, expected_count: int | None = None):
        """展开全部「展开N条回复 / 展开更多」，直到列表里再无展开控件。

        关键修复：旧实现用 (按钮文案 + 昵称) 作为"已点击"去重键，导致同一条主评论
        下重复出现的「展开更多」被判定为"已点过"直接跳过 —— 每个回复线程只翻到第
        1~2 页就停了（接口 has_more=1 但再没人去点）。
        现在改为纯进度驱动：不记录"点过谁"，只要当前视口还有展开控件就继续点，
        点击成功后抖音自己的按钮形态会变（展开N条回复 -> 展开更多 -> 收起/消失），
        天然收敛。
        """
        total_clicked = 0
        passes = 0

        for full_pass in range(8):
            passes = full_pass + 1
            await self._scroll_comment_container(page, 0)
            await asyncio.sleep(1.0)
            pass_clicked = 0
            prev_items = await self._count_comment_items(page)

            # 一趟 = 从列表顶部一路滚到底，边走边点。
            # 注意：中间"没有展开控件"的区段非常常见（已展开过的线程），
            # 绝不能因为某一屏没点着东西就提前结束这一趟 —— 必须滚到底为止。
            for _ in range(600):
                # 在当前屏反复点，直到这一屏再没有展开控件
                round_clicked = 0
                for _ in range(25):
                    n = await self._click_visible_expand_buttons(page)
                    if n == 0:
                        break
                    round_clicked += n
                    pass_clicked += n
                    total_clicked += n
                    await self._wait_api_settle(collector, timeout=6.0)

                prev_items = max(prev_items, await self._count_comment_items(page))

                # 滚不动了 = 到列表底部。本屏如果刚点开过东西（列表被顶长），
                # 就再给一轮机会，直到"滚不动 + 没点着"才算这一趟结束。
                moved = await self._scroll_comment_container_relative(page, 600)
                if not moved and round_clicked == 0:
                    break
                await asyncio.sleep(0.3)

            logger.info(
                f"[douyin] Pass {passes}: clicked {pass_clicked} buttons, "
                f"dom_items={prev_items}"
                + (
                    f", api_loaded={collector.page_count} pages"
                    f" (top={len(collector.top_order)}, replies={collector.loaded_replies})"
                    if collector is not None else ""
                )
            )
            if pass_clicked == 0:
                break

        logger.info(f"[douyin] Expanded {total_clicked} reply sections (total clicks)")

        if collector is not None:
            missing = collector.missing_summary()
            if missing:
                logger.warning(
                    f"[douyin] {len(missing)} 个回复线程 has_more=1 未拉完: "
                    + ", ".join(f"{cid}({total})" for cid, total in missing[:10])
                )
            incomplete = collector.incomplete_threads()
            if incomplete:
                logger.warning(
                    f"[douyin] {len(incomplete)} 个回复线程没展开干净 (已加载/官方): "
                    + ", ".join(f"{cid}({got}/{tot})" for cid, got, tot in incomplete[:10])
                )
            else:
                logger.info("[douyin] 所有有回复的主楼线程都已展开完整")
            if expected_count:
                logger.info(
                    f"[douyin] 接口已抓取 {len(collector.nodes)} 条 / 页面显示 {expected_count} 条"
                )

    async def _extract_all_comments(
        self,
        page,
        expected_count: int | None = None,
        max_scrolls: int = 150,
    ) -> list[tuple[str, str]]:
        await page.evaluate(f"""
            () => {{
                const getContainer = () => {{
                    const list = document.querySelector('[data-e2e="comment-list"]');
                    const firstItem = document.querySelector('[data-e2e="comment-item"]');
                    const seed = list || firstItem;
                    if (seed) {{
                        let el = seed;
                        while (el && el !== document.body) {{
                            const cs = window.getComputedStyle(el);
                            const oy = cs.overflowY;
                            if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 20) {{
                                return el;
                            }}
                            el = el.parentElement;
                        }}
                    }}
                    const cmc = document.querySelector('.comment-mainContent');
                    if (cmc && cmc.scrollHeight > cmc.clientHeight + 20) return cmc;
                    return document.querySelector('div.route-scroll-container') || document.querySelector('{SCROLL_CONTAINER}');
                }};
                const container = getContainer();
                if (container) container.scrollTop = 0;
            }}
        """)
        await asyncio.sleep(1)

        all_comments = []
        seen = set()
        stuck_count = 0

        # Override CSS line-clamp so innerText returns full text
        await page.evaluate("""
            () => {
                const style = document.createElement('style');
                style.textContent = '[data-e2e="comment-item"] * { -webkit-line-clamp: unset !important; max-height: none !important; overflow: visible !important; text-overflow: clip !important; }';
                document.head.appendChild(style);
            }
        """)

        for _ in range(max_scrolls):
            items = await page.evaluate("""
                () => {
                    const items = document.querySelectorAll('[data-e2e="comment-item"]');
                    const normalize = (text) => (text || '').replace(/\\u00a0/g, ' ').trim();

                    // 通用 content 抽取：不依赖混淆类名。
                    // isMeta: 判一个元素文本是否是纯元数据（时间戳/地点/点赞数/按钮/省略号）
                    const isMeta = (t) => {
                        if (!t) return true;
                        if (/^\\d+$/.test(t)) return true;                                    // 纯数字（点赞数）
                        if (/^\\.{2,}$/.test(t)) return true;                                  // "..." 等省略号（更多菜单残留）
                        if (/^(刚刚|\\d+\\s*(分钟|秒钟|秒|分|小时|天|周|月|年)前)(\\s*[·•]\\s*\\S+)?$/.test(t)) return true;  // 时间戳
                        if (/^[·•]\\s*\\S+$/.test(t)) return true;                             // 单独地点
                        if (/^(展开|收起|分享|回复|查看更多|点赞|更多|置顶|作者|播放中|IP属地)[:：]?\\S*$/.test(t)) return true;
                        return false;
                    };
                    // extractContent: 通用兜底——不假设正文在哪一级、不碰混淆类名。
                    //   1) 若抖音给正文加了稳定 data-e2e，优先用它；
                    //   2) 否则克隆后剥掉"已知非正文元素"（头像/stats/昵称/更多菜单/回复容器/按钮/svg/img/链接/嵌套评论），
                    //      再剥掉整段是元数据的 div/span，取剩余文本。只依赖稳定选择器与文本语义。
                    const extractContent = (item, nickname) => {
                        const byE2e = item.querySelector(
                            '[data-e2e="comment-content"], [data-e2e="comment-desc"], ' +
                            '[data-e2e="comment-text"], [data-e2e="comment-text-content"]'
                        );
                        if (byE2e) {
                            const t = normalize(byE2e.textContent || '');
                            if (t && !isMeta(t)) return t;
                        }
                        const clone = item.cloneNode(true);
                        clone.querySelectorAll('[data-e2e="comment-item"]').forEach(el => { if (el !== clone) el.remove(); });
                        clone.querySelectorAll(
                            '.comment-item-avatar, .comment-item-stats-container, ' +
                            '[data-click-from="title"], [data-e2e="video-comment-more"], ' +
                            '[class*="replyContainer"], [class*="reply-container"], [class*="reply_wrap"], ' +
                            'svg, button, img, a'
                        ).forEach(el => el.remove());
                        clone.querySelectorAll('div, span').forEach(el => {
                            if (isMeta(normalize(el.textContent || ''))) el.remove();
                        });
                        let text = normalize(clone.textContent || '');
                        text = text.replace(/\\s*\\d+\\s*(分钟|秒钟|秒|分|小时|天|周|月|年)前(\\s*[·•]\\s*\\S+)?/g, '').trim();
                        text = text.replace(/\\s*[·•]\\s*\\S+$/g, '').trim();
                        if (nickname && text === nickname) return '';
                        return text;
                    };

                    return Array.from(items).map(item => {
                        const nickEl = item.querySelector('[data-click-from="title"]');
                        const nickname = nickEl ? normalize(nickEl.textContent || '') : '';
                        const content = extractContent(item, nickname);

                        // Find parent comment for reply items (nested inside .replyContainer)
                        let parentRef = '';
                        const replyContainer = item.closest(
                            '.replyContainer, [class*="replyContainer"], ' +
                            '[class*="reply-container"], [class*="reply_wrap"]'
                        );
                        if (replyContainer) {
                            const parentItem = replyContainer.closest('[data-e2e="comment-item"]');
                            if (parentItem && parentItem !== item) {
                                const pNickEl = parentItem.querySelector('[data-click-from="title"]');
                                const pNick = pNickEl ? normalize(pNickEl.textContent || '') : '';
                                const pContent = extractContent(parentItem, pNick);
                                if (pNick || pContent) {
                                    parentRef = pNick + ':' + pContent.substring(0, 30);
                                }
                            }
                        }

                        return { nickname, content, parentRef };
                    }).filter(c => c.nickname || c.content);
                }
            """)

            before_count = len(seen)
            for item in items:
                key = item['nickname'] + '::' + item['content'][:50]
                if item.get('parentRef'):
                    key += '@@' + item['parentRef']
                if key not in seen:
                    seen.add(key)
                    all_comments.append((item['nickname'], item['content']))

            if expected_count and len(seen) >= expected_count:
                break

            state = await page.evaluate(f"""
                () => {{
                    const getContainer = () => {{
                        const list = document.querySelector('[data-e2e="comment-list"]');
                        const firstItem = document.querySelector('[data-e2e="comment-item"]');
                        const seed = list || firstItem;
                        if (seed) {{
                            let el = seed;
                            while (el && el !== document.body) {{
                                const cs = window.getComputedStyle(el);
                                const oy = cs.overflowY;
                                if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 20) {{
                                    return el;
                                }}
                                el = el.parentElement;
                            }}
                        }}
                        const cmc = document.querySelector('.comment-mainContent');
                        if (cmc && cmc.scrollHeight > cmc.clientHeight + 20) return cmc;
                        return document.querySelector('div.route-scroll-container') || document.querySelector('{SCROLL_CONTAINER}');
                    }};

                    const container = getContainer();
                    if (!container) return {{ moved: false, atBottom: true }};
                    const beforeTop = container.scrollTop;
                    container.scrollTop = beforeTop + Math.max(300, Math.floor(container.clientHeight * 0.9));
                    const afterTop = container.scrollTop;
                    const atBottom = container.scrollTop >= container.scrollHeight - container.clientHeight - 10;
                    return {{ moved: afterTop > beforeTop + 5, atBottom }};
                }}
            """)

            if (not state.get("moved")) and len(seen) == before_count:
                stuck_count += 1
                if stuck_count >= 6:
                    break
            else:
                stuck_count = 0
            await asyncio.sleep(0.8)

        return all_comments

    async def _get_expected_comment_count(self, page) -> int | None:
        try:
            return await page.evaluate("""
                () => {
                    const candidates = [];
                    const icon = document.querySelector('[data-e2e="feed-comment-icon"]');
                    if (icon) candidates.push((icon.innerText || '').trim());

                    const els = Array.from(document.querySelectorAll('span,div,button'));
                    for (const el of els) {
                        const t = (el.innerText || '').trim();
                        if (!t) continue;
                        const m = t.match(/评论\\s*\\(?\\s*(\\d+)\\s*\\)?/);
                        if (m) candidates.push(m[1]);
                    }
                    for (const raw of candidates) {
                        const n = parseInt(raw, 10);
                        if (!Number.isNaN(n) && n > 0) return n;
                    }
                    return null;
                }
            """)
        except Exception:
            return None

    async def _dismiss_popups(self, page):
        for _ in range(5):
            dismissed = False
            for selector in [
                'button:has-text("暂不")', 'button:has-text("不保存")',
                'button:has-text("取消")', 'button:has-text("关闭")',
            ]:
                try:
                    btn = page.locator(selector).first
                    if await btn.count() > 0 and await btn.is_visible():
                        await btn.click()
                        dismissed = True
                        await asyncio.sleep(1)
                        break
                except Exception:
                    continue
            if dismissed:
                break
            await asyncio.sleep(1)

        await page.evaluate("""
            document.querySelectorAll('button').forEach(btn => {
                const text = btn.textContent || '';
                if (text.includes('暂不') || text.includes('不保存') || text.includes('取消')) {
                    btn.click();
                }
            });
        """)
        await asyncio.sleep(1)

    async def _click_comment_tab(self, page):
        comment_tab_clicked = False
        for selector in [
            '[data-e2e="feed-comment-icon"]',
            'div[data-e2e="feed-comment-icon"]',
            'span[data-e2e="feed-comment-icon"]',
        ]:
            try:
                tab = page.locator(selector).first
                if await tab.count() > 0 and await tab.is_visible():
                    await tab.click()
                    comment_tab_clicked = True
                    await asyncio.sleep(2)
                    break
            except Exception:
                continue

        for count in range(10):
            pattern = f'评论({count})'
            try:
                tab = page.locator(f'div:has-text("{pattern}")').first
                if await tab.count() > 0 and await tab.is_visible():
                    await tab.click()
                    comment_tab_clicked = True
                    await asyncio.sleep(2)
                    break
            except Exception:
                continue

        if not comment_tab_clicked:
            try:
                tab = page.locator('div:has-text("评论")').first
                if await tab.count() > 0 and await tab.is_visible():
                    await tab.click()
                    comment_tab_clicked = True
                    await asyncio.sleep(2)
            except Exception:
                pass

    async def _extract_post_content(self, page) -> str:
        """Extract post content from the video info area."""
        # Step 1: Click 展开 to reveal full content（图文帖无 note-detail，按文字找展开按钮）
        await page.evaluate("""
            () => {
                const btns = document.querySelectorAll('button, [class*="expand"], [class*="more"]');
                for (const btn of btns) {
                    const text = (btn.innerText || '').trim();
                    if (text === '展开') {  // 精确匹配，避开评论的"展开N条回复"
                        const rect = btn.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) { btn.click(); return; }
                    }
                }
            }
        """)
        await asyncio.sleep(0.5)

        # Step 2: Extract content with updated selectors
        content = await page.evaluate("""
            () => {
                const normalize = (text) => text
                    .replace(/\\u00a0/g, ' ')
                    .replace(/[ \\t\\f\\v]+/g, ' ')
                    .replace(/ *\\n+ */g, '\\n')
                    .trim();

                // 优先：Open Graph meta 标签（最稳定，不依赖 DOM 结构）
                const ogDesc = document.querySelector('meta[property="og:description"]');
                if (ogDesc) {
                    const text = normalize(ogDesc.getAttribute('content') || '');
                    if (text.length >= 3) return text;
                }
                const metaDesc = document.querySelector('meta[name="description"]');
                if (metaDesc) {
                    const text = normalize(metaDesc.getAttribute('content') || '');
                    if (text.length >= 3) return text;
                }

                // Primary: note-detail (new Douyin layout)
                const noteDetail = document.querySelector('[data-e2e="note-detail"]');
                if (noteDetail) {
                    // 通用提取：不依赖混淆类名。
                    // 策略：以"发布时间"为锚点，往前找紧邻的正文段落。
                    // 正文通常是发布时间前面那个 10-500 字、不含元数据的纯文本段落。
                    const fullText = normalize(noteDetail.textContent || '');
                    // 移除"发布时间：..."及之后的内容
                    const beforeTime = fullText.replace(/\\n?发布时间：.*$/s, '').trim();
                    // 按行切分
                    const lines = beforeTime.split('\\n').map(l => l.trim()).filter(l => l.length > 0);
                    // 从后往前找：紧邻"发布时间"之前的长文本行（正文段落）
                    let postText = '';
                    for (let i = lines.length - 1; i >= 0; i--) {
                        const line = lines[i];
                        // 跳过明显的元数据/控件/评论/统计数字
                        if (/(粉丝|关注|获赞|播放|进入全屏|截图|字幕|不开启|重播|点击按住|收藏|分享|转发|举报|大家都在搜|全部评论|暂时没有更多评论|条回复|友善评论|登录后)/.test(line)) continue;
                        if (/^(登录|注册|打开)$/.test(line)) continue;
                        if (/^\\d+$/.test(line)) continue;                                              // 纯数字（点赞数等）
                        if (/^\\d+\\s*(收藏|分享|赞|评论)$/.test(line)) continue;                       // "42收藏"
                        if (/^\\d{1,2}:\\d{2}(\\s*\\/\\s*\\d{1,2}:\\d{2})?$/.test(line)) continue;     // 视频时长 "24:14" 或 "24:14/0:30"
                        if (/^\\d+\\/\\d+$/.test(line)) continue;                                       // 分页 "1/4" "241471/4"
                        if (/^(\\d+\\s*)+\\/\\d+$/.test(line)) continue;                                // "24 14 7 1/4" 等变异
                        if (/^\\d+周前|^\\d+天前|^\\d+小时前|^\\d+分钟前|^刚刚/.test(line)) continue;
                        if (/^[·•]\\s*\\S+$/.test(line)) continue;
                        if (['展开', '收起'].includes(line)) continue;
                        // 正文：10-500 字的自然语言段落
                        if (line.length >= 10 && line.length <= 500) {
                            postText = line;
                            break;
                        }
                        // 短文本也可能是正文（如"无标题"），但如果 < 10 就继续向前找
                        if (line.length >= 3 && !postText) {
                            postText = line;
                        }
                    }
                    if (postText) return postText;

                    // 如果逐行解析为空，再尝试旧的混淆类名作为补充
                    const descDiv = noteDetail.querySelector('.D7OuYODi, .SAYsgcoF, [class*="desc"]');
                    if (descDiv) {
                        let t2 = normalize(descDiv.innerText || '');
                        t2 = t2.replace(/\\n?展开\\n?/g, '\\n').replace(/\\n?收起\\n?/g, '\\n');
                        t2 = t2.replace(/\\n发布时间：.*$/, '').trim();
                        t2 = t2.replace(/\\n+/g, ' ').trim();
                        if (t2.length >= 2) return t2;
                    }

                    const contentDiv = noteDetail.querySelector('.oEmB895Z');
                    if (contentDiv) {
                        const allText = contentDiv.innerText || '';
                        // Extract only the content part (after user info)
                        const lines = allText.split('\\n');
                        const contentLines = [];
                        let foundContent = false;
                        for (const line of lines) {
                            const trimmed = line.trim();
                            // Skip user info lines
                            if (trimmed.match(/^(粉丝|关注|获赞)/)) continue;
                            if (trimmed.match(/^\\d+$/)) continue;
                            if (['展开', '收起', '发布时间'].some(k => trimmed.includes(k))) continue;
                            if (trimmed.length > 10) foundContent = true;
                            if (foundContent) contentLines.push(trimmed);
                        }
                        let text = contentLines.join(' ').trim();
                        text = text.replace(/发布时间：.*$/, '').trim();
                        if (text.length >= 2) return text;
                    }
                }

                // Legacy selectors (fallback)
                const info = document.querySelector('[data-e2e="detail-video-info"]');
                if (info) {
                    const textDiv = info.firstElementChild;
                    if (textDiv) {
                        let text = normalize(textDiv.innerText || '');
                        if (text.startsWith('展开')) text = text.substring(2).trim();
                        text = text.replace(/\\d+举报.*$/, '').trim();
                        if (text.length >= 2) return text;
                    }
                }

                // More fallbacks
                const fallbacks = [
                    '[data-e2e="video-desc"]',
                    '[data-e2e="cover-age-title-container"]',
                    '.video-info-detail',
                ];
                for (const sel of fallbacks) {
                    const el = document.querySelector(sel);
                    if (!el) continue;
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) continue;
                    const text = normalize(el.innerText || '');
                    if (text.length >= 5) return text;
                }

                // 兜底：图文帖无固定类名——用"发布时间"文字做锚点，
                // 取它上方兄弟节点里的实质正文（跳过 stats/控制条/按钮文字）。
                // 类名无关，只靠"发布时间"这个稳定文字 + 文本语义。
                const SKIP_WORDS = ['举报','分享','收藏','点赞','转发','关注','不感兴趣',
                                    '查看更多','倍速','清屏','连播','循环播放','智能','静音','弹幕',
                                    '登录后即可','参与互动','条评论','说点什么','友善评论'];
                const isSkip = (t) => {
                    if (!t) return true;
                    if (SKIP_WORDS.some(w => t.includes(w))) return true;
                    if (/^(登录|注册|打开)(后)?(即可)?(参与|查看|发表)/.test(t)) return true;  // 登录类占位文本
                    const d = (t.match(/\\d/g) || []).length;
                    if (t.length > 0 && d / t.length > 0.5) return true;          // 多半是数字（点赞数等）
                    if (/^\\d{1,2}:\\d{2}\\s*\\/\\s*\\d{1,2}:\\d{2}/.test(t)) return true;  // 进度 00:15/00:16
                    return false;
                };
                const allLeaves = Array.from(document.querySelectorAll('span, p, div, time'));
                let timeLeaf = null;
                for (const el of allLeaves) {
                    if (el.childElementCount > 0) continue;
                    const t = (el.textContent || '').trim();
                    if (t.startsWith('发布时间') && t.length < 50) {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) { timeLeaf = el; break; }
                    }
                }
                if (timeLeaf) {
                    let node = timeLeaf, levels = 0;
                    while (node && levels < 5) {
                        const prev = node.previousElementSibling;
                        if (prev) {
                            const c = normalize(prev.innerText || prev.textContent || '')
                                .replace(/展开|收起/g, '').trim();
                            if (c.length >= 3 && !isSkip(c)) return c;
                        }
                        node = node.parentElement;
                        levels++;
                    }
                }

                return '';
            }
        """)
        if not content:
            logger.info("[douyin] No post content found, using [无正文]")
            return "[无正文]"
        # 过滤掉登录占位文本等非正文内容
        _bogus_patterns = [
            "登录后即可", "登录后参与", "参与互动讨论", "说点什么",
            "友善评论", "条评论", "还没有评论", "暂无评论",
            "评论",  # 单独的"评论"可能是按钮文字，但太短已在上层过滤
        ]
        if any(p in content for p in _bogus_patterns) and len(content) < 20:
            logger.info(f"[douyin] Post content looks like placeholder: {content[:50]}...")
            return "[无正文]"
        return content

    async def _pause_video(self, page):
        """Pause video playback immediately after opening the page."""
        media_state = await self._pause_all_media(page)
        if media_state.get("count", 0) <= 0:
            return
        if media_state.get("playing", 0) <= 0:
            logger.info("[douyin] Media pause guard stopped autoplay")
            return

        video_info = media_state.get("target")
        if video_info and not video_info.get("paused"):
            await page.mouse.click(video_info["x"], video_info["y"])
            logger.info(f"[douyin] Clicked video at ({video_info['x']}, {video_info['y']}) to pause")
            await asyncio.sleep(0.5)
            await self._pause_all_media(page)

    @staticmethod
    def _extract_video_id(raw_url: str) -> str:
        """Extract Douyin video id from a URL for same-video validation."""
        if not raw_url:
            return ""
        path = urlparse(raw_url).path
        parts = [part for part in path.split("/") if part]
        for idx, part in enumerate(parts):
            if part == "video" and idx + 1 < len(parts):
                return parts[idx + 1]
        return parts[-1] if parts else ""

    async def _ensure_expected_video(self, page, original_url: str, expected_video_id: str):
        """Reload the original URL if the page has auto-switched to another video."""
        if not expected_video_id:
            return
        current_video_id = self._extract_video_id(page.url)
        if not current_video_id or current_video_id == expected_video_id:
            return

        logger.warning(
            f"[douyin] Video auto-switched from {expected_video_id} to {current_video_id}, reloading original URL"
        )
        await page.goto(original_url, timeout=30000, wait_until="domcontentloaded")
        await self._pause_video(page)
        await asyncio.sleep(2)
