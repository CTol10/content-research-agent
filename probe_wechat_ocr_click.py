"""UIA + OCR 端到端验证：截微信窗口 → OCR 定位 UI 元素 → 点击。

原理：UIA 获取窗口位置 → 截图窗口区域 → OCR 找文字位置 → 转屏幕坐标 → pyautogui 点击

Usage:
    python probe_wechat_ocr_click.py                    # 交互式探索
    python probe_wechat_ocr_click.py --url "http://..."  # 自动搜索并打开文章
"""
import argparse
import time
from pathlib import Path

import pyautogui
from rapidocr_onnxruntime import RapidOCR

try:
    import uiautomation as auto
except ImportError:
    print("请先安装: pip install uiautomation")
    raise

OUTPUT_DIR = Path("output/debug")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_wechat_window():
    """通过 UIA 获取微信主窗口"""
    for w in auto.GetRootControl().GetChildren():
        try:
            if "微信" in (w.Name or "") and "Qt" in (w.ClassName or ""):
                return w
        except Exception:
            continue
    return None


def get_window_rect(win):
    """获取窗口矩形 (left, top, width, height)"""
    rect = win.BoundingRectangle
    return {
        "left": rect.left,
        "top": rect.top,
        "width": rect.width(),
        "height": rect.height(),
    }


def screenshot_window(win, label=""):
    """截取微信窗口截图，返回截图路径和窗口 rect"""
    rect = get_window_rect(win)

    try:
        screenshot = pyautogui.screenshot(
            region=(rect["left"], rect["top"], rect["width"], rect["height"])
        )
    except Exception as e:
        print(f"[!] 截图失败: {e}")
        return None, rect

    ts = time.strftime("%H%M%S")
    name = f"wechat_{label}_{ts}" if label else f"wechat_{ts}"
    path = OUTPUT_DIR / f"{name}.png"
    screenshot.save(str(path))

    return str(path), rect


def ocr_find_text(image_path, target_texts, ocr):
    """OCR 识别图片，返回匹配指定文字的区域中心坐标（图片相对坐标）"""
    result, _ = ocr(image_path)
    matches = []

    if not result:
        return matches

    for item in result:
        box = item[0]  # [[left,top],[right,top],[right,bottom],[left,bottom]]
        text = item[1]  # 识别到的文字
        confidence = item[2]  # 置信度

        for target in target_texts:
            if target in text:
                # 计算中心点
                cx = int((box[0][0] + box[2][0]) / 2)
                cy = int((box[0][1] + box[2][1]) / 2)
                matches.append({
                    "text": text,
                    "matched_keyword": target,
                    "center": (cx, cy),  # 图片内相对坐标
                    "bbox": box,
                    "confidence": confidence,
                })
                break  # 每条文字只匹配一次

    return matches


def click_window_relative(win, rel_x, rel_y, desc=""):
    """在微信窗口内的相对坐标处点击"""
    rect = get_window_rect(win)
    screen_x = rect["left"] + rel_x
    screen_y = rect["top"] + rel_y

    print(f"  点击 ({rel_x}, {rel_y}) => 屏幕 ({screen_x}, {screen_y}) {desc}")
    pyautogui.click(screen_x, screen_y)
    time.sleep(0.5)


