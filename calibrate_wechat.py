# calibrate_wechat.py
"""微信 PC 坐标校准工具 — 双击运行或在终端中执行。

操作方式：
  移动鼠标到目标位置 → 按 F8 记录
  按 F9 跳过当前步骤
  按 Esc 退出校准
"""
import ctypes
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pyautogui

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from scrapers.wechat_window_manager import WechatWindowManager, WindowRect

# Virtual key codes
VK_F8 = 0x77
VK_F9 = 0x78
VK_ESCAPE = 0x1B

BASE_DIR = Path(__file__).parent
CALIBRATION_CONFIG = BASE_DIR / "config.wechat_pc.json"
CALIBRATED_PROFILE = "wechat_pc_calibrated"


def wait_for_key(*vk_codes, prompt="按 F8 记录位置，F9 跳过"):
    """等待用户按下指定按键。返回按下的 VK 码。"""
    print()
    print(f"  >>> {prompt} <<<")
    print("  " + "-" * 50)

    # 清除之前的按键状态
    time.sleep(0.3)
    for vk in vk_codes:
        ctypes.windll.user32.GetAsyncKeyState(vk)

    while True:
        for vk in vk_codes:
            state = ctypes.windll.user32.GetAsyncKeyState(vk)
            if state & 0x8000:
                # 等待按键释放，避免重复触发
                while ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000:
                    time.sleep(0.05)
                return vk
        time.sleep(0.05)


# ── Calibration steps ────────────────────────────────────────────

STEPS = [
    {
        "section": "wechat_main",
        "key": "search_bar",
        "type": "point",
        "title": "搜索栏",
        "desc": "微信主界面顶部的【搜索栏/搜索框】",
    },
    {
        "section": "wechat_main",
        "key": "result_open_entry",
        "type": "point",
        "title": "搜索结果入口（访问网页）",
        "desc": "搜索一个公众号链接后，搜索结果中的【访问网页】按钮",
    },
    {
        "section": "official",
        "key": "comment_icon",
        "type": "point",
        "title": "公众号 - 评论区图标",
        "desc": "公众号文章底部的【评论区图标/按钮】（点击可展开评论区）",
    },
    {
        "section": "official",
        "key": "comment_region",
        "type": "region",
        "title": "公众号 - 评论区",
        "desc": "公众号文章中评论区区域\n  第1次 F8: 左上角 | 第2次 F8: 右下角",
    },
    {
        "section": "channels",
        "key": "pause_video",
        "type": "point",
        "title": "视频号 - 视频区域",
        "desc": "打开一个视频号，视频播放区域的中心位置",
    },
    {
        "section": "channels",
        "key": "comment_button",
        "type": "point",
        "title": "视频号 - 评论按钮",
        "desc": "视频号页面的【评论按钮】",
    },
    {
        "section": "channels",
        "key": "comment_panel_region",
        "type": "region",
        "title": "视频号 - 评论面板",
        "desc": "视频号评论面板区域\n  第1次 F8: 左上角 | 第2次 F8: 右下角",
    },
]


