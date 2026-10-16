# _probe_mimo_narration_fix.py
"""端到端验证 MiMo 口播 prompt 修复效果：
1. 打开抖音分享页，通过网络抓包拿视频直链并下载
2. 走 VideoProcessor._transcribe_with_mimo 完整链路（音频转录 -> 视频兜底）
3. 校验：纯BGM视频应返回 None；口播视频应只含最终结果（无时间戳/思考过程/重复）
"""
import logging
import os
import sys
import time
import types

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

# 注入 playwright 自带的 ffmpeg，替代 imageio_ffmpeg
FFMPEG = os.path.join(
    os.environ["LOCALAPPDATA"], "ms-playwright", "ffmpeg-1011", "ffmpeg-win64.exe"
)
assert os.path.exists(FFMPEG), f"ffmpeg not found: {FFMPEG}"
fake = types.ModuleType("imageio_ffmpeg")
fake.get_ffmpeg_exe = lambda: FFMPEG
sys.modules["imageio_ffmpeg"] = fake

import config  # noqa: E402
from video_processor import VideoProcessor  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

CASES = [
    # (video_id, 期望, 说明)
    ("7691279614861327227", "none", "纯音乐BGM无口播 -> 应返回 None"),
    ("7692072256314615531", "text", "用户给的测试视频，应只输出最终口播"),
    ("7692000777968003136", "text", "原反馈带时间节点/重复 -> 应只输出最终口播"),
]

VIDEO_CDNS = ("douyinvod.com", "bytevcloudcdn.com", "ixigua.com", "toutiaovod.com")


def get_video_url(page, share_url: str, wait_s: int = 25) -> str | None:
    captured = []
    page.on(
        "response",
        lambda r: captured.append(r.url)
        if any(c in r.url for c in VIDEO_CDNS) and ".mp4" in r.url.lower()
        else None,
    )
    page.goto(share_url, wait_until="domcontentloaded", timeout=60000)
    # 等待 video 元素出现并尝试播放触发媒体请求
    deadline = time.time() + wait_s
    while time.time() < deadline and not captured:
        try:
            page.mouse.move(400, 300)
            vids = page.locator("video")
            if vids.count() > 0:
                vids.first.evaluate("v => v.play().catch(()=>{})")
        except Exception:
            pass
        page.wait_for_timeout(1000)
    return captured[0] if captured else None


def main():
    assert config.MIMO_API_KEY, "MIMO_API_KEY 未配置"
    vp = VideoProcessor()
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=config.DEFAULT_USER_AGENT,
            viewport={"width": 480, "height": 900},
            locale="zh-CN",
        )
        for vid, expect, desc in CASES:
            print(f"\n{'='*60}\n[{vid}] {desc}")
            url = f"https://www.iesdouyin.com/share/video/{vid}/?schema_type=37"
            page = ctx.new_page()
            try:
                src = get_video_url(page, url)
                if not src:
                    print("  !! 未能抓到视频直链（页面可能被反爬拦截）")
                    results.append((vid, "skip", desc))
                    continue
                print(f"  视频直链: {src[:100]}...")
                vpath = vp.download_video(src, referer="https://www.douyin.com/")
                if not vpath:
                    print("  !! 视频下载失败")
                    results.append((vid, "skip", desc))
                    continue
                print(f"  下载完成: {os.path.getsize(vpath)/1024/1024:.1f}MB，开始转录...")
                text = vp._transcribe_with_mimo(vpath)
                vp.cleanup(vpath)
                print(f"  转录结果: {repr(text[:200]) if text else 'None（无口播）'}")
                results.append((vid, text, desc))
            except Exception as e:
                print(f"  !! 异常: {e}")
                results.append((vid, "error", desc))
            finally:
                page.close()
        browser.close()

    print(f"\n{'='*60}\n结论:")
    for vid, text, desc in results:
        if text == "skip" or text == "error":
            print(f"  [{vid}] 未验证（{text}）")
            continue
        if desc.startswith("纯音乐"):
            ok = text is None
            print(f"  [{vid}] {'PASS' if ok else 'FAIL'} 纯BGM应返回None, got={repr(text)[:80]}")
        else:
            if text is None:
                print(f"  [{vid}] FAIL 有口播却返回None")
                continue
            has_ts = "[0" in text or "（0" in text or "00:" in text
            has_think = "思考" in text or "用户" in text[:10] or "好的" in text[:6]
            print(
                f"  [{vid}] {'PASS' if not has_ts and not has_think else 'WARN'} "
                f"len={len(text)} 时间戳={'有' if has_ts else '无'} "
                f"开头: {text[:60]!r}"
            )


if __name__ == "__main__":
    main()
