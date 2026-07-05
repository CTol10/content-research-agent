"""Batch scrape Douyin + Xiaohongshu comments from input Excel, write to Sheet2."""

import asyncio
import logging
import sys
from datetime import datetime

import openpyxl

import config
from scrapers.comment_cleaner import clean_comment_content
from scrapers.douyin import DouyinScraper, NoCommentsError
from scrapers.xiaohongshu import XiaohongshuScraper, NoteUnavailableError

INPUT_FILE = "input/全季大观原始底表0515-0517.xlsx"
PLATFORM_SCRAPERS = {
    "抖音": DouyinScraper,
    "小红书": XiaohongshuScraper,
}

logger = logging.getLogger("batch")


def setup_logging():
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = config.LOG_DIR / f"batch_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def read_input(filepath: str) -> list[dict]:
    """Read input Excel, return rows with Douyin/Xiaohongshu URLs."""
    wb = openpyxl.load_workbook(filepath, read_only=True)
    ws = wb.active
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        url = row[2]   # Col C: 原文/评论链接
        platform = row[4]  # Col E: 媒体平台
        if not url or not isinstance(url, str) or not url.startswith("http"):
            continue
        if platform not in PLATFORM_SCRAPERS:
            continue
        rows.append({
            "row_num": len(rows) + 2,  # Excel row number (1-indexed, +1 for header)
            "serial": row[0],
            "title": str(row[1] or ""),
            "url": url,
            "platform": platform,
            "author": str(row[6] or ""),
        })
    wb.close()
    return rows


def write_sheet2(filepath: str, all_comments: list[dict]):
    """Write all comments to Sheet2 of the input Excel file.

    all_comments: list of {serial, platform, title, author, url, nickname, content}
    """
    wb = openpyxl.load_workbook(filepath)

    # Remove existing Sheet2 if present
    if "评论抓取结果" in wb.sheetnames:
        del wb["评论抓取结果"]

    ws = wb.create_sheet("评论抓取结果")

    # Header
    ws.append(["序号", "平台", "原标题", "原文作者", "链接", "ID名称", "评论内容"])

    for idx, c in enumerate(all_comments, start=1):
        ws.append([
            idx,
            c["platform"],
            c["title"],
            c["author"],
            c["url"],
            c["nickname"],
            c["content"],
        ])

    try:
        wb.save(filepath)
        wb.close()
        logger.info(f"Sheet2 写入完成: {len(all_comments)} 条评论 -> {filepath}")
    except PermissionError:
        # File is open in Excel, save to a copy instead
        copy_path = filepath.replace(".xlsx", "_结果.xlsx")
        wb.save(copy_path)
        wb.close()
        logger.warning(f"原文件被占用，结果已保存到: {copy_path}")
    except Exception as e:
        wb.close()
        raise


async def run():
    setup_logging()

    rows = read_input(INPUT_FILE)
    logger.info(f"读取到 {len(rows)} 条抖音/小红书链接")

    # Group by platform
    by_platform = {}
    for r in rows:
        by_platform.setdefault(r["platform"], []).append(r)

    all_comments = []
    unavailable = []

    for platform, platform_rows in by_platform.items():
        scraper_cls = PLATFORM_SCRAPERS[platform]
        scraper = scraper_cls()
        await scraper.start()

        try:
            for i, row in enumerate(platform_rows):
                url = row["url"]
                logger.info(f"[{platform}] ({i+1}/{len(platform_rows)}) 正在抓取: {url}")

                try:
                    comments = await scraper.scrape(url)
                    logger.info(f"[{platform}] 获取 {len(comments)} 条评论")

                    for nickname, content in comments:
                        cleaned_content = clean_comment_content(content, platform=platform)
                        if not cleaned_content:
                            continue
                        all_comments.append({
                            "serial": row["serial"],
                            "platform": platform,
                            "title": row["title"],
                            "author": row["author"],
                            "url": url,
                            "nickname": nickname,
                            "content": cleaned_content,
                        })

                except NoteUnavailableError as e:
                    logger.warning(f"[{platform}] 笔记不可用: {url} - {e}")
                    all_comments.append({
                        "serial": row["serial"],
                        "platform": platform,
                        "title": row["title"],
                        "author": row["author"],
                        "url": url,
                        "nickname": "[笔记不可用]",
                        "content": str(e),
                    })
                    unavailable.append({"serial": row["serial"], "platform": platform, "title": row["title"], "url": url, "reason": str(e)})

                except NoCommentsError as e:
                    logger.info(f"[{platform}] 无评论: {url}")
                    all_comments.append({
                        "serial": row["serial"],
                        "platform": platform,
                        "title": row["title"],
                        "author": row["author"],
                        "url": url,
                        "nickname": "[无评论]",
                        "content": str(e),
                    })

                except Exception as e:
                    logger.error(f"[{platform}] 抓取失败: {url} - {e}")
                    all_comments.append({
                        "serial": row["serial"],
                        "platform": platform,
                        "title": row["title"],
                        "author": row["author"],
                        "url": url,
                        "nickname": "[抓取失败]",
                        "content": str(e),
                    })

                # Brief pause between URLs
                await asyncio.sleep(2)

        finally:
            await scraper.stop()

    # Write results to Sheet2
    write_sheet2(INPUT_FILE, all_comments)

    # Summary
    logger.info(f"=== 批量抓取完成 ===")
    logger.info(f"总链接数: {len(rows)}")
    logger.info(f"总评论数: {len(all_comments)}")
    for platform in by_platform:
        count = sum(1 for c in all_comments if c["platform"] == platform)
        logger.info(f"  {platform}: {count} 条评论")

    if unavailable:
        logger.warning(f"=== 不可用笔记 ({len(unavailable)} 条) ===")
        for u in unavailable:
            logger.warning(f"  [{u['serial']}] {u['platform']} - {u['title'][:30]}: {u['reason']}")


if __name__ == "__main__":
    asyncio.run(run())