def main():
    print("\n" + "=" * 60)
    print("      微信 PC 坐标校准工具")
    print("=" * 60)
    print()
    print("  F8 = 记录鼠标位置")
    print("  F9 = 跳过当前步骤")
    print("  Esc = 退出")
    print()

    # ── Find WeChat window ──
    print("正在查找微信窗口...")
    mgr = WechatWindowManager()
    if not mgr.find_window():
        print("\n[错误] 找不到微信窗口！请确保微信 PC 客户端已启动并登录。")
        input("\n按 Enter 退出...")
        sys.exit(1)

    mgr.move_to_primary_screen()
    mgr.activate()
    rect = mgr.get_rect()
    print(f"找到微信窗口: ({rect.left}, {rect.top}) {rect.width}x{rect.height}\n")

    data = {"wechat_main": {}, "official": {}, "channels": {}}
    skipped = []
    success = 0

    for i, step in enumerate(STEPS, 1):
        section = step["section"]
        key = step["key"]

        # Refresh window rect BEFORE each step — the window may have
        # resized (e.g. article view widens from 942→1386 px)
        if mgr.is_found:
            rect = mgr.get_rect()
            print(f"[窗口] ({rect.left}, {rect.top}) {rect.width}x{rect.height}")

        print("=" * 60)
        print(f"  [{i}/{len(STEPS)}] {step['title']}")
        print("=" * 60)
        print(f"  {step['desc']}")
        print()

        if step["type"] == "point":
            vk = wait_for_key(VK_F8, VK_F9, VK_ESCAPE,
                              prompt="移动鼠标到目标位置 → 按 F8 | F9 跳过 | Esc 退出")

            if vk == VK_ESCAPE:
                print("\n[退出]\n")
                break
            if vk == VK_F9:
                print(f"[跳过] {step['title']}")
                skipped.append(f"{section}.{key}")
                continue

            x, y = pyautogui.position()
            x_ratio = round((x - rect.left) / rect.width, 4)
            y_ratio = round((y - rect.top) / rect.height, 4)
            data[section][key] = {"x_ratio": x_ratio, "y_ratio": y_ratio}
            print(f"  [OK] 屏幕({x}, {y}) → ({x_ratio:.4f}, {y_ratio:.4f})")
            success += 1

        else:
            vk1 = wait_for_key(VK_F8, VK_F9, VK_ESCAPE,
                               prompt="移动鼠标到【左上角】→ 按 F8 | F9 跳过 | Esc 退出")

            if vk1 == VK_ESCAPE:
                print("\n[退出]\n")
                break
            if vk1 == VK_F9:
                print(f"[跳过] {step['title']}")
                skipped.append(f"{section}.{key}")
                continue

            left_x, top_y = pyautogui.position()
            print(f"  左上角: ({left_x}, {top_y})")

            vk2 = wait_for_key(VK_F8, VK_ESCAPE,
                               prompt="移动鼠标到【右下角】→ 按 F8 | Esc 退出")
            if vk2 == VK_ESCAPE:
                print("\n[退出]\n")
                break

            right_x, bottom_y = pyautogui.position()
            print(f"  右下角: ({right_x}, {bottom_y})")

            left_ratio = round((left_x - rect.left) / rect.width, 4)
            top_ratio = round((top_y - rect.top) / rect.height, 4)
            width_ratio = round((right_x - left_x) / rect.width, 4)
            height_ratio = round((bottom_y - top_y) / rect.height, 4)

            data[section][key] = {
                "left_ratio": left_ratio, "top_ratio": top_ratio,
                "width_ratio": width_ratio, "height_ratio": height_ratio,
            }
            print(f"  [OK] 区域: ({left_ratio:.4f}, {top_ratio:.4f}, {width_ratio:.4f}, {height_ratio:.4f})")
            success += 1

        # 刷新窗口位置
        if mgr.is_found:
            rect = mgr.get_rect()

    if success == 0:
        print("\n没有记录任何坐标。")
        input("\n按 Enter 退出...")
        sys.exit(0)

    # ── Save ──
    existing = {}
    if CALIBRATION_CONFIG.exists():
        try:
            existing = json.loads(CALIBRATION_CONFIG.read_text(encoding="utf-8"))
        except Exception:
            pass

    profiles = existing.get("profiles", {})
    prev = profiles.get(CALIBRATED_PROFILE, {})
    merged = {
        "wechat_main": {**prev.get("wechat_main", {}), **data.get("wechat_main", {})},
        "official": {**prev.get("official", {}), **data.get("official", {})},
        "channels": {**prev.get("channels", {}), **data.get("channels", {})},
    }
    profiles[CALIBRATED_PROFILE] = {
        "meta": {
            "wechat_version": "calibrated",
            "dpi_scale": 1.0,
            "calibrated_at": datetime.now(timezone.utc).isoformat(),
            "window_rect": {
                "left": rect.left, "top": rect.top,
                "width": rect.width, "height": rect.height,
            },
        },
        **merged,
    }

    output = {"active_profile": CALIBRATED_PROFILE, "profiles": profiles}
    CALIBRATION_CONFIG.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    print(f"\n{'=' * 60}")
    print(f"  校准完成！记录了 {success} 个坐标")
    if skipped:
        print(f"  跳过: {', '.join(skipped)}")
    print(f"  配置已保存: {CALIBRATION_CONFIG}")
    print(f"{'=' * 60}")

    input("\n按 Enter 退出...")


if __name__ == "__main__":
    main()
