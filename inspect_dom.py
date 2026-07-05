"""Inspect DOM structure of a page for debugging selectors."""
import asyncio
import json
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path
from playwright.async_api import async_playwright


async def inspect_url(url: str, platform: str, cookie_file: str):
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=False)
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    cookie_path = Path("cookies") / cookie_file
    if cookie_path.exists():
        cookies = json.loads(cookie_path.read_text(encoding="utf-8"))
        await context.add_cookies(cookies)
        print(f"Loaded cookies from {cookie_path}")

    page = await context.new_page()
    page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))

    print(f"Navigating to: {url}")
    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
    await asyncio.sleep(5)

    # Scroll down to load comments
    for _ in range(5):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1)

    # Save screenshot
    debug_dir = Path("output/debug")
    debug_dir.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(debug_dir / f"inspect_{platform}.png"), full_page=False)

    # Get page HTML structure
    js_file = Path("dom_inspect_weibo.js") if platform == "weibo" else Path("dom_inspect.js")
    if not js_file.exists():
        js_file = Path("dom_inspect.js")
    if js_file.exists():
        js_code = js_file.read_text(encoding="utf-8")
        result = await page.evaluate(js_code)
        print(result)
    else:
        # Fallback: get inner text
        text = await page.evaluate("() => document.body.innerText.substring(0, 3000)")
        print("Page text:")
        print(text)

    await browser.close()
    await pw.stop()


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://weibo.com/1734455121/QB6RrevjV"
    platform = sys.argv[2] if len(sys.argv) > 2 else "weibo"
    cookie_file = sys.argv[3] if len(sys.argv) > 3 else "weibo.json"
    asyncio.run(inspect_url(url, platform, cookie_file))
