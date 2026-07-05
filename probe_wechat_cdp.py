"""探测微信 CDP 连接，列出可调试页面，抓取视频号页面 DOM 结构。

Usage:
    python probe_wechat_cdp.py                # 列出所有可调试页面
    python probe_wechat_cdp.py --dump-dom     # 列出页面 + 抓取第一个页面的 DOM
    python probe_wechat_cdp.py --page <index> # 指定页面索引进行 DOM 抓取
    python probe_wechat_cdp.py --watch        # 监听网络请求（等待用户手动打开视频号）
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

from playwright.async_api import async_playwright


CDP_URL = "http://127.0.0.1:9222"
OUTPUT_DIR = Path("output/debug")


async def list_targets(browser):
    """列出所有 CDP 可调试的页面 target。"""
    print("\n=== CDP 可调试页面列表 ===\n")
    all_pages = []
    for ctx_idx, context in enumerate(browser.contexts):
        for page_idx, page in enumerate(context.pages):
            url = page.url
            title = await page.title()
            all_pages.append((ctx_idx, page_idx, page, url, title))
            print(f"  [{len(all_pages)-1}] context={ctx_idx} page={page_idx}")
            print(f"      URL:   {url}")
            print(f"      Title: {title}")
            print()

    if not all_pages:
        print("  (没有找到可调试的页面)")
        print("  请在微信中打开一个视频号视频，然后重新运行此脚本。")

    return all_pages


async def dump_page_dom(page, index: int):
    """抓取指定页面的 DOM 结构并保存。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    url = page.url
    title = await page.title()
    print(f"\n=== 抓取页面 [{index}] DOM ===")
    print(f"  URL:   {url}")
    print(f"  Title: {title}")

    # 等待页面加载
    await asyncio.sleep(3)

    # 截图
    screenshot_path = OUTPUT_DIR / f"wechat_cdp_page_{index}.png"
    await page.screenshot(path=str(screenshot_path), full_page=False)
    print(f"  截图: {screenshot_path}")

    # 抓取完整 DOM
    html = await page.evaluate("document.documentElement.outerHTML")
    dom_path = OUTPUT_DIR / f"wechat_cdp_page_{index}.html"
    dom_path.write_text(html, encoding="utf-8")
    print(f"  DOM:  {dom_path} ({len(html)} bytes)")

    # 尝试找评论相关元素
    print("\n=== 搜索评论相关元素 ===")
    comment_info = await page.evaluate("""
        () => {
            const results = {
                // 搜索包含 "comment" 或 "评论" 的 class/id
                comment_classes: [],
                comment_ids: [],
                // 搜索包含评论文本的元素
                comment_texts: [],
                // 所有包含关键词的元素
                keyword_elements: [],
            };

            // 收集所有元素的 class
            const allElements = document.querySelectorAll('*');
            const classSet = new Set();
            for (const el of allElements) {
                const cls = el.className;
                if (typeof cls === 'string' && cls.length > 0) {
                    // 检查是否包含 comment 相关关键词
                    if (cls.match(/comment|discuss|reply|评价|评论|留言/i)) {
                        classSet.add(cls.substring(0, 100));
                    }
                }
                const id = el.id;
                if (id && id.match(/comment|discuss|reply|评价|评论|留言/i)) {
                    results.comment_ids.push(id);
                }
            }
            results.comment_classes = [...classSet].slice(0, 50);

            // 搜索包含评论关键词的文本内容
            const walker = document.createTreeWalker(
                document.body, NodeFilter.SHOW_TEXT, null, false
            );
            const textNodes = [];
            while (walker.nextNode()) {
                const text = walker.currentNode.textContent.trim();
                if (text.match(/评论|留言|回复|展开|查看更多|查看回复|收起|点赞/) && text.length < 50) {
                    const parent = walker.currentNode.parentElement;
                    textNodes.push({
                        text: text,
                        tag: parent.tagName,
                        class: (parent.className || '').substring(0, 80),
                        parent_class: (parent.parentElement?.className || '').substring(0, 80),
                    });
                }
            }
            results.comment_texts = textNodes.slice(0, 30);

            // 统计页面结构
            results.stats = {
                total_elements: allElements.length,
                iframes: document.querySelectorAll('iframe').length,
                videos: document.querySelectorAll('video').length,
                scroll_height: document.body.scrollHeight,
                viewport_height: window.innerHeight,
            };

            return results;
        }
    """)

    info_path = OUTPUT_DIR / f"wechat_cdp_page_{index}_analysis.json"
    info_path.write_text(json.dumps(comment_info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  分析结果: {info_path}")

    # 打印关键发现
    print(f"\n  页面统计:")
    print(f"    元素总数: {comment_info['stats']['total_elements']}")
    print(f"    iframe 数: {comment_info['stats']['iframes']}")
    print(f"    video 数:  {comment_info['stats']['videos']}")
    print(f"    页面高度: {comment_info['stats']['scroll_height']}px")

    if comment_info['comment_classes']:
        print(f"\n  找到 {len(comment_info['comment_classes'])} 个评论相关 class:")
        for cls in comment_info['comment_classes'][:10]:
            print(f"    .{cls}")
    else:
        print("\n  未找到包含 'comment/评论' 关键词的 class")

    if comment_info['comment_texts']:
        print(f"\n  找到 {len(comment_info['comment_texts'])} 个评论相关文本节点:")
        for item in comment_info['comment_texts'][:10]:
            print(f"    [{item['tag']}] \"{item['text']}\"  class=.{item['class']}")

    # 如果有 iframe，也检查 iframe 内容
    iframes = await page.query_selector_all("iframe")
    if iframes:
        print(f"\n  发现 {len(iframes)} 个 iframe，尝试检查内容...")
        for i, iframe in enumerate(iframes):
            try:
                frame = await iframe.content_frame()
                if frame:
                    frame_url = frame.url
                    frame_html = await frame.evaluate("document.documentElement.outerHTML")
                    frame_path = OUTPUT_DIR / f"wechat_cdp_page_{index}_iframe_{i}.html"
                    frame_path.write_text(frame_html, encoding="utf-8")
                    print(f"    iframe[{i}]: {frame_url} -> {frame_path} ({len(frame_html)} bytes)")
            except Exception as e:
                print(f"    iframe[{i}]: 无法访问 ({e})")

    return comment_info


async def watch_network(page, duration: int = 60):
    """监听页面的网络请求，捕获评论相关 API。"""
    print(f"\n=== 监听网络请求 ({duration}秒) ===")
    print("请在微信中打开一个视频号视频并滚动到评论区...\n")

    api_calls = []

    async def on_response(response):
        url = response.url
        # 过滤可能的评论 API
        keywords = ["comment", "discuss", "reply", "feed", "finder", "channels"]
        if any(kw in url.lower() for kw in keywords):
            try:
                content_type = response.headers.get("content-type", "")
                body_preview = ""
                if "json" in content_type or "text" in content_type:
                    body = await response.text()
                    body_preview = body[:500]
                api_calls.append({
                    "url": url,
                    "status": response.status,
                    "content_type": content_type,
                    "body_preview": body_preview,
                })
                print(f"  [API] {response.status} {url[:120]}")
                if body_preview:
                    print(f"        Body: {body_preview[:200]}...")
            except Exception:
                pass

    page.on("response", on_response)

    # 等待用户操作
    start = time.time()
    while time.time() - start < duration:
        await asyncio.sleep(2)
        elapsed = int(time.time() - start)
        if elapsed % 10 == 0 and elapsed > 0:
            print(f"  ... 已等待 {elapsed}s，已捕获 {len(api_calls)} 个 API 调用")

    page.remove_listener("response", on_response)

    # 保存结果
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    api_path = OUTPUT_DIR / "wechat_channels_api_calls.json"
    api_path.write_text(json.dumps(api_calls, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 监听结束 ===")
    print(f"  共捕获 {len(api_calls)} 个 API 调用")
    print(f"  保存到: {api_path}")


async def main():
    parser = argparse.ArgumentParser(description="探测微信 CDP 连接")
    parser.add_argument("--dump-dom", action="store_true", help="抓取页面 DOM 结构")
    parser.add_argument("--page", type=int, default=0, help="指定页面索引（默认 0）")
    parser.add_argument("--watch", action="store_true", help="监听网络请求")
    parser.add_argument("--watch-duration", type=int, default=60, help="监听时长（秒，默认 60）")
    parser.add_argument("--cdp-url", default=CDP_URL, help="CDP 地址")
    args = parser.parse_args()

    print(f"连接 CDP: {args.cdp_url}")
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(args.cdp_url)
    except Exception as e:
        print(f"\n连接失败: {e}")
        print("请确保微信已启动并开启了调试端口：")
        print("  python launch_wechat.py")
        return

    try:
        targets = await list_targets(browser)

        if args.watch:
            # 监听模式：使用第一个页面或新建页面
            if targets:
                page = targets[args.page][2]
            else:
                ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await ctx.new_page()
                print("已创建新页面，请在微信中打开视频号视频...")
            await watch_network(page, args.watch_duration)

        elif args.dump_dom:
            if not targets:
                print("\n没有可调试的页面。请先在微信中打开一个视频号视频。")
                return
            idx = min(args.page, len(targets) - 1)
            page = targets[idx][2]
            await dump_page_dom(page, idx)

        else:
            print("\n提示:")
            print("  --dump-dom       抓取页面 DOM 结构")
            print("  --watch          监听网络请求（推荐先用这个）")
            print("  --page <index>   指定页面索引")

    finally:
        await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())
