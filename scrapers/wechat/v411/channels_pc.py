# scrapers/wechat_channels_pc.py
"""WeChat Channels (视频号) comment scraper — PC desktop.

Uses relative coordinates and OCR to open 视频号 content in WeChat,
extract comments, and optionally capture video.
"""
import logging
import os
import subprocess
import time

import pyautogui

import config
from scrapers.wechat.v411.base import WechatPcBaseScraper

logger = logging.getLogger(__name__)


class WechatChannelsPcScraper(WechatPcBaseScraper):
    """Scrape comments from WeChat Channels (视频号) via PC desktop.

    Flow:
      1. Activate WeChat window
      2. Click search bar, paste video URL, press Enter
      3. Click "访问网页" to open video (auto-plays)
      4. Record screen + system audio → MiMo transcription (口播)
      5. Pause video, click comment button
      6. OCR screenshot comment panel, scroll to load more, expand replies
      7. Return {"post_content": "", "comments": [...], "narration": "...", "video_url": None}
    """

    platform_name = "wechat_channels"

    async def scrape(self, url: str) -> dict:
        """Scrape 视频号 comments + narration."""
        # Delay between URLs
        if self._scrape_count > 0:
            import random
            delay = random.uniform(3, 6)
            logger.info(f"[wechat_channels] Waiting {delay:.1f}s before next URL")
            time.sleep(delay)
        self._scrape_count += 1

        try:
            # Step 1: Activate WeChat window (must be foreground for paste/Enter)
            if not self.window_mgr.activate():
                raise RuntimeError(
                    "无法将微信窗口切换到前台。请关闭其他可能拦截焦点的窗口后重试。"
                )
            if self._scrape_count == 1:
                self.reacquire_window()
            time.sleep(0.8)  # let WeChat settle as foreground before clicking

            # Step 2: Open video (refreshes rect after window expands)
            self._open_video(url)

            # Step 3: Extract narration (record screen + audio → MiMo transcription)
            narration = self._extract_narration()

            # Step 4: Pause video (prevent auto-advance) and open comment panel
            self._pause_video()
            self._open_comment_panel()

            # Step 5: Extract comments
            comments = self._extract_comments()
            logger.info(f"[wechat_channels] Total comments: {len(comments)}")

            # Debug screenshot
            self.take_screenshot("final")

            return {
                "post_content": "",
                "comments": comments,
                "narration": narration or "",
                "video_url": None,
            }

        except Exception as e:
            logger.error(f"[wechat_channels] Failed to scrape {url}: {e}")
            self.take_screenshot("error")
            raise

    # ── Navigation ───────────────────────────────────────────────

    def _open_video(self, url: str) -> None:
        """Open 视频号 URL via WeChat search bar."""
        # Reset to narrow chat-list view if window is in expanded state
        # (e.g. from a previous video).  Expanded windows have a side
        # panel that interferes with search result display.
        if self._rect.width > 1100:
            logger.info(
                "[wechat_channels] Window is expanded "
                f"({self._rect.width}px), pressing Escape to reset"
            )
            pyautogui.press("escape")
            time.sleep(0.8)
            self.refresh_rect()
            logger.info(
                f"[wechat_channels] After Escape, window: "
                f"({self._rect.left}, {self._rect.top}) "
                f"{self._rect.width}x{self._rect.height}"
            )

        # Two clicks needed — first opens the search sub-page,
        # second focuses the input field
        self.click_key("wechat_main", "search_bar", "搜索栏")
        time.sleep(0.4)
        self.click_key("wechat_main", "search_bar", "搜索栏")
        time.sleep(0.4)
        self.clear_input()

        # Paste URL and press Enter (uses keybd_event)
        self.paste_and_enter(url)
        time.sleep(2)

        # Click search result — try OCR first, fallback to coordinate
        region_left = self._rect.left
        region_top = self._rect.top
        region_w = self._rect.width
        region_h = self._rect.height // 2
        if not self.click_text_via_ocr(
            "访问网页", (region_left, region_top, region_w, region_h)
        ):
            logger.warning(
                "[wechat_channels] OCR missed '访问网页', trying calibrated coord"
            )
            self.click_key("wechat_main", "result_open_entry", "访问网页(兜底)")

        # Wait for video page to load and window to expand
        time.sleep(5)
        self.refresh_rect()
        logger.info(
            f"[wechat_channels] After video open, window: "
            f"({self._rect.left}, {self._rect.top}) "
            f"{self._rect.width}x{self._rect.height}"
        )

    def _pause_video(self) -> None:
        """Click video area to pause playback."""
        self.refresh_rect()
        self.click_key("channels", "pause_video", "暂停视频")
        time.sleep(1)

    def _open_comment_panel(self) -> None:
        """Click the comment button."""
        self.click_key("channels", "comment_button", "评论按钮")
        time.sleep(2)

    # ── Narration extraction ──────────────────────────────────────

    def _find_audio_device(self) -> str | None:
        """Auto-detect a WASAPI loopback audio device via ffmpeg.

        Returns the device name string (e.g. "扬声器 (Realtek...)") or
        None if no loopback-capable device is found.
        """
        # Check config override first
        profile = self._profile.get("channels", {})
        cfg_device = profile.get("audio_device", "")
        if cfg_device:
            logger.info(f"[wechat_channels] Using configured audio device: {cfg_device}")
            return cfg_device

        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            ffmpeg = get_ffmpeg_exe()
            result = subprocess.run(
                [ffmpeg, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                capture_output=True, text=True, timeout=15,
            )
            # ffmpeg writes device list to stderr
            output = result.stderr
        except Exception as e:
            logger.warning(f"[wechat_channels] ffmpeg device listing failed: {e}")
            return None

        # Parse device names — look for lines containing "Loopback" or
        # common Chinese loopback device markers like "扬声器" / "立体声混音"
        candidates = []
        for line in output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # ffmpeg dshow device names appear in quoted strings like:
            # [dshow @ ...] "扬声器 (Realtek High Definition Audio)"
            if '"' in stripped:
                name = stripped.split('"')[1]
                lower = name.lower()
                if "loopback" in lower or "扬声器" in name or "立体声混音" in name or "stereo mix" in lower:
                    candidates.append(name)

        if candidates:
            # Prefer loopback → 立体声混音 → 扬声器
            for name in candidates:
                if "loopback" in name.lower() or "立体声混音" in name:
                    logger.info(f"[wechat_channels] Auto-detected audio device: {name}")
                    return name
            logger.info(f"[wechat_channels] Auto-detected audio device: {candidates[0]}")
            return candidates[0]

        # If no loopback found, try to find ANY audio input device as fallback
        for line in output.splitlines():
            stripped = line.strip()
            if '"' in stripped and ("DirectSound" in stripped or "audio" in stripped.lower()):
                name = stripped.split('"')[1]
                logger.info(f"[wechat_channels] Fallback audio device: {name}")
                return name

        logger.warning("[wechat_channels] No suitable audio device found")
        return None

    def _detect_video_end(
        self, region: tuple[int, int, int, int],
        max_duration: int, poll_interval: float = 1.0,
        still_threshold: float = 3.0,
    ) -> bool:
        """Poll video area screenshots; return True when the video has ended.

        Compares consecutive screenshots.  If they remain similar for
        ``still_threshold`` seconds, the video is considered finished.
        Returns early if ``max_duration`` is reached.
        """
        from PIL import ImageChops, ImageStat

        left, top, width, height = region
        still_seconds = 0.0
        elapsed = 0.0
        prev_gray = None

        logger.info(
            f"[wechat_channels] Monitoring video end: max={max_duration}s, "
            f"still_threshold={still_threshold}s"
        )

        while elapsed < max_duration:
            time.sleep(poll_interval)
            elapsed += poll_interval

            img = pyautogui.screenshot(region=(left, top, width, height))
            gray = img.convert("L")

            if prev_gray is not None:
                diff = ImageChops.difference(gray, prev_gray)
                stat = ImageStat.Stat(diff)
                avg_diff = stat.mean[0]  # 0 … 255

                if avg_diff < 3:  # very small change → video is paused/ended
                    still_seconds += poll_interval
                    if still_seconds >= still_threshold:
                        logger.info(
                            f"[wechat_channels] Video ended after {elapsed:.0f}s "
                            f"(still for {still_seconds:.0f}s, avg_diff={avg_diff:.1f})"
                        )
                        return True
                else:
                    still_seconds = 0.0  # reset — video is still playing
            else:
                prev_gray = gray

        logger.info(f"[wechat_channels] Max duration {max_duration}s reached")
        return False  # timed out

    def _record_video_area(
        self,
        region: tuple[int, int, int, int],
        output_path: str,
        audio_device: str | None,
        max_duration: int = 300,
        fps: int = 5,
    ) -> bool:
        """Record the video area + system audio to a file via ffmpeg.

        Starts ffmpeg in the background, then polls for video end via
        ``_detect_video_end``.  Stops ffmpeg gracefully when done.

        Clicks the pause area immediately when the video ends, to prevent
        WeChat from auto-advancing to the next video.

        Returns True if recording completed successfully.
        """
        left, top, width, height = region

        try:
            from imageio_ffmpeg import get_ffmpeg_exe
            ffmpeg_exe = get_ffmpeg_exe()
        except ImportError:
            logger.warning("[wechat_channels] imageio_ffmpeg not installed, cannot record")
            return False

        # Build ffmpeg command
        cmd = [
            ffmpeg_exe,
            "-f", "gdigrab",
            "-framerate", str(fps),
            "-offset_x", str(left),
            "-offset_y", str(top),
            "-video_size", f"{width}x{height}",
            "-i", "desktop",
        ]

        if audio_device:
            cmd += [
                "-f", "dshow",
                "-i", f"audio={audio_device}",
            ]

        cmd += [
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "35",
            "-pix_fmt", "yuv420p",
        ]

        if audio_device:
            cmd += ["-c:a", "aac", "-b:a", "64k"]
        else:
            cmd += ["-an"]

        cmd += [
            "-t", str(max_duration),
            "-y",
            output_path,
        ]

        logger.info(
            f"[wechat_channels] Starting ffmpeg recording: "
            f"region=({left},{top}) {width}x{height}, "
            f"fps={fps}, max={max_duration}s, audio={'yes' if audio_device else 'no'}"
        )
        logger.debug(f"[wechat_channels] ffmpeg cmd: {' '.join(cmd)}")

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.warning(f"[wechat_channels] Failed to start ffmpeg: {e}")
            return False

        # Wait a moment for ffmpeg to initialize
        time.sleep(1.0)

        # Monitor for video end
        video_ended = False
        try:
            video_ended = self._detect_video_end(region, max_duration)
        finally:
            # ── CRITICAL: pause immediately to prevent auto-advance ──
            if video_ended:
                logger.info("[wechat_channels] Video ended, pausing to prevent auto-advance")
                self._pause_video()

            # Stop ffmpeg gracefully — send 'q' to stdin
            try:
                proc.stdin.write(b"q")
                proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("[wechat_channels] ffmpeg did not exit, killing")
                proc.kill()
                proc.wait()

        # Verify output file exists and has content
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(f"[wechat_channels] Recording saved: {output_path} ({size_mb:.1f}MB)")
            return True

        logger.warning("[wechat_channels] Recording file missing or too small")
        return False

    def _extract_narration(self, max_duration: int = 300) -> str | None:
        """Extract video narration (口播) via screen recording + MiMo transcription.

        Flow:
          1. Resolve video area region from config
          2. Detect system audio loopback device
          3. Start ffmpeg screen recording (gdigrab + dshow)
          4. Wait for video to end (pixel-diff) or timeout
          5. Extract audio from recording
          6. Send audio to MiMo for transcription
          7. Return transcribed text or None

        The video auto-plays when opened, so we start recording immediately.
        After recording, the video is paused to prevent auto-advance.
        """
        # Resolve video area region
        try:
            region_left, region_top, region_w, region_h = \
                self.resolve_region("channels", "video_area")
        except Exception:
            logger.warning(
                "[wechat_channels] video_area not calibrated, "
                "skipping narration extraction"
            )
            return None

        if region_w < 50 or region_h < 50:
            logger.warning(
                "[wechat_channels] video_area too small "
                f"({region_w}x{region_h}), skipping narration"
            )
            return None

        region = (region_left, region_top, region_w, region_h)
        logger.info(
            f"[wechat_channels] Video area: ({region_left},{region_top}) "
            f"{region_w}x{region_h}"
        )

        # Find audio device
        audio_device = self._find_audio_device()
        if not audio_device:
            logger.warning(
                "[wechat_channels] No loopback audio device found — "
                "recording video-only. MiMo will try video understanding "
                "as fallback."
            )

        # Record
        temp_dir = config.VIDEO_TEMP_DIR
        temp_dir.mkdir(parents=True, exist_ok=True)
        video_path = str(temp_dir / f"channels_narration_{int(time.time())}.mp4")
        recorded = self._record_video_area(region, video_path, audio_device, max_duration)

        if not recorded:
            # Clean up empty file
            self._cleanup_file(video_path)
            return None

        # Transcribe via MiMo
        try:
            from video_processor import VideoProcessor
            vp = VideoProcessor()
            transcription = vp._transcribe_with_mimo(video_path)
        except Exception as e:
            logger.warning(f"[wechat_channels] MiMo transcription failed: {e}")
            transcription = None
        finally:
            self._cleanup_file(video_path)

        if transcription:
            logger.info(
                f"[wechat_channels] Narration transcribed: "
                f"{len(transcription)} chars"
            )
        else:
            logger.warning("[wechat_channels] Narration transcription returned empty")

        return transcription

    @staticmethod
    def _cleanup_file(path: str) -> None:
        """Remove a temp file, ignoring errors."""
        try:
            if path and os.path.exists(path):
                os.unlink(path)
        except Exception:
            pass

    # ── Comment extraction ───────────────────────────────────────

    def _measure_px_per_click(
        self, left: int, top: int, width: int, height: int,
        cx: int, cy: int, test_clicks: int = 3,
    ) -> float:
        """Measure actual pixels scrolled per wheel click in the panel.

        Takes a screenshot, scrolls ``test_clicks`` clicks, takes
        another, then finds the vertical pixel offset by searching for
        a horizontal strip of the first image in the second.

        Falls back to 25 px/click when the panel is too empty to
        measure reliably.
        """
        from PIL import Image
        import numpy as np

        # Take before screenshot
        before = pyautogui.screenshot(region=(left, top, width, height))
        before_gray = np.array(before.convert("L"), dtype=np.int16)

        # Scroll a known number of clicks
        self._scroll_spaced(test_clicks, cx, cy)
        time.sleep(0.5)

        # Take after screenshot
        after = pyautogui.screenshot(region=(left, top, width, height))
        after_gray = np.array(after.convert("L"), dtype=np.int16)

        # Save debug images
        debug_dir = config.OUTPUT_DIR / "debug" / "wechat_pc"
        debug_dir.mkdir(parents=True, exist_ok=True)
        before.save(str(debug_dir / f"{self.platform_name}_measure_before.png"))
        after.save(str(debug_dir / f"{self.platform_name}_measure_after.png"))

        # --- Find vertical offset by template matching ---
        # Use a horizontal strip from the top of 'before' as the template
        strip_h = min(40, height // 4)
        if strip_h < 10:
            logger.warning("[wechat_channels] Panel too short to measure scroll")
            return 25.0

        template = before_gray[:strip_h, :]  # top strip
        best_offset = 0
        best_diff = float("inf")

        # Search for the template in the after image
        max_search = height - strip_h
        for offset in range(0, max_search, 1):
            candidate = after_gray[offset:offset + strip_h, :]
            diff = np.sum(np.abs(template - candidate))
            if diff < best_diff:
                best_diff = diff
                best_offset = offset

        # If best match is at the very top (offset ≈ 0), the panel may be
        # static (empty or at bottom).  Fall back to conservative default.
        if best_offset <= 2:
            logger.warning(
                "[wechat_channels] Scroll measurement inconclusive "
                f"(best_offset={best_offset}), using conservative 25 px/click"
            )
            return 25.0

        px_per_click = best_offset / test_clicks
        logger.info(
            f"[wechat_channels] Measured scroll: {best_offset}px over "
            f"{test_clicks} clicks → {px_per_click:.1f} px/click"
        )
        return px_per_click

    def _extract_comments(
        self, max_scrolls: int = 20, scroll_fraction: float = 0.67,
    ) -> list[tuple[str, str]]:
        """OCR extract comments from the comment panel. Scrolls to load more.

        Measures the actual scroll distance per wheel click, then uses
        ``scroll_fraction`` of the region height (default ⅔ screen) per
        step.  The 视频号 comment panel has no visible scrollbar, so
        scrollbar-drag is not an option here.

        Also finds and clicks "展开"/"x条回复" buttons before each OCR
        pass, to expand nested replies.
        """
        from scrapers.wechat.ocr import merge_comment_fragments

        raw: list[tuple[str, str]] = []
        no_change_count = 0
        total_expanded = 0

        # Resolve region once — used for both OCR and scroll position
        region_left, region_top, region_w, region_h = \
            self.resolve_region("channels", "comment_panel_region")
        cx = region_left + region_w // 2
        cy = region_top + region_h // 2

        # Anchor the mouse cursor on the comment panel centre so wheel
        # events reach the panel rather than the video / background.
        pyautogui.moveTo(cx, cy, duration=0.1)
        time.sleep(0.2)

        # Click top-right corner to focus the comment panel (safe from links/images)
        focus_x = region_left + region_w - 10
        focus_y = region_top + 10
        pyautogui.click(focus_x, focus_y)
        time.sleep(0.3)

        # Measure actual px/click from the live panel (falls back to 25)
        px_per_click = self._measure_px_per_click(
            region_left, region_top, region_w, region_h, cx, cy,
        )

        # Wheel clicks per scroll step → scroll_fraction of region height
        target_px = round(region_h * scroll_fraction)
        scroll_clicks = max(1, round(target_px / px_per_click)) * 30
        logger.info(
            f"[wechat_channels] Scroll step: {scroll_clicks} clicks "
            f"(target={target_px}px, measured={px_per_click:.1f} px/click, "
            f"region_h={region_h}px, fraction={scroll_fraction:.0%})"
        )

        for i in range(max_scrolls):
            # ── Expand replies before OCR ──
            buttons = self.find_expand_buttons(
                "channels", "comment_panel_region", f"expand_{i:02d}"
            )
            if buttons:
                logger.info(
                    f"[wechat_channels] Scroll {i+1}: "
                    f"{len(buttons)} expand buttons"
                )
                for btn in buttons:
                    self.click_point(
                        btn["screen_x"], btn["screen_y"],
                        f"展开:{btn['text']}",
                    )
                    time.sleep(0.3)
                total_expanded += len(buttons)
                time.sleep(0.8)  # wait for expansion animation
                # Expanding adds new content, reset stale counter
                no_change_count = 0

            # Screenshot & OCR the comment panel region
            batch = self.ocr_region(
                "channels", "comment_panel_region", f"comments_{i:02d}"
            )
            raw.extend(batch)

            # Check if new comments appeared
            if len(batch) == 0:
                no_change_count += 1
                if no_change_count >= 5:
                    logger.debug("[wechat_channels] No new comments after 5 scrolls")
                    break
            else:
                no_change_count = 0

            # Scroll down in comment panel — one big wheel burst
            pyautogui.scroll(-scroll_clicks, x=cx, y=cy)
            time.sleep(1.5)

            if i > 0 and i % 5 == 4:
                logger.debug(
                    f"[wechat_channels] Scroll {i+1}/{max_scrolls}, "
                    f"raw pairs: {len(raw)}, expanded: {total_expanded}"
                )

        logger.info(
            f"[wechat_channels] Expand done: {total_expanded} total expanded"
        )
        return merge_comment_fragments(raw)
