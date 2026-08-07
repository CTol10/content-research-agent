# scrapers/wechat/channels_sph_parser.py
"""视频号在线解析模块：sph 分享链接 -> 视频下载 -> 口播转录。

对照 d:\\project\\wx_channels_download\\internal\\api/sph/worker.js。

设计要点（已端到端验证，见记忆 project_wechat_channels_online_parse）：
- 只支持 `weixin.qq.com/sph/xxx` 分享链接。元宝只为 sph 链接铸造 generalToken
  （finder-preview 必需 token，空 token 返回"此内容暂时无法播放"）。
  `channels.weixin.qq.com/...?feedID=...` 这类纯 feedID URL 走不通（无 token 来源）。
- finder-preview 返回的 videoUrl 是 `finder.video.qq.com` 上可直接裸访问的可播放
  MP4，无需 Referer、无需 ISAAC 解密。
- **体积优化（关键）**：不下载视频文件，而是用 ffmpeg 直接从 URL 流式提取音频
  （`-i <url> -vn -acodec aac`），只产出 ~3MB 音频。无论原视频 15MB 还是 208MB
  都不落地，避免磁盘尖峰，也不触发 video_processor 的 200MB 上限。
- **用 videoUrl 默认变体**（h264，音频最好 96-192kbps）。不"选最小变体"——
  最小变体(h265)音频仅 48kb/s，会严重降低 MiMo 转录质量（实测产生元推理/幻觉）。
- 复用 video_processor.VideoProcessor 的 `_audio_to_base64` + `_transcribe_audio_with_mimo`
  做提音频后的 base64 + MiMo 转录。
- 与评论(OCR)解耦：本模块只负责"视频->口播"，不碰评论，不依赖微信客户端。

依赖配置：元宝 cookie 不走 config.ini——由 `python main.py --login-yuanbao` 登录后
存入 Playwright 持久化 profile (cookies/_yuanbao_profile)，解析时 headless 重开
读取（见 `_load_cookie_from_profile`）。MIMO_API_KEY 仍走 config.ini。
"""
import hashlib
import logging
import os
import random
import subprocess
import time
from urllib.parse import urlparse, parse_qs

import requests

import config

logger = logging.getLogger(__name__)


def is_sph_url(url: str) -> bool:
    """是否为视频号 sph 分享链接（weixin.qq.com/sph/xxx）。"""
    return bool(url) and "weixin.qq.com/sph/" in url


def _get_ffmpeg_exe() -> str | None:
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        return get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        import shutil
        return shutil.which("ffmpeg")


