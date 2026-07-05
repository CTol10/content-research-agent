"""探测微信窗口的 UI 控件结构。

Usage:
    python probe_wechat_ui.py                # 列出所有微信相关窗口
    python probe_wechat_ui.py --dump-tree    # dump 窗口控件树
    python probe_wechat_ui.py --screenshot   # 截图并保存
"""
import argparse
import json
import time
from pathlib import Path

import pyautogui
import pywinauto
from pywinauto import Desktop

OUTPUT_DIR = Path("output/debug")


def list_wechat_windows():
    """列出所有微信相关窗口。"""
    desktop = Desktop(backend="uia")
    windows = desktop.windows()

    print("\n=== 所有窗口 ===\n")
    wechat_windows = []
    for w in windows:
        title = w.window_text()
        cls = w.class_name()
        rect = w.rectangle()
        if "微信" in title or "WeChat" in title or "wechat" in cls.lower():
            wechat_windows.append(w)
            print(f"  [微信] title={title!r} class={cls!r} rect=({rect.left},{rect.top},{rect.right},{rect.bottom})")
        elif title.strip():
            print(f"  [其他] title={title!r} class={cls!r}")

    print(f"\n共找到 {len(wechat_windows)} 个微信窗口")
    return wechat_windows


def dump_control_tree(window, depth=0, max_depth=5):
    """递归 dump 窗口的控件树。"""
    if depth > max_depth:
        return []

    results = []
    try:
        children = window.children()
        for child in children:
            try:
                info = {
                    "depth": depth,
                    "class_name": child.class_name(),
                    "control_type": child.element_info.control_type if hasattr(child.element_info, 'control_type') else "?",
                    "title": child.window_text()[:100] if child.window_text() else "",
                    "rect": str(child.rectangle()),
                    "is_visible": child.is_visible(),
                }
                results.append(info)
                indent = "  " * depth
                print(f"{indent}[{info['control_type']}] class={info['class_name']!r} title={info['title']!r} rect={info['rect']}")
                results.extend(dump_control_tree(child, depth + 1, max_depth))
            except Exception as e:
                print(f"{'  ' * depth}[Error] {e}")
    except Exception:
        pass
    return results


def find_controls_by_text(window, keywords: list[str], max_depth=10):
    """在控件树中搜索包含指定关键词的控件。"""
    results = []

    def walk(ctrl, depth=0):
        if depth > max_depth:
            return
        try:
            title = ctrl.window_text()
            if title:
                for kw in keywords:
                    if kw in title:
                        results.append({
                            "depth": depth,
                            "class_name": ctrl.class_name(),
                            "control_type": ctrl.element_info.control_type if hasattr(ctrl.element_info, 'control_type') else "?",
                            "title": title[:100],
                            "rect": str(ctrl.rectangle()),
                        })
                        print(f"  [{'  ' * depth}] Found: [{ctrl.element_info.control_type if hasattr(ctrl.element_info, 'control_type') else '?'}] title={title[:80]!r}")
                        break
            for child in ctrl.children():
                walk(child, depth + 1)
        except Exception:
            pass

    print(f"\n=== 搜索关键词: {keywords} ===\n")
    walk(window)
    return results


def take_screenshot(window=None):
    """截图并保存。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%H%M%S")

    if window:
        rect = window.rectangle()
        screenshot = pyautogui.screenshot(region=(rect.left, rect.top, rect.width(), rect.height()))
    else:
        screenshot = pyautogui.screenshot()

    path = OUTPUT_DIR / f"wechat_screenshot_{timestamp}.png"
    screenshot.save(str(path))
    print(f"截图保存: {path}")
    return path


def main():
    parser = argparse.ArgumentParser(description="探测微信窗口 UI 控件")
    parser.add_argument("--dump-tree", action="store_true", help="dump 控件树")
    parser.add_argument("--screenshot", action="store_true", help="截图")
    parser.add_argument("--depth", type=int, default=5, help="控件树深度（默认 5）")
    parser.add_argument("--search", nargs="+", default=["评论", "回复", "展开", "查看更多", "comment", "reply"],
                        help="搜索关键词")
    args = parser.parse_args()

    windows = list_wechat_windows()
    if not windows:
        print("\n没有找到微信窗口，请确保微信已打开。")
        return

    # 选择主窗口（标题为"微信"的那个）
    main_window = None
    for w in windows:
        title = w.window_text()
        if title == "微信" or "WeChat" in title:
            main_window = w
            break
    if not main_window:
        main_window = windows[0]

    print(f"\n选择窗口: {main_window.window_text()!r}")

    if args.screenshot:
        take_screenshot(main_window)

    if args.dump_tree:
        print(f"\n=== 控件树 (depth={args.depth}) ===\n")
        dump_control_tree(main_window, max_depth=args.depth)

    # 搜索评论相关控件
    find_controls_by_text(main_window, args.search)

    # 保存结果
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("\n提示:")
    print("  --dump-tree   查看完整控件树")
    print("  --screenshot  截图保存到 output/debug/")
    print("  --search X Y  搜索包含指定关键词的控件")


if __name__ == "__main__":
    main()
