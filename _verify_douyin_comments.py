"""端到端验证抖音评论抓取修复效果（只跑抓取，不做分类/视频处理）。"""
import asyncio
import json
import sys
from pathlib import Path

URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.iesdouyin.com/share/video/7683754809844976948"


async def main():
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("_verify_douyin.log.detail", encoding="utf-8"),
        ],
    )

    from scrapers.douyin import DouyinScraper

    dist_profile = Path(__file__).parent / "dist" / "cookies" / "_browser_profile"
    if dist_profile.exists():
        DouyinScraper._USER_DATA_DIR = dist_profile
        print(f"[i] 使用登录态 profile: {dist_profile}", flush=True)

    scraper = DouyinScraper()
    await scraper.start(headless=False)
    try:
        result = await scraper.scrape(URL)
        comments = result["comments"]
        print(f"\n>>> 抓取到评论 {len(comments)} 条", flush=True)
        out = Path("output/debug/_verify_comments.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(comments, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f">>> 已保存 {out}", flush=True)
        # 抽样打印
        for nick, text in comments[:5]:
            print("   ", nick, "|", (text or "")[:50], flush=True)
        print("   ...", flush=True)
        for nick, text in comments[-5:]:
            print("   ", nick, "|", (text or "")[:50], flush=True)
    finally:
        await scraper.stop()


if __name__ == "__main__":
    asyncio.run(main())