def _cleanup(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except Exception:  # noqa: BLE001
        pass


def _resolve_chrome_channel() -> str | None:
    """检测系统 Chrome/Edge，让元宝登录用真实浏览器而非 Playwright 自带 Chromium。

    exe 未 bundle Chromium（dist/playwright_browsers 为空），不传 channel 会
    找不到浏览器可执行文件而启动失败。抖音等 scraper 用 channel="chrome" 走
    系统 Chrome，这里复用同一套检测。
    """
    try:
        from scrapers.base import _get_browser_channel, _detect_browser_channel_fallback
        return _get_browser_channel() or _detect_browser_channel_fallback()
    except Exception:  # noqa: BLE001
        return None


_cookie_cache: str | None = None


async def _load_cookie_from_profile() -> str:
    """从元宝登录态 profile (cookies/_yuanbao_profile) 读取 cookie。

    登录态由 `python main.py --login-yuanbao` 写入：Playwright 持久化 profile，
    Chromium 把 cookie 存进 user_data_dir，关闭即落盘。这里 headless 重开
    同一 profile 取出 cookie，进程级缓存避免每次解析都启动浏览器。
    不走 config.ini，无需人工配置。
    """
    global _cookie_cache
    if _cookie_cache is not None:
        return _cookie_cache
    profile_dir = config.COOKIE_DIR / "_yuanbao_profile"
    if not profile_dir.exists():
        logger.warning(
            "[channels_sph] 未找到元宝登录态 profile (cookies/_yuanbao_profile)，"
            "请先运行: python main.py --login-yuanbao"
        )
        return ""
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            _ch = _resolve_chrome_channel()
            _kw = {"headless": True, "args": ["--no-first-run", "--disable-blink-features=AutomationControlled"]}
            if _ch:
                _kw["channel"] = _ch
            ctx = await pw.chromium.launch_persistent_context(str(profile_dir), **_kw)
            try:
                cookies = await ctx.cookies("https://yuanbao.tencent.com/")
            finally:
                await ctx.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[channels_sph] 读取元宝登录态 cookie 失败: {e}")
        return ""
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    if any(c["name"] in ("hy_token", "hy_user") for c in cookies):
        _cookie_cache = cookie_str
        logger.info(f"[channels_sph] 从登录态读取元宝 cookie ({len(cookie_str)} 字符)")
    else:
        logger.warning(
            "[channels_sph] 登录态未检测到 hy_token，可能未登录，请重跑 --login-yuanbao"
        )
    return cookie_str


# ─── Step 1: 元宝解析分享链接 ────────────────────────────────────────

_PARSE_URL = "https://yuanbao.tencent.com/api/weixin/get_parse_result"
# 反爬 header 照搬 worker.js（硬编码值，不绑定 cookie，实测可用）
_PARSE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "content-type": "application/json",
    "origin": "https://yuanbao.tencent.com",
    "referer": "https://yuanbao.tencent.com/chat/naQivTmsDa/cf4d0079-ed1b-4c55-a3f3-2ca1379727d1",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "t-userid": "b9575f6b0a8c4a55a08096904a5ef20a",
    "x-agentid": "naQivTmsDa/cf4d0079-ed1b-4c55-a3f3-2ca1379727d1",
    "x-commit-tag": "72282a0d",
    "x-device-id": "1921b001708100d7fa310002b9646bd0cc15a3e2e1f",
    "x-hy106": "",
    "x-hy92": "e963067ffa31002b9646bd0c03000008b1951a",
    "x-hy93": "1921b001708100d7fa310002b9646bd0cc15a3e2e1f",
    "x-id": "b9575f6b0a8c4a55a08096904a5ef20a",
    "x-instance-id": "5",
    "x-language": "zh-CN",
    "x-os_version": "Mac OS(10.15.7)-Blink",
    "x-platform": "mac",
    "x-requested-with": "XMLHttpRequest",
    "x-source": "web",
    "x-web-third-source": "main",
    "x-webdriver": "0",
    "x-webversion": "2.69.0",
    "x-ybuitest": "0",
}

# ─── Step 2: finder-preview 取 feed ──────────────────────────────────

_FEED_INFO_URL = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info"
_FEED_INFO_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
    "Origin": "https://channels.weixin.qq.com",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15.7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}


def _gen_rid() -> str:
    ts_hex = format(int(time.time()), "x")
    rand_hex = "".join(random.choice("0123456789abcdef") for _ in range(8))
    return f"{ts_hex}-{rand_hex}"