def focus_wechat(win):
    """激活微信窗口"""
    try:
        if hasattr(win, 'SetFocus'):
            win.SetFocus()
        # 也可以尝试点击窗口标题栏
        rect = get_window_rect(win)
        pyautogui.click(rect["left"] + rect["width"] // 2, rect["top"] + 10)
        time.sleep(0.3)
    except Exception:
        pass


def paste_text(text):
    """通过剪贴板粘贴文字"""
    import subprocess
    subprocess.run(
        ["powershell", "-Command", f"Set-Clipboard -Value '{text}'"],
        capture_output=True, check=True
    )
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="要打开的公众号文章 URL")
    parser.add_argument("--search-only", action="store_true",
                        help="只搜索 UI 元素不执行点击")
    args = parser.parse_args()

    print("=" * 60)
    print("UIA + OCR 微信自动化验证")
    print("=" * 60)

    # 1. 找窗口
    print("\n[1] 查找微信窗口...")
    win = get_wechat_window()
    if not win:
        print("[!] 未找到微信窗口，请确保微信已打开并登录")
        return

    rect = get_window_rect(win)
    print(f"  找到: Name={win.Name!r}")
    print(f"  位置: left={rect['left']} top={rect['top']} "
          f"size={rect['width']}x{rect['height']}")

    # 2. 激活并截图
    print("\n[2] 激活窗口并截图...")
    focus_wechat(win)
    time.sleep(0.5)

    img_path, _ = screenshot_window(win, "baseline")
    if not img_path:
        return
    print(f"  截图: {img_path}")

    # 3. OCR 探索
    print("\n[3] OCR 识别窗口文字...")
    ocr = RapidOCR()

    # 搜索关键 UI 元素
    search_keywords = ["搜索", "Search", "search",
                       "评论", "comment", "Comment",
                       "回复", "Reply", "回复",
                       "访问", "网页",
                       "文章", "公众号",
                       "刷新", "返回", "关闭",
                       "更多"]
    matches = ocr_find_text(img_path, search_keywords, ocr)

    print(f"\n找到 {len(matches)} 个匹配:")
    for m in matches:
        print(f"  [{m['confidence']:.1%}] \"{m['text'][:40]}\" "
              f"=> 相对坐标 ({m['center'][0]}, {m['center'][1]})")

    # 4. 如果有 URL，尝试搜索并打开
    if args.url and not args.search_only:
        print(f"\n[4] 尝试搜索并打开 URL...")
        print(f"  URL: {args.url[:80]}")

        # 4a. 找搜索框并点击
        search_matches = [m for m in matches
                         if m["matched_keyword"] in ["搜索", "Search", "search"]]
        if search_matches:
            m = search_matches[0]
            print(f"  找到搜索入口: \"{m['text']}\"")
            click_window_relative(win, m["center"][0], m["center"][1],
                                  "搜索框")
            time.sleep(0.5)

        # 4b. 粘贴 URL
        print("  粘贴 URL...")
        paste_text(args.url)
        time.sleep(0.3)
        pyautogui.press("enter")
        print("  已按回车，等待加载...")
        time.sleep(3)

        # 4c. 截图看结果
        img2, _ = screenshot_window(win, "after_search")
        if img2:
            print(f"  搜索后截图: {img2}")

            # OCR 新截图
            matches2 = ocr_find_text(img2, search_keywords, ocr)
            print(f"\n  搜索后找到 {len(matches2)} 个匹配:")
            for m in matches2:
                print(f"  [{m['confidence']:.1%}] \"{m['text'][:40]}\"")
        else:
            print("  [!] 搜索后截图失败")

    elif args.search_only:
        # 探索模式：遍历所有识别到的文字
        print(f"\n[4] 探索模式：显示所有识别文字（前100个）")
        result, _ = ocr(img_path)
        if result:
            texts = sorted(result, key=lambda x: (x[0][0][1], x[0][0][0]))  # 按位置排序
            for item in texts[:100]:
                text = item[1]
                box = item[0]
                x, y = int(box[0][0]), int(box[0][1])
                print(f"  ({x:4d},{y:4d}) {text[:60]}")

    # 完成
    print(f"\n{'=' * 60}")
    print("完成")
    print(f"{'=' * 60}")
    print(f"所有截图保存于: {OUTPUT_DIR}")
    print(f"\n提示:")
    print(f"  python probe_wechat_ocr_click.py --search-only  纯 OCR 探索")
    print(f"  python probe_wechat_ocr_click.py --url URL      自动搜索打开文章")


if __name__ == "__main__":
    main()
