"""探测微信文章窗口的 UIA 结构。

前置条件: 请先在微信中打开一篇带评论的公众号文章

Usage:
    python probe_wechat_article.py
"""
import time
from pathlib import Path

import pyautogui

try:
    import uiautomation as auto
except ImportError:
    print("请先安装: pip install uiautomation")
    raise

OUTPUT_DIR = Path("output/debug")


def find_article_windows():
    """查找可能的文章窗口（非主窗口）"""
    wechat_keywords = ["微信", "WeChat", "wechat", "Weixin"]

    # 常见浏览器/渲染引擎类名
    webview_classes = [
        "Chrome_WidgetWin_1",      # Chromium-based
        "Chrome_WindowImpl_0",      # Chrome
        "EdgeWindow",               # Edge
        "CefBrowserWindow",         # CEF
        "Qt51514QWindowIcon",       # Qt (主窗口)
    ]

    print("\n=== 所有窗口（寻找文章窗口）===\n")

    all_wins = auto.GetRootControl().GetChildren()
    candidates = []

    for w in all_wins:
        try:
            name = w.Name or ""
            class_name = w.ClassName or ""
            automation_id = w.AutomationId or ""
            rect = w.BoundingRectangle

            # 跳过最小化或不可见的
            if not name and not class_name:
                continue

            # 候选: 类名是 WebView 相关 或 标题像文章标题
            is_webview = class_name in webview_classes
            is_article_like = not any(k in name for k in wechat_keywords) and len(name) > 5
            is_qt = "Qt" in class_name and class_name != "Qt51514QWindowIcon"

            if is_webview or is_qt or (is_article_like and class_name == "Chrome_WidgetWin_1"):
                candidates.append(w)
                win_type = "[WebView]" if is_webview else "[候选]"
                print(f"  {win_type} Name={name[:60]!r}  "
                      f"Class={class_name!r}  "
                      f"Rect=({rect.left},{rect.top},{rect.width},{rect.height})")

        except Exception:
            continue

    print(f"\n共找到 {len(candidates)} 个可能的文章窗口")
    return candidates


def deep_probe_window(ctrl, max_depth=6):
    """深度遍历单个窗口的 UIA 树"""
    results = []

    def walk(node, depth):
        if depth > max_depth or len(results) > 200:
            return
        try:
            name = (node.Name or "")[:100]
            class_name = node.ClassName or ""
            automation_id = node.AutomationId or ""
            control_type = node.ControlTypeName or ""
            rect = node.BoundingRectangle

            # 只记录有意义的节点
            if name or automation_id or control_type not in ["Pane", "Custom"]:
                results.append({
                    "depth": depth,
                    "control_type": control_type,
                    "name": name,
                    "automation_id": automation_id,
                    "class_name": class_name,
                    "rect": str(rect) if rect else "",
                })

            for child in node.GetChildren():
                walk(child, depth + 1)
        except Exception:
            pass

    walk(ctrl, 0)
    return results


def search_comments(node, keywords, max_depth=10):
    """搜索评论区相关控件"""
    matches = []

    def walk(n, d):
        if d > max_depth or len(matches) > 30:
            return
        try:
            text = f"{n.Name}|{n.AutomationId}|{n.ClassName}|{n.ControlTypeName}".lower()
            for kw in keywords:
                if kw.lower() in text:
                    matches.append({
                        "depth": d,
                        "control_type": n.ControlTypeName,
                        "name": (n.Name or "")[:60],
                        "automation_id": n.AutomationId,
                        "class_name": n.ClassName,
                    })
                    break

            for c in n.GetChildren():
                walk(c, d + 1)
        except Exception:
            pass

    walk(node, 0)
    return matches


def take_window_screenshot(ctrl, name):
    """截取指定窗口"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        rect = ctrl.BoundingRectangle
        if rect and rect.width > 100 and rect.height > 100:
            screenshot = pyautogui.screenshot(
                region=(rect.left, rect.top, rect.width, rect.height)
            )
            path = OUTPUT_DIR / f"{name}_{int(time.time())}.png"
            screenshot.save(str(path))
            print(f"  截图保存: {path}")
            return path
    except Exception as e:
        print(f"  截图失败: {e}")
    return None


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("微信文章窗口 UIA 探测器")
    print("=" * 60)
    print("\n请确保:")
    print("  1. 微信已登录")
    print("  2. 已打开一篇带评论的公众号文章")
    print()

    input("按回车键开始探测...")

    candidates = find_article_windows()

    if not candidates:
        print("\n[!] 未找到可能的文章窗口")
        print("    请在微信中打开一篇文章后重试")
        return

    # 探测每个候选窗口
    for i, win in enumerate(candidates[:3], 1):
        print(f"\n{'='*60}")
        print(f"窗口 {i}/{min(len(candidates), 3)}: {win.Name[:50]!r}")
        print(f"{'='*60}")

        # 截图
        take_window_screenshot(win, f"article_win_{i}")

        # 深度遍历
        print(f"\n--- UIA 控件树 (depth=6) ---")
        results = deep_probe_window(win)

        if results:
            for r in results[:30]:  # 只显示前30个
                indent = "  " * r["depth"]
                name_part = f" Text={r['name']!r}" if r['name'] else ""
                aid_part = f" AId={r['automation_id']!r}" if r['automation_id'] else ""
                print(f"{indent}[{r['control_type']}]{name_part}{aid_part}")
            if len(results) > 30:
                print(f"  ... (共 {len(results)} 个节点)")
        else:
            print("  (该窗口无子控件 - 可能是完全自定义绘制)")

        # 搜索评论相关
        print(f"\n--- 搜索评论区关键词 ---")
        keywords = ["comment", "评论", "回复", "reply", "discuss", "展开", "查看更多", "查看全部"]
        matches = search_comments(win, keywords)

        if matches:
            for m in matches:
                indent = "  " * m["depth"]
                print(f"{indent}[{m['control_type']}] {m['name']!r} "
                      f"(AId={m['automation_id']!r})")
        else:
            print("  未找到评论相关控件")

    # 总结
    print(f"\n{'='*60}")
    print("探测完成")
    print(f"{'='*60}")
    print("\n关键发现:")

    webview_count = sum(1 for c in candidates
                       if c.ClassName in ["Chrome_WidgetWin_1", "EdgeWindow"])
    if webview_count > 0:
        print(f"  ✓ 发现 {webview_count} 个 Chromium/Edge 窗口（可能有 WebView 可访问性）")
    else:
        print(f"  ✗ 未发现标准 WebView 窗口")

    print(f"\n所有截图保存于: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
