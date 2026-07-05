"""深度探测微信 UIA 结构 — 使用 UIA COM 接口遍历控件树。

Usage:
    python probe_wechat_uia_deep.py              # 列出微信窗口 + 浅层遍历
    python probe_wechat_uia_deep.py --screenshot  # 加截图
"""
import argparse
import time
from pathlib import Path

import pyautogui

try:
    import uiautomation as auto
    HAS_UIA = True
except ImportError:
    HAS_UIA = False
    print("[WARN] uiautomation 未安装，请: pip install uiautomation")
    import sys
    sys.exit(1)

OUTPUT_DIR = Path("output/debug")


def list_wechat_windows():
    """使用 uiautomation 列出所有窗口"""
    print("\n=== 所有微信相关窗口 (UIA) ===\n")
    wechat_wins = []

    for w in auto.GetRootControl().GetChildren():
        try:
            name = w.Name
            class_name = w.ClassName
            automation_id = w.AutomationId
            rect = w.BoundingRectangle

            if not name and not class_name:
                continue

            keywords = ["微信", "WeChat", "wechat", "Weixin"]
            is_wechat = any(k in (name or "") for k in keywords) or \
                       any(k in (class_name or "") for k in keywords) or \
                       any(k in (automation_id or "") for k in keywords)

            if is_wechat:
                wechat_wins.append(w)
                print(f"  [微信] Name={name!r}  Class={class_name!r}  "
                      f"AutomationId={automation_id!r}  "
                      f"Rect=({rect.left},{rect.top},{rect.right},{rect.bottom})")
            elif name and name.strip():
                pass

        except Exception:
            continue

    print(f"\n共找到 {len(wechat_wins)} 个微信相关窗口")
    return wechat_wins


def dump_subtree(ctrl, depth=0, max_depth=5, max_children=60):
    """递归打印 UIA 控件子树，聚焦关键信息"""
    if depth > max_depth:
        return

    try:
        children = ctrl.GetChildren()
    except Exception:
        return

    for i, child in enumerate(children[:max_children]):
        try:
            name = (child.Name or "")[:80]
            class_name = child.ClassName or ""
            automation_id = child.AutomationId or ""
            control_type = child.ControlTypeName or ""
            rect = child.BoundingRectangle
            is_enabled = child.IsEnabled
            is_offscreen = child.IsOffscreen if hasattr(child, 'IsOffscreen') else False

            indent = "  " * depth
            tag = ""
            if not is_enabled:
                tag += " [disabled]"
            if is_offscreen:
                tag += " [offscreen]"

            has_text = "TEXT:" if name else ""
            print(f"{indent}[{control_type}] "
                  f"{has_text}{name!r} "
                  f"AId={automation_id!r} "
                  f"Cls={class_name!r}"
                  f"{tag}")

            dump_subtree(child, depth + 1, max_depth, max_children)
        except Exception:
            pass


def find_comment_related(ctrl, keywords, max_depth=8, results=None):
    """递归搜索评论区相关控件"""
    if results is None:
        results = []

    if len(results) >= 50:
        return results

    try:
        depth = 0
        def walk(node, d):
            nonlocal depth
            if d > max_depth or len(results) >= 50:
                return
            try:
                name = (node.Name or "")[:200]
                automation_id = node.AutomationId or ""
                class_name = node.ClassName or ""

                search_text = f"{name}|{automation_id}|{class_name}".lower()
                for kw in keywords:
                    if kw.lower() in search_text:
                        results.append({
                            "depth": d,
                            "name": name,
                            "automation_id": automation_id,
                            "class_name": class_name,
                            "control_type": node.ControlTypeName,
                            "rect": str(node.BoundingRectangle),
                        })
                        break

                for child in node.GetChildren():
                    walk(child, d + 1)
            except Exception:
                pass

        walk(ctrl, 0)
    except Exception:
        pass

    return results


def take_wechat_screenshot(name="wechat_uia"):
    """截取微信窗口"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%H%M%S")

    # Find WeChat window
    wechat_wins = [w for w in auto.GetRootControl().GetChildren()
                   if "微信" in (w.Name or "") or "WeChat" in (w.Name or "")]
    if wechat_wins:
        win = wechat_wins[0]
        rect = win.BoundingRectangle
        screenshot = pyautogui.screenshot(
            region=(rect.left, rect.top, rect.width(), rect.height())
        )
    else:
        screenshot = pyautogui.screenshot()

    path = OUTPUT_DIR / f"{name}_{timestamp}.png"
    screenshot.save(str(path))
    print(f"\n截图保存: {path}")
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshot", action="store_true", help="截图")
    parser.add_argument("--depth", type=int, default=4, help="遍历深度")
    parser.add_argument("--search", nargs="+",
                        default=["comment", "评论", "回复", "展开", "reply", "discuss", "查看全部", "查看更多"],
                        help="搜索关键词")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    windows = list_wechat_windows()
    if not windows:
        print("\n[错误] 没有找到微信窗口，请确保微信已打开且已登录。")
        return

    main_win = windows[0]
    print(f"\n主窗口: Name={main_win.Name!r} Class={main_win.ClassName!r}")

    if args.screenshot:
        take_wechat_screenshot()

    # 浅层遍历主窗口控件
    print(f"\n=== 控件树 (depth={args.depth}) ===\n")
    dump_subtree(main_win, max_depth=args.depth)

    # 搜索评论相关
    print(f"\n=== 搜索关键词: {args.search} ===\n")
    results = find_comment_related(main_win, args.search)
    if results:
        for r in results:
            print(f"  depth={r['depth']} [{r['control_type']}] "
                  f"Name={r['name'][:80]!r} "
                  f"AId={r['automation_id']!r}")
    else:
        print("  未找到评论相关控件（可能需要先打开一篇文章）")

    print("\n提示:")
    print("  --screenshot  截图保存到 output/debug/")
    print("  --depth N     设置遍历深度（默认 4）")
    print("  --search X Y  搜索指定关键词")


if __name__ == "__main__":
    main()
