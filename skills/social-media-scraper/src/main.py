import argparse
import asyncio
import importlib
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("scraper")

# ── Dependency check & auto-install ──────────────────────────────────────

REQUIRED_PACKAGES = {
    "playwright": "playwright",
    "openpyxl": "openpyxl",
}


def check_dependencies() -> list[str]:
    """Return list of missing package names."""
    missing = []
    for module_name, pip_name in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(pip_name)
    return missing


def _detect_browser_channel() -> str | None:
    """Detect available system browser. Returns channel name or None."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(channel="chrome", headless=True)
                b.close()
                return "chrome"
            except Exception:
                pass
            try:
                b = p.chromium.launch(channel="msedge", headless=True)
                b.close()
                return "msedge"
            except Exception:
                pass
    except Exception:
        pass
    return None


def check_playwright_browser() -> bool:
    """Check if system browser or Chromium is available."""
    if _detect_browser_channel():
        return True
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


def _install_chromium_with_fallback() -> bool:
    """Install Chromium browser. Tries default CDN first, then npmmirror."""
    try:
        subprocess.check_call(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return True
    except subprocess.CalledProcessError:
        pass

    mirror = "https://cdn.npmmirror.com/binaries/playwright"
    print(f"  -> 默认 CDN 安装失败，尝试国内镜像: {mirror}")
    env = os.environ.copy()
    env["PLAYWRIGHT_DOWNLOAD_HOST"] = mirror
    try:
        subprocess.check_call(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return True
    except subprocess.CalledProcessError:
        pass

    print("  -> 国内镜像版本可能不匹配，请手动安装:")
    print("     1. 使用 VPN/代理后运行: playwright install chromium")
    print("     2. 或手动下载: https://playwright.dev/docs/browsers")
    return False


def install_dependencies():
    """Install missing pip packages and Playwright browser."""
    print("\n" + "=" * 60)
    print("  环境检查与自动安装")
    print("=" * 60)

    missing = check_dependencies()
    if missing:
        print(f"\n[1/2] 正在安装缺失的 Python 包: {', '.join(missing)}")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet"] + missing,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            print(f"  -> 安装成功: {', '.join(missing)}")
        except subprocess.CalledProcessError as e:
            print(f"  -> 安装失败: {e}")
            print(f"  -> 请手动运行: pip install {' '.join(missing)}")
            return False
    else:
        print("\n[1/2] Python 包已就绪")

    print("[2/2] 检查浏览器...")
    channel = _detect_browser_channel()
    if channel:
        print(f"  -> 检测到系统 {channel}，无需下载 Chromium")
    elif not check_playwright_browser():
        print("  -> 未检测到系统 Chrome，正在安装 Chromium 浏览器（约 300MB）...")
        if _install_chromium_with_fallback():
            print("  -> Chromium 安装成功")
        else:
            print("  -> Chromium 安装失败")
            print("  -> 请确保已安装 Google Chrome 或手动运行: playwright install chromium")
            return False
    else:
        print("  -> Chromium 已就绪")

    print("\n" + "=" * 60)
    print("  环境检查通过！可以开始使用了。")
    print("=" * 60)
    return True


# ── Logging ──────────────────────────────────────────────────────────────

def setup_logging():
    import config
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = config.LOG_DIR / f"scrape_{datetime.now():%Y%m%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


# ── Excel I/O ───────────────────────────────────────────────────────────

def read_input_excel(filepath) -> list[dict]:
    """Read input Excel and return rows with valid URLs and supported platforms."""
    import config
    from scrapers import match_platform

    if not Path(filepath).exists():
        print(f"\n[错误] 找不到输入文件: {filepath}")
        print("请检查文件路径是否正确，或者使用 --input 指定文件路径")
        return []

    try:
        import openpyxl
        wb = openpyxl.load_workbook(filepath, read_only=True)
    except Exception as e:
        print(f"\n[错误] 无法打开 Excel 文件: {e}")
        print("请确认文件是 .xlsx 格式，且没有被其他程序占用")
        return []

    ws = wb.active
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        url = row[config.COL_URL]
        if not url or not isinstance(url, str) or not url.startswith("http"):
            continue
        platform_key = match_platform(url)
        if platform_key is None:
            continue
        rows.append({
            "serial": row[config.COL_SERIAL],
            "title": str(row[config.COL_TITLE] or ""),
            "source": str(row[config.COL_SOURCE] or ""),
            "author": str(row[config.COL_AUTHOR] or ""),
            "url": url,
            "platform_key": platform_key,
        })
    wb.close()

    if not rows:
        print("\n[警告] Excel 中没有找到支持的链接")
        print("支持的平台: 抖音(douyin), 小红书(xiaohongshu), 微博(weibo)")
        print("请确认 Excel 第3列(C列)包含有效链接")

    return rows


_PLATFORM_DISPLAY = {
    "douyin": "抖音",
    "xiaohongshu": "小红书",
    "weibo": "微博",
}


# ── Login ────────────────────────────────────────────────────────────────

def check_cookies(platform_keys: list[str]) -> list[str]:
    import config
    missing = []
    for key in platform_keys:
        cookie_path = config.COOKIE_DIR / f"{key}.json"
        if not cookie_path.exists():
            missing.append(key)
    return missing


async def login_platform(platform_key: str, wait_seconds: int = 300):
    from scrapers.douyin import DouyinScraper
    from scrapers.xiaohongshu import XiaohongshuScraper
    from scrapers.weibo import WeiboScraper

    scraper_map = {
        "douyin": DouyinScraper,
        "xiaohongshu": XiaohongshuScraper,
        "weibo": WeiboScraper,
    }
    scraper_cls = scraper_map.get(platform_key)
    if not scraper_cls:
        print(f"[错误] 未知平台: {platform_key}，支持的平台: {', '.join(scraper_map.keys())}")
        return
    scraper = scraper_cls()
    await scraper.login_interactive(wait_seconds=wait_seconds)


async def login_open_all(platform_keys: list[str]):
    """Open browsers for all missing platforms, poll for signal files to save cookies."""
    import config
    from scrapers.douyin import DouyinScraper
    from scrapers.xiaohongshu import XiaohongshuScraper
    from scrapers.weibo import WeiboScraper

    scraper_map = {
        "douyin": DouyinScraper,
        "xiaohongshu": XiaohongshuScraper,
        "weibo": WeiboScraper,
    }

    missing = check_cookies(platform_keys)
    if not missing:
        print("\n所有平台均已登录，无需重复登录。")
        return

    print(f"\n需要登录的平台: {', '.join(missing)}")
    print("将依次打开浏览器，请在浏览器中手动登录。\n")

    for key in missing:
        scraper_cls = scraper_map.get(key)
        if not scraper_cls:
            continue
        try:
            scraper = scraper_cls()
            await scraper.login_interactive(wait_seconds=0)
        except Exception as e:
            print(f"[{key}] 打开失败: {e}")

    print("\n所有平台登录完成。")


async def login_save_all(platform_keys: list[str]):
    """Write signal files to trigger cookie saves in running login processes."""
    import config

    missing = check_cookies(platform_keys)
    if not missing:
        print("\n所有平台均已登录，无需重复保存。")
        return

    print(f"\n正在保存以下平台的 Cookie: {', '.join(missing)}\n")

    for key in missing:
        signal_file = config.COOKIE_DIR / f".{key}_login_done"
        signal_file.write_text("done", encoding="utf-8")
        print(f"[{key}] 已发送保存信号")

    print("\n所有 Cookie 保存完成！可以开始抓取了。")


# ── Scrape ───────────────────────────────────────────────────────────────

ERROR_MESSAGES = {
    "no_comments": "该视频暂无评论",
    "note_unavailable": "笔记不可用（可能已被删除或设为私密）",
    "note_anti_crawl": "笔记触发反爬机制（已自动重试）",
    "login_required": "需要重新登录（Cookie 已过期）",
    "network_error": "网络连接失败，请检查网络后重试",
    "page_timeout": "页面加载超时，可能网络较慢或平台繁忙",
    "unknown": "未知错误",
}


def classify_error(error_str: str) -> str:
    """Map raw error to user-friendly Chinese message."""
    lower = error_str.lower()
    if "暂无评论" in error_str:
        return ERROR_MESSAGES["no_comments"]
    if "笔记不可用" in error_str or "noteunavailable" in lower:
        return ERROR_MESSAGES["note_unavailable"]
    if "反爬" in error_str:
        return ERROR_MESSAGES["note_anti_crawl"]
    if "login" in lower or "cookie" in lower:
        return ERROR_MESSAGES["login_required"]
    if "timeout" in lower or "timed out" in lower:
        return ERROR_MESSAGES["page_timeout"]
    if "connection" in lower or "connect" in lower:
        return ERROR_MESSAGES["network_error"]
    return error_str


async def run_all(input_file, platforms_filter: list[str] = None, resume: bool = False, batch_size: int = 0, login_timeout: int = 300):
    """Full pipeline: check cookies → auto-login if needed → scrape.

    Single command, no agent coordination needed. Opens browser for missing
    platforms, auto-detects login via URL change, saves cookies, then scrapes.
    """
    import config
    from scrapers.douyin import DouyinScraper
    from scrapers.xiaohongshu import XiaohongshuScraper
    from scrapers.weibo import WeiboScraper

    scraper_classes = {
        "douyin": DouyinScraper,
        "xiaohongshu": XiaohongshuScraper,
        "weibo": WeiboScraper,
    }

    rows = read_input_excel(input_file)
    if not rows:
        print("[错误] Excel 中没有找到支持的链接")
        return []

    if platforms_filter:
        rows = [r for r in rows if r["platform_key"] in platforms_filter]

    if not rows:
        print("[错误] 筛选后没有匹配的链接")
        return []

    needed_platforms = list(set(r["platform_key"] for r in rows))
    missing = check_cookies(needed_platforms)

    # ── Auto-login for missing platforms ─────────────────────────
    if missing:
        print(f"\n{'='*50}")
        print(f"  需要登录的平台: {', '.join(missing)}")
        print(f"  浏览器将自动打开，请在浏览器中登录")
        print(f"  登录成功后会自动检测并保存 Cookie")
        print(f"{'='*50}\n")

        for key in missing:
            scraper_cls = scraper_classes.get(key)
            if not scraper_cls:
                continue
            scraper = scraper_cls()
            try:
                success = await scraper.wait_for_login(timeout=login_timeout)
                if not success:
                    print(f"[{key}] 登录超时，跳过该平台")
            except Exception as e:
                print(f"[{key}] 登录失败: {e}")

        # Re-check after login attempt
        still_missing = check_cookies(needed_platforms)
        if still_missing:
            print(f"\n[警告] 以下平台仍未登录: {', '.join(still_missing)}")
            print("这些平台的链接将被跳过。")

    # ── Scrape ───────────────────────────────────────────────────
    print(f"\n开始抓取...\n")
    return await scrape_all(input_file, platforms_filter, resume=resume, batch_size=batch_size)


async def scrape_all(input_file, platforms_filter: list[str] = None, resume: bool = False, batch_size: int = 0):
    """Scrape all URLs and return raw results for agent processing.

    Args:
        resume: If True, load checkpoint and skip already-scraped URLs.
        batch_size: If >0, only scrape this many URLs then exit (for timeout avoidance).
    """
    import json
    import config
    from scrapers.douyin import DouyinScraper
    from scrapers.xiaohongshu import XiaohongshuScraper
    from scrapers.weibo import WeiboScraper

    scraper_classes = {
        "douyin": DouyinScraper,
        "xiaohongshu": XiaohongshuScraper,
        "weibo": WeiboScraper,
    }

    rows = read_input_excel(input_file)
    if not rows:
        return []

    total = len(rows)
    logger.info(f"开始抓取，共 {total} 条链接")

    # ── Checkpoint loading ──────────────────────────────────────
    checkpoint_path = config.OUTPUT_DIR / "scrape_checkpoint.jsonl"
    completed_urls: set[str] = set()
    results: list[dict] = []

    if resume and checkpoint_path.exists():
        try:
            lines = checkpoint_path.read_text(encoding="utf-8").splitlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                results.append(item)
                completed_urls.add(item["url"])
            logger.info(f"从 checkpoint 恢复，已完成 {len(results)} 条")
            print(f"\n[续抓] 从 checkpoint 恢复，已完成 {len(results)} 条，跳过已抓链接")
        except Exception as e:
            logger.warning(f"读取 checkpoint 失败，从头开始: {e}")
            results = []
            completed_urls = set()

    if platforms_filter:
        rows = [r for r in rows if r["platform_key"] in platforms_filter]
        logger.info(f"筛选平台 {platforms_filter} 后，剩余 {len(rows)} 条链接")
        if not rows:
            print(f"\n[警告] 筛选后没有匹配的链接")
            print(f"支持的平台: {', '.join(scraper_classes.keys())}")
            return []

    needed_platforms = list(set(r["platform_key"] for r in rows))
    missing = check_cookies(needed_platforms)
    if missing:
        print(f"\n以下平台缺少登录状态: {', '.join(missing)}")
        print("正在自动打开浏览器登录...\n")

        for key in missing:
            scraper_cls = scraper_classes.get(key)
            if not scraper_cls:
                continue
            scraper = scraper_cls()
            try:
                success = await scraper.wait_for_login(timeout=300)
                if not success:
                    print(f"[{key}] 登录超时，该平台链接将被跳过")
            except Exception as e:
                print(f"[{key}] 登录失败: {e}")

        # Re-check after login attempt
        still_missing = check_cookies(needed_platforms)
        if still_missing:
            print(f"\n[警告] 以下平台仍未登录: {', '.join(still_missing)}")
            print("这些平台的链接将被跳过。")

    # ── Filter out already-scraped URLs ─────────────────────────
    if completed_urls:
        before = len(rows)
        rows = [r for r in rows if r["url"] not in completed_urls]
        logger.info(f"跳过已完成 {before - len(rows)} 条，剩余 {len(rows)} 条")
        print(f"  跳过已完成 {before - len(rows)} 条，剩余 {len(rows)} 条待抓取")

    if not rows:
        print("\n所有链接均已抓取完成。")
        if checkpoint_path.exists():
            checkpoint_path.unlink()
        return results

    # ── Batch size limit ──────────────────────────────────────
    if batch_size > 0 and len(rows) > batch_size:
        rows = rows[:batch_size]
        logger.info(f"批次限制，本轮抓取 {batch_size} 条")
        print(f"  批次限制：本轮抓取前 {batch_size} 条")

    active_platforms = {r["platform_key"] for r in rows}

    for platform_key, scraper_cls in scraper_classes.items():
        if platform_key not in active_platforms:
            continue

        platform_rows = [r for r in rows if r["platform_key"] == platform_key]
        platform_display = _PLATFORM_DISPLAY.get(platform_key, platform_key)
        print(f"\n--- 开始抓取 {platform_display} ({len(platform_rows)} 条链接) ---")

        scraper = scraper_cls()
        try:
            await scraper.start()
        except Exception as e:
            print(f"[错误] 无法启动浏览器: {e}")
            for row in platform_rows:
                results.append({
                    "serial": row["serial"],
                    "title": row["title"],
                    "url": row["url"],
                    "platform": platform_key,
                    "status": "失败",
                    "error": f"浏览器启动失败: {e}",
                    "post_content": "",
                    "comments": [],
                })
            continue

        try:
            for i, row in enumerate(platform_rows, 1):
                url = row["url"]
                print(f"  [{i}/{len(platform_rows)}] {row['title'][:30]}...", end=" ", flush=True)

                try:
                    result = await scraper.scrape(url)
                    post_content = result.get("post_content", "")
                    comments = result.get("comments", [])
                    count = len(comments)
                    print(f"成功 ({count}条评论)")

                    item = {
                        "serial": row["serial"],
                        "title": row["title"],
                        "url": url,
                        "platform": platform_key,
                        "status": "成功",
                        "error": "",
                        "post_content": post_content,
                        "comments": [{"nickname": n, "content": c} for n, c in comments],
                    }
                    results.append(item)

                except Exception as e:
                    error_msg = classify_error(str(e))
                    print(f"失败 - {error_msg}")
                    logger.error(f"[{platform_key}] 抓取失败: {url} - {e}")
                    item = {
                        "serial": row["serial"],
                        "title": row["title"],
                        "url": url,
                        "platform": platform_key,
                        "status": "失败",
                        "error": error_msg,
                        "post_content": "",
                        "comments": [],
                    }
                    results.append(item)

                    # Restart context if browser/context was closed
                    if "closed" in str(e).lower():
                        logger.info(f"[{platform_key}] Restarting browser context...")
                        try:
                            await scraper.stop()
                            await scraper.start()
                        except Exception as restart_err:
                            logger.error(f"[{platform_key}] Failed to restart: {restart_err}")

                # ── Incremental checkpoint save ─────────────────
                try:
                    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                    with open(checkpoint_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(item, ensure_ascii=False) + "\n")
                except Exception as e:
                    logger.warning(f"checkpoint 写入失败: {e}")

        finally:
            await scraper.stop()

    # Output results as JSON for agent parsing
    print(f"\n{'=' * 50}")
    print(f"  本轮抓取完成！共 {len(results)} 条")
    print(f"{'=' * 50}")

    # Save final JSON results
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    json_path = config.OUTPUT_DIR / f"scrape_result_{now:%Y%m%d_%H%M%S}.json"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已保存: {json_path}")

    # ── Check if ALL URLs are done before cleaning checkpoint ────
    # Reload original input to get total URL count
    all_input_rows = read_input_excel(input_file)
    all_urls = {r["url"] for r in all_input_rows}
    all_scraped = completed_urls | {r["url"] for r in results}
    remaining = all_urls - all_scraped

    if not remaining:
        if checkpoint_path.exists():
            checkpoint_path.unlink()
            logger.info("所有链接已抓取，checkpoint 文件已清理")
        print("所有链接已完成抓取！")
    else:
        print(f"  剩余 {len(remaining)} 条链接未抓取，checkpoint 已保留。")
        print(f"  下次运行: python main.py --input {input_file} --resume --batch-size {batch_size}")

    # Print JSON to stdout for agent
    print("\n===RAW_RESULTS_START===")
    print(json.dumps(results, ensure_ascii=False))
    print("===RAW_RESULTS_END===")

    return results


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="多平台评论抓取工具（抖音/小红书/微博）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python main.py --setup                     首次使用，自动安装环境
  python main.py --run --input input.xlsx    一键执行：自动登录+抓取（推荐）
  python main.py --run --input input.xlsx --resume --batch-size 20  带断点续抓
  python main.py --login                     登录所有平台（打开浏览器，等待登录后保存）
  python main.py --login --platforms weibo   只登录微博
  python main.py --input input.xlsx          从Excel抓取评论
  python main.py --input input.xlsx --platforms douyin,xiaohongshu  只抓指定平台

分步登录（备用）:
  python main.py --login-open                打开登录浏览器
  （人类在浏览器中登录...）
  python main.py --login-save                保存Cookie
        """,
    )
    parser.add_argument("--setup", action="store_true", help="自动安装依赖环境（首次使用必选）")
    parser.add_argument("--run", action="store_true", help="一键执行：自动检查登录+抓取（推荐）")
    parser.add_argument("--login", action="store_true", help="交互式登录各平台并保存Cookie")
    parser.add_argument("--login-open", action="store_true", help="打开登录浏览器（不阻塞，登录后需运行 --login-save）")
    parser.add_argument("--login-save", action="store_true", help="保存已打开浏览器的Cookie")
    parser.add_argument("--platforms", default="", help="指定平台(逗号分隔): douyin,xiaohongshu,weibo")
    parser.add_argument("--input", default="", help="输入Excel文件路径")
    parser.add_argument("--resume", action="store_true", help="从上次中断处继续抓取（跳过已完成的URL）")
    parser.add_argument("--batch-size", type=int, default=0, help="每轮最多抓取N条（配合--resume避免超时）")
    parser.add_argument("--login-timeout", type=int, default=300, help="登录等待超时秒数（默认300）")
    args = parser.parse_args()

    # Always check and install dependencies first
    if not args.setup:
        missing = check_dependencies()
        if missing:
            print(f"[提示] 缺少依赖包: {', '.join(missing)}")
            print("正在自动安装...")
            if not install_dependencies():
                sys.exit(1)

    if args.setup:
        success = install_dependencies()
        sys.exit(0 if success else 1)

    setup_logging()
    import config
    config.COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    input_file = args.input if args.input else str(config.INPUT_FILE)

    if args.run:
        platforms_filter = None
        if args.platforms:
            platforms_filter = [p.strip() for p in args.platforms.split(",") if p.strip()]
        asyncio.run(run_all(input_file, platforms_filter, resume=args.resume, batch_size=args.batch_size, login_timeout=args.login_timeout))
    elif args.login_open:
        if args.platforms:
            platform_list = [p.strip() for p in args.platforms.split(",") if p.strip()]
        else:
            platform_list = ["douyin", "xiaohongshu", "weibo"]
        asyncio.run(login_open_all(platform_list))
    elif args.login_save:
        asyncio.run(login_save_all(["douyin", "xiaohongshu", "weibo"]))
    elif args.login:
        if args.platforms:
            platform_list = [p.strip() for p in args.platforms.split(",") if p.strip()]
        else:
            platform_list = ["douyin", "xiaohongshu", "weibo"]
        asyncio.run(login_open_all(platform_list))
    else:
        platforms_filter = None
        if args.platforms:
            platforms_filter = [p.strip() for p in args.platforms.split(",") if p.strip()]
        asyncio.run(scrape_all(input_file, platforms_filter, resume=args.resume, batch_size=args.batch_size))


if __name__ == "__main__":
    main()