class ChannelsSphParser:
    """sph 分享链接 -> 口播文字稿 + 元数据。失败返回 None。"""

    def __init__(self, cookie: str = "", video_processor=None):
        self.cookie = cookie
        try:
            from video_processor import VideoProcessor
            self.vp = video_processor or VideoProcessor()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[channels_sph] VideoProcessor 不可用: {e}")
            self.vp = None
        self.timeout = 30
        self._ffmpeg = _get_ffmpeg_exe()
        if not self._ffmpeg:
            logger.warning("[channels_sph] 未找到 ffmpeg，无法提音频")

    # ── 公开入口 ───────────────────────────────────────────────────

    def parse_narration(self, sph_url: str) -> dict | None:
        """解析 sph 链接，从 videoUrl 流式提取音频并转录口播。

        返回 dict: {narration, video_url, description, cover_url, author,
                    comment_count}。失败返回 None。
        """
        if not is_sph_url(sph_url):
            logger.warning(f"[channels_sph] 非 sph 链接，不支持: {sph_url[:60]}")
            return None
        if not self.cookie:
            logger.warning("[channels_sph] 无元宝登录态 cookie，请先运行 python main.py --login-yuanbao")
            return None
        if self.vp is None or not self._ffmpeg:
            logger.warning("[channels_sph] VideoProcessor 或 ffmpeg 不可用，无法转录")
            return None

        audio_path = None
        try:
            parse_data = self._parse_share_url(sph_url)
            token, eid = self._extract_token_eid(parse_data)
            feed_data = self._get_feed_info(eid, token)
            feed = feed_data.get("feedInfo") or {}
            author = (feed_data.get("authorInfo") or {}).get("nickname", "")

            video_url = feed.get("videoUrl", "")
            if not video_url:
                logger.warning(f"[channels_sph] feedInfo 无 videoUrl: {sph_url[:60]}")
                return None

            # 流式提音频（不落地视频）
            audio_path = self._extract_audio_from_url(video_url)
            if not audio_path:
                return None

            narration = self._transcribe_audio(audio_path)
            if narration:
                logger.info(f"[channels_sph] 口播转录完成: {len(narration)} 字")

            return {
                "narration": narration or "",
                "video_url": video_url,
                "description": feed.get("description", "") or "",
                "cover_url": feed.get("coverUrl", "") or "",
                "author": author,
                "comment_count": feed.get("commentCountFmt", "") or "",
            }
        except Exception as e:  # noqa: BLE001
            logger.error(f"[channels_sph] parse failed for {sph_url[:60]}: {e}")
            return None
        finally:
            if audio_path:
                _cleanup(audio_path)

    # ── Step 1 ─────────────────────────────────────────────────────

    def _parse_share_url(self, sph_url: str) -> dict:
        r = requests.post(
            _PARSE_URL,
            headers={**_PARSE_HEADERS, "cookie": self.cookie},
            json={"type": "video_channel_url", "url": sph_url, "scene": 1},
            timeout=self.timeout,
        )
        if r.status_code != 200:
            raise RuntimeError(f"parseShareUrl HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        data = body.get("data") or {}
        if not data.get("wx_export_id"):
            raise RuntimeError(f"parseShareUrl 无 wx_export_id: {str(body)[:200]}")
        logger.debug(f"[channels_sph] step1 exportId={data.get('wx_export_id')}")
        return data

    @staticmethod
    def _extract_token_eid(parse_data: dict) -> tuple[str, str]:
        playable = parse_data.get("playable_url", "")
        token, eid = "", ""
        if playable:
            q = parse_qs(urlparse(playable).query)
            token = q.get("token", [""])[0]
            eid = q.get("eid", [""])[0]
        if not eid:
            eid = parse_data.get("wx_export_id", "")
        return token, eid

    # ── Step 2 ─────────────────────────────────────────────────────

    def _get_feed_info(self, eid: str, token: str) -> dict:
        rid = _gen_rid()
        api_url = (
            _FEED_INFO_URL + f"?_rid={rid}"
            + "&_pageUrl=https:%2F%2Fchannels.weixin.qq.com%2Ffinder-preview%2Fpages%2Ffeed"
        )
        referer = (
            "https://channels.weixin.qq.com/finder-preview/pages/feed"
            "?entry_card_type=48&comment_scene=39&appid=0"
            f"&token={requests.utils.quote(token, safe='')}"
            f"&entry_scene=0&eid={requests.utils.quote(eid, safe='')}"
        )
        r = requests.post(
            api_url,
            headers={**_FEED_INFO_HEADERS, "Referer": referer},
            json={"baseReq": {"generalToken": token}, "exportId": eid},
            timeout=self.timeout,
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(f"getFeedInfo HTTP {r.status_code}: {r.text[:200]}")
        body = r.json()
        data = body.get("data") or {}
        if not data.get("feedInfo"):
            err = data.get("errMsg") or {}
            title = err.get("title", "") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"getFeedInfo 无 feedInfo: {title or str(body)[:200]}")
        logger.debug(f"[channels_sph] step2 videoUrl 已获取")
        return data

    # ─── Step 3: ffmpeg 流式提音频 + MiMo ───────────────────────────

    def _extract_audio_from_url(
        self, video_url: str, max_duration: int = 1800,
    ) -> str | None:
        """ffmpeg 直接从 URL 流式提取音频到本地 m4a，不落地视频文件。"""
        out_dir = config.VIDEO_TEMP_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        url_hash = hashlib.md5(video_url.encode()).hexdigest()[:10]
        out_path = str(out_dir / f"channels_sph_audio_{int(time.time())}_{url_hash}.m4a")

        cmd = [
            self._ffmpeg,
            "-i", video_url,
            "-vn",                       # 丢弃视频，只要音频
            "-acodec", "aac",
            "-b:a", "128k",
            "-t", str(max_duration),     # 上限 30 分钟，防异常 URL 卡死
            "-y", out_path,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=max_duration + 30, errors="replace",
            )
        except subprocess.TimeoutExpired:
            logger.warning(f"[channels_sph] ffmpeg 提音频超时（>{max_duration+30}s）")
            _cleanup(out_path)
            return None
        if proc.returncode != 0 or not os.path.exists(out_path):
            logger.warning(f"[channels_sph] ffmpeg 提音频失败: {proc.stderr[-300:] if proc.stderr else ''}")
            _cleanup(out_path)
            return None
        size = os.path.getsize(out_path)
        if size < 2000:
            logger.warning(f"[channels_sph] 提取音频过小: {size} bytes")
            _cleanup(out_path)
            return None
        logger.debug(f"[channels_sph] 音频提取 {size/1024:.0f}KB -> {out_path}")
        return out_path

    def _transcribe_audio(self, audio_path: str) -> str | None:
        """音频文件 -> base64 -> MiMo 转录（复用 VideoProcessor 私有方法）。"""
        vp = self.vp
        if vp is None:
            return None
        # 优先走 audio 转录；_transcribe_with_mimo 需要 video_path 会重新提音频，
        # 这里已有音频，直接用 audio 链路。
        audio_b64 = vp._audio_to_base64(audio_path)
        if not audio_b64:
            return None
        return vp._transcribe_audio_with_mimo(audio_b64)

    # ─── 交互式登录（获取元宝 cookie）──────────────────────────────

    @classmethod
    async def login_interactive(cls, wait_seconds: int = 300) -> bool:
        """交互式登录腾讯元宝 web，cookie 落入登录态 profile（不写 config.ini）。

        流程对照 BaseScraper.login_interactive：持久化 profile（cookies/_yuanbao_profile）
        + 打开 yuanbao.tencent.com + 自动检测 hy_token cookie（也可按 Enter 提前确认）。
        cookie 由 Chromium 持久化 profile 自动落盘；解析时 `_load_cookie_from_profile`
        headless 重开读取。无需人工配置 config.ini。
        用法: python main.py --login-yuanbao
        """
        import asyncio
        import threading
        from playwright.async_api import async_playwright

        profile_dir = config.COOKIE_DIR / "_yuanbao_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as pw:
            _ch = _resolve_chrome_channel()
            _kw = {
                "headless": False,
                "viewport": {"width": 1280, "height": 900},
                "user_agent": config.DEFAULT_USER_AGENT,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-first-run",
                ],
            }
            if _ch:
                _kw["channel"] = _ch
            ctx = await pw.chromium.launch_persistent_context(str(profile_dir), **_kw)
            page = await ctx.new_page()
            try:
                await page.goto("https://yuanbao.tencent.com/", timeout=30000)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[channels_sph] 打开元宝页面失败: {e}")

            print("\n" + "=" * 56)
            print("  [元宝登录] 浏览器已打开，请在页面扫码登录腾讯元宝")
            print("  登录后自动检测（检测到 hy_token 即保存），或回终端按 Enter 确认")
            print(f"  最多等待 {wait_seconds} 秒")
            print("=" * 56)

            # 手动 Enter 确认（后台线程，daemon 不阻塞退出）
            enter_event = threading.Event()

            def _wait_enter():
                try:
                    input()
                except Exception:  # noqa: BLE001
                    pass
                enter_event.set()

            threading.Thread(target=_wait_enter, daemon=True).start()

            logged = False
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                try:
                    cs = await ctx.cookies("https://yuanbao.tencent.com/")
                except Exception:  # noqa: BLE001
                    cs = []
                if any(c["name"] in ("hy_token", "hy_user") for c in cs):
                    logged = True
                    break
                if enter_event.is_set():
                    break
                await asyncio.sleep(2)

            cookies = await ctx.cookies("https://yuanbao.tencent.com/")
            await ctx.close()

        if not cookies:
            print("[channels_sph] 未获取到元宝 cookie")
            return False

        # cookie 已由持久化 profile 自动落盘（cookies/_yuanbao_profile），
        # 解析时 headless 重开读取，无需写 config.ini
        has_auth = any(c["name"] in ("hy_token", "hy_user") for c in cookies)
        if has_auth:
            profile_path = config.COOKIE_DIR / "_yuanbao_profile"
            print(f"[channels_sph] 元宝登录态已保存在 {profile_path}")
            print("[channels_sph] 后续视频号解析自动读取，无需配置 config.ini")
        else:
            print("[channels_sph] 警告：未检测到 hy_token，可能未登录成功，请重试。")
        return has_auth
