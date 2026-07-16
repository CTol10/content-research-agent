# scrapers/wechat/v417/channels_pc.py
"""WeChat 4.1.7 Channels (视频号) scraper — dual-window.

4.1.7 opens video content in a SEPARATE Chrome_WidgetWin_0 popup.
Narration recording captures from the popup's video area.
Comment extraction and expand logic are identical to 4.1.11.
"""
import logging
import os
import subprocess
import time

import pyautogui

import config
from scrapers.wechat.v417.base import WechatPcBaseScraperV417

logger = logging.getLogger(__name__)


class WechatChannelsPcScraperV417(WechatPcBaseScraperV417):
    """Scrape 视频号 comments + narration via 4.1.7 dual-window flow."""

    platform_name = "wechat_channels_v417"

    async def scrape(self, url: str) -> dict:
        if self._scrape_count > 0:
            import random
            delay = random.uniform(3, 6)
            logger.info(f"[wechat_channels_v417] Waiting {delay:.1f}s before next URL")
            time.sleep(delay)
        self._scrape_count += 1

        try:
            # Step 1: Activate main window
            if not self.window_mgr.activate():
                raise RuntimeError("无法激活微信主窗口")
            if self._scrape_count == 1:
                self.window_mgr.find_main()
            time.sleep(0.8)

            # Step 2: Open video in main window
            self._open_video(url)

            # Step 3: Wait for popup
            if not self.window_mgr.wait_for_popup(timeout=15):
                raise RuntimeError("视频弹窗未出现")

            # Step 4: Activate popup, extract narration
            self.window_mgr.activate_popup()
            time.sleep(1.0)
            narration = self._extract_narration()

            # Step 5: Pause, open comment panel, extract comments
            self._pause_video()
            self._open_comment_panel()
            comments = self._extract_comments()
            logger.info(f"[wechat_channels_v417] Total comments: {len(comments)}")

            self.take_screenshot("final")

            return {
                "post_content": "",
                "comments": comments,
                "narration": narration or "",
                "video_url": None,
            }

        except Exception as e:
            logger.error(f"[wechat_channels_v417] Failed to scrape {url}: {e}")
            self.take_screenshot("error")
            raise

    # ── Navigation ─────────────────────────────────────────────────

    def _open_video(self, url: str) -> None:
        """Open 视频号 URL via main window search bar."""
        # Two clicks on search bar
        self.click_key("wechat_main", "search_bar", "搜索栏")
        time.sleep(0.4)
        self.click_key("wechat_main", "search_bar", "搜索栏")
        time.sleep(0.4)
        self.clear_input()

        self.paste_and_enter(url)
        time.sleep(2)

        main_rect = self.window_mgr.get_main_rect()
        region = (
            main_rect.left, main_rect.top,
            main_rect.width, main_rect.height // 2,
        )
        if not self.click_text_via_ocr("访问网页", region):
            logger.warning("[wechat_channels_v417] OCR missed '访问网页', trying coord")
            self.click_key("wechat_main", "result_open_entry", "访问网页(兜底)")

        time.sleep(2)

    def _pause_video(self) -> None:
        """Click video area to pause."""
        self.click_key("channels", "pause_video", "暂停视频")
        time.sleep(1)

    def _open_comment_panel(self) -> None:
        """Click the comment button in the popup."""
        self.click_key("channels", "comment_button", "评论按钮")
        time.sleep(2)

    # ── Narration extraction ───────────────────────────────────────

    def _find_audio_device(self) -> str | None:
        """Auto-detect WASAPI loopback audio device via ffmpeg."""
        profile = self._profile.get("channels", {})
        cfg_device = profile.get("audio_device", "")
        if cfg_device:
            return cfg_device

        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            ffmpeg = get_ffmpeg_exe()
            result = subprocess.run(
                [ffmpeg, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                capture_output=True, text=True, timeout=15,
            )
            output = result.stderr
        except Exception as e:
            logger.warning(f"[wechat_channels_v417] ffmpeg device list failed: {e}")
            return None

        candidates = []
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped or '"' not in stripped:
                continue
            name = stripped.split('"')[1]
            lower = name.lower()
            if "loopback" in lower or "扬声器" in name or "立体声混音" in name or "stereo mix" in lower:
                candidates.append(name)

        if candidates:
            for name in candidates:
                if "loopback" in name.lower() or "立体声混音" in name:
                    return name
            return candidates[0]

        return None

    def _detect_video_end(
        self, region: tuple[int, int, int, int],
        max_duration: int, poll_interval: float = 1.0,
        still_threshold: float = 3.0,
    ) -> bool:
        """Poll video area; return True when video has ended."""
        from PIL import ImageChops, ImageStat

        left, top, width, height = region
        still_seconds = 0.0
        elapsed = 0.0
        prev_gray = None

        while elapsed < max_duration:
            time.sleep(poll_interval)
            elapsed += poll_interval
            img = pyautogui.screenshot(region=(left, top, width, height))
            gray = img.convert("L")
            if prev_gray is not None:
                diff = ImageChops.difference(gray, prev_gray)
                stat = ImageStat.Stat(diff)
                avg_diff = stat.mean[0]
                if avg_diff < 3:
                    still_seconds += poll_interval
                    if still_seconds >= still_threshold:
                        logger.info(
                            f"[wechat_channels_v417] Video ended after {elapsed:.0f}s"
                        )
                        return True
                else:
                    still_seconds = 0.0
            else:
                prev_gray = gray

        logger.info(f"[wechat_channels_v417] Max duration {max_duration}s reached")
        return False

    def _record_video_area(
        self, region: tuple[int, int, int, int],
        output_path: str, audio_device: str | None,
        max_duration: int = 300, fps: int = 5,
    ) -> bool:
        """Record popup video area + system audio via ffmpeg."""
        left, top, width, height = region

        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            ffmpeg_exe = get_ffmpeg_exe()
        except ImportError:
            logger.warning("[wechat_channels_v417] imageio_ffmpeg not installed")
            return False

        cmd = [
            ffmpeg_exe,
            "-f", "gdigrab", "-framerate", str(fps),
            "-offset_x", str(left), "-offset_y", str(top),
            "-video_size", f"{width}x{height}", "-i", "desktop",
        ]
        if audio_device:
            cmd += ["-f", "dshow", "-i", f"audio={audio_device}"]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "35", "-pix_fmt", "yuv420p"]
        if audio_device:
            cmd += ["-c:a", "aac", "-b:a", "64k"]
        else:
            cmd += ["-an"]
        cmd += ["-t", str(max_duration), "-y", output_path]

        logger.info(
            f"[wechat_channels_v417] Recording: ({left},{top}) {width}x{height}, "
            f"max={max_duration}s, audio={'yes' if audio_device else 'no'}"
        )

        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.warning(f"[wechat_channels_v417] ffmpeg start failed: {e}")
            return False

        time.sleep(1.0)

        video_ended = False
        try:
            video_ended = self._detect_video_end(region, max_duration)
        finally:
            if video_ended:
                logger.info("[wechat_channels_v417] Pausing to prevent auto-advance")
                self._pause_video()
            try:
                proc.stdin.write(b"q")
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(f"[wechat_channels_v417] Recording saved: {size_mb:.1f}MB")
            return True
        return False

    def _extract_narration(self, max_duration: int = 300) -> str | None:
        """Extract video narration via screen recording + MiMo."""
        try:
            region_left, region_top, region_w, region_h = \
                self.resolve_region("channels", "video_area")
        except Exception:
            logger.warning("[wechat_channels_v417] video_area not calibrated")
            return None

        if region_w < 50 or region_h < 50:
            return None

        region = (region_left, region_top, region_w, region_h)
        audio_device = self._find_audio_device()

        temp_dir = config.VIDEO_TEMP_DIR
        temp_dir.mkdir(parents=True, exist_ok=True)
        video_path = str(temp_dir / f"channels_narration_v417_{int(time.time())}.mp4")

        if not self._record_video_area(region, video_path, audio_device, max_duration):
            self._cleanup_file(video_path)
            return None

        try:
            from video_processor import VideoProcessor
            vp = VideoProcessor()
            transcription = vp._transcribe_with_mimo(video_path)
        except Exception as e:
            logger.warning(f"[wechat_channels_v417] MiMo failed: {e}")
            transcription = None
        finally:
            self._cleanup_file(video_path)

        if transcription:
            logger.info(f"[wechat_channels_v417] Narration: {len(transcription)} chars")
        return transcription

    @staticmethod
    def _cleanup_file(path: str) -> None:
        try:
            if path and os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass

    # ── Comment extraction ─────────────────────────────────────────

    def _measure_px_per_click(
        self, left: int, top: int, width: int, height: int,
        cx: int, cy: int, test_clicks: int = 3,
    ) -> float:
        """Measure actual pixels scrolled per wheel click."""
        from PIL import Image
        import numpy as np

        before = pyautogui.screenshot(region=(left, top, width, height))
        before_gray = np.array(before.convert("L"), dtype=np.int16)

        self._scroll_spaced(test_clicks, cx, cy)
        time.sleep(0.5)

        after = pyautogui.screenshot(region=(left, top, width, height))
        after_gray = np.array(after.convert("L"), dtype=np.int16)

        strip_h = min(40, height // 4)
        if strip_h < 10:
            return 25.0

        template = before_gray[:strip_h, :]
        best_offset = 0
        best_diff = float("inf")
        max_search = height - strip_h
        for offset in range(0, max_search, 1):
            candidate = after_gray[offset:offset + strip_h, :]
            diff = np.sum(np.abs(template - candidate))
            if diff < best_diff:
                best_diff = diff
                best_offset = offset

        if best_offset <= 2:
            return 25.0

        px_per_click = best_offset / test_clicks
        logger.info(
            f"[wechat_channels_v417] Measured scroll: {best_offset}px "
            f"over {test_clicks} clicks → {px_per_click:.1f} px/click"
        )
        return px_per_click

    def _extract_comments(
        self, max_scrolls: int = 20, scroll_fraction: float = 0.67,
    ) -> list[tuple[str, str]]:
        """OCR extract comments from the popup comment panel."""
        from scrapers.wechat.ocr import merge_comment_fragments

        raw: list[tuple[str, str]] = []
        no_change_count = 0
        total_expanded = 0

        region_left, region_top, region_w, region_h = \
            self.resolve_region("channels", "comment_panel_region")
        cx = region_left + region_w // 2
        cy = region_top + region_h // 2

        pyautogui.moveTo(cx, cy, duration=0.1)
        time.sleep(0.2)

        px_per_click = self._measure_px_per_click(
            region_left, region_top, region_w, region_h, cx, cy,
        )

        target_px = round(region_h * scroll_fraction)
        scroll_clicks = max(1, round(target_px / px_per_click)) * 30
        logger.info(
            f"[wechat_channels_v417] Scroll step: {scroll_clicks} clicks "
            f"(target={target_px}px, measured={px_per_click:.1f} px/click)"
        )

        for i in range(max_scrolls):
            # Expand replies
            buttons = self.find_expand_buttons(
                "channels", "comment_panel_region", f"expand_{i:02d}"
            )
            if buttons:
                logger.info(
                    f"[wechat_channels_v417] Scroll {i+1}: "
                    f"{len(buttons)} expand buttons"
                )
                for btn in buttons:
                    self.click_point(btn["screen_x"], btn["screen_y"], f"展开:{btn['text']}")
                    time.sleep(0.3)
                total_expanded += len(buttons)
                time.sleep(0.8)

            batch = self.ocr_region("channels", "comment_panel_region", f"comments_{i:02d}")
            raw.extend(batch)

            if len(batch) == 0:
                no_change_count += 1
                if no_change_count >= 3:
                    break
            else:
                no_change_count = 0

            pyautogui.scroll(-scroll_clicks, x=cx, y=cy)
            time.sleep(1.5)

            if i > 0 and i % 5 == 4:
                logger.debug(
                    f"[wechat_channels_v417] Scroll {i+1}/{max_scrolls}, "
                    f"raw pairs: {len(raw)}, expanded: {total_expanded}"
                )

        logger.info(f"[wechat_channels_v417] Expand done: {total_expanded} total")
        return merge_comment_fragments(raw)
