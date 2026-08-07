# video_processor.py
"""Video processing pipeline: download -> MiMo-v2.5 video understanding."""
import base64
import hashlib
import logging
import os
from pathlib import Path

import httpx
import requests

import config

logger = logging.getLogger(__name__)


class VideoProcessor:
    def __init__(self):
        self.temp_dir = config.VIDEO_TEMP_DIR
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.last_audio_base64 = None  # audio base64 from last process_video call

    def download_video(self, url: str, referer: str = None) -> str | None:
        """Download video to temp directory. Returns file path or None on failure."""
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        ext = ".mp4"
        for e in config.VIDEO_EXTENSIONS:
            if e in url.lower():
                ext = e
                break
        video_path = str(self.temp_dir / f"video_{url_hash}{ext}")

        headers = {"User-Agent": config.DEFAULT_USER_AGENT}
        if referer:
            headers["Referer"] = referer
        else:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            headers["Referer"] = f"{parsed.scheme}://{parsed.hostname}/"

        try:
            with httpx.Client(timeout=config.VIDEO_DOWNLOAD_TIMEOUT, follow_redirects=True, headers=headers) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()

                    content_length = response.headers.get("content-length")
                    if content_length:
                        size_mb = int(content_length) / (1024 * 1024)
                        if size_mb > config.MAX_VIDEO_SIZE_MB:
                            logger.warning(f"Video too large: {size_mb:.1f}MB > {config.MAX_VIDEO_SIZE_MB}MB limit")
                            return None

                    downloaded = 0
                    max_bytes = config.MAX_VIDEO_SIZE_MB * 1024 * 1024
                    with open(video_path, "wb") as f:
                        for chunk in response.iter_bytes(chunk_size=8192):
                            downloaded += len(chunk)
                            if downloaded > max_bytes:
                                logger.warning(f"Video download exceeded size limit, aborting")
                                f.close()
                                os.unlink(video_path)
                                return None
                            f.write(chunk)

            logger.info(f"Video downloaded: {video_path} ({downloaded / 1024 / 1024:.1f}MB)")
            return video_path

        except Exception as e:
            logger.warning(f"Video download failed: {e}")
            if os.path.exists(video_path):
                os.unlink(video_path)
            return None

    def _video_to_base64(self, video_path: str) -> str | None:
        """Encode video file to base64 data URL. Returns None if too large."""
        size_mb = os.path.getsize(video_path) / (1024 * 1024)
        if size_mb > config.VIDEO_BASE64_MAX_MB:
            logger.warning(f"Video too large for base64: {size_mb:.1f}MB > {config.VIDEO_BASE64_MAX_MB}MB limit")
            return None

        with open(video_path, "rb") as f:
            data = f.read()

        # Determine MIME type from extension
        ext = Path(video_path).suffix.lower()
        mime_map = {".mp4": "video/mp4", ".webm": "video/webm", ".ts": "video/mp2t"}
        mime = mime_map.get(ext, "video/mp4")

        b64 = base64.b64encode(data).decode("utf-8")
        logger.info(f"Video encoded to base64: {len(b64) / 1024 / 1024:.1f}MB")
        return f"data:{mime};base64,{b64}"

    VIDEO_ANALYSIS_PROMPT = """你是一个视频内容分析助手。请观看视频并生成简洁的内容分析。

要求：
1. 用一句话概括视频的核心内容
2. 识别视频拍摄的对象（如：客房、大堂、餐厅、泳池、酒店外观等）
3. 提取关键信息点

请严格返回以下格式，不要添加其他内容：
拍摄对象：[对象]
内容摘要：[一句话总结]
关键信息：[要点1, 要点2, ...]

示例：
输出：
拍摄对象：客房
内容摘要：展示全季大观客房的面积、装修风格和床品体验
关键信息：房间约40平，中式禅意装修，床品舒适"""

    def _analyze_with_mimo(self, video_path: str) -> dict | None:
        """Analyze video using MiMo-v2.5 video understanding. Returns parsed dict or None."""
        if not config.MIMO_API_KEY:
            logger.warning("MIMO_API_KEY not set, skipping video analysis")
            return None

        video_data = self._video_to_base64(video_path)
        if not video_data:
            return None

        for attempt in range(3):
            try:
                response = requests.post(
                    f"{config.MIMO_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.MIMO_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.MIMO_MODEL,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "video_url",
                                        "video_url": {
                                            "url": video_data,
                                            "fps": 2,
                                            "media_resolution": "default",
                                        },
                                    },
                                    {
                                        "type": "text",
                                        "text": self.VIDEO_ANALYSIS_PROMPT,
                                    },
                                ],
                            },
                        ],
                        "temperature": 0.1,
                        "max_tokens": 300,
                    },
                    timeout=120,
                )
                response.raise_for_status()

                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return self._parse_summary(content)

            except Exception as e:
                logger.warning(f"MiMo analysis failed (attempt {attempt+1}): {e}")
                if attempt < 2:
                    continue

        logger.warning("All MiMo analysis attempts failed")
        return None

    def _transcribe_with_mimo(self, video_path: str) -> str | None:
        """Transcribe video audio using MiMo-v2.5. Falls back to video understanding for audio-less videos."""
        if not config.MIMO_API_KEY:
            logger.warning("MIMO_API_KEY not set, skipping transcription")
            return None

        # Try audio transcription first
        audio_path = self._extract_audio(video_path)
        if audio_path:
            try:
                audio_data = self._audio_to_base64(audio_path)
            finally:
                self.cleanup(audio_path)

            if audio_data:
                result = self._transcribe_audio_with_mimo(audio_data)
                if result:
                    return result

        # Fallback: use MiMo video understanding for audio-less videos
        logger.info("Audio transcription unavailable, trying video understanding fallback")
        return self._transcribe_video_with_mimo(video_path)

    _BAD_TRANSCRIPTION_MARKERS = (
        "消音", "模糊不清", "听不清", "注：", "注:", "No output",
        "涉及敏感", "敏感信息", "无法识别", "无法转录", "（前段", "（消音", "（注",
    )

    @classmethod
    def _transcript_has_annotations(cls, text: str) -> bool:
        """检测 MiMo 是否返回了消音/标注/拒转（而非干净转录）。"""
        if not text or not text.strip():
            return True
        for m in cls._BAD_TRANSCRIPTION_MARKERS:
            if m in text:
                return True
        # 全角括号注释过多也视为标注污染
        if text.count("（") >= 2 or text.count("(") >= 3:
            return True
        return False

    def _transcribe_audio_with_mimo(self, audio_data: str) -> str | None:
        """Transcribe audio using MiMo audio input."""
        for attempt in range(3):
            try:
                response = requests.post(
                    f"{config.MIMO_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.MIMO_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.MIMO_MODEL,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_audio",
                                        "input_audio": {"data": audio_data, "format": "m4a"},
                                    },
                                    {
                                        "type": "text",
                                        "text": (
                                            "请将这段音频中的口播/旁白内容完整、逐字转录为纯文字。"
                                            "严格要求：1) 只输出听到的原话原文，不输出任何解释、说明、注释；"
                                            "2) 禁止添加（消音）、（模糊不清）、（注：…）等任何括号标注；"
                                            "3) 听不清的字按发音尽量写，不要省略或用括号占位；"
                                            "4) 无论内容是否涉及敏感、争议、维权，都原样完整转录，不得省略、改写或拒转。"
                                            "直接输出转录文本，不要任何前后缀。"
                                        ),
                                    },
                                ],
                            },
                        ],
                        "temperature": 0.1,
                        "max_tokens": 4000,
                    },
                    timeout=120,
                )
                response.raise_for_status()

                data = response.json()
                choice = data["choices"][0]
                text = choice["message"].get("content", "") or choice["message"].get("reasoning_content", "")
                if text and text.strip():
                    cleaned = text.strip()
                    if self._transcript_has_annotations(cleaned):
                        logger.warning(
                            f"MiMo 转录含消音/标注/拒转 (attempt {attempt+1})，重试: {cleaned[:80]}"
                        )
                        if attempt < 2:
                            continue
                        # 末次仍带标注/拒转：返回 None，避免污染口播
                        return None
                    logger.info(f"Audio transcription completed: {len(cleaned)} chars")
                    return cleaned

            except Exception as e:
                logger.warning(f"MiMo transcription failed (attempt {attempt+1}): {e}")
                if attempt < 2:
                    continue

        logger.warning("All MiMo transcription attempts failed")
        return None

    def _transcribe_video_with_mimo(self, video_path: str) -> str | None:
        """Use MiMo video understanding to describe content of audio-less videos."""
        video_data = self._video_to_base64(video_path)
        if not video_data:
            return None

        for attempt in range(3):
            try:
                response = requests.post(
                    f"{config.MIMO_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.MIMO_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.MIMO_MODEL,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "video_url",
                                        "video_url": {
                                            "url": video_data,
                                            "fps": 2,
                                            "media_resolution": "default",
                                        },
                                    },
                                    {
                                        "type": "text",
                                        "text": "请描述这个视频的内容，包括画面中展示的场景、物品、人物动作等。用中文回答，200字以内。",
                                    },
                                ],
                            },
                        ],
                        "temperature": 0.1,
                        "max_tokens": 500,
                    },
                    timeout=120,
                )
                response.raise_for_status()

                data = response.json()
                choice = data["choices"][0]
                text = choice["message"].get("content", "") or choice["message"].get("reasoning_content", "")
                if text and text.strip():
                    logger.info(f"Video understanding completed: {len(text)} chars")
                    return text.strip()

            except Exception as e:
                logger.warning(f"MiMo video understanding failed (attempt {attempt+1}): {e}")
                if attempt < 2:
                    continue

        logger.warning("All MiMo video understanding attempts failed")
        return None

    def _parse_summary(self, content: str) -> dict:
        """Parse LLM response into structured dict."""
        result = {
            "拍摄对象": "酒店",
            "内容摘要": "",
            "关键信息": "",
        }

        for line in content.strip().split("\n"):
            line = line.strip()
            if line.startswith("拍摄对象："):
                result["拍摄对象"] = line.split("：", 1)[1].strip()
            elif line.startswith("内容摘要："):
                result["内容摘要"] = line.split("：", 1)[1].strip()
            elif line.startswith("关键信息："):
                result["关键信息"] = line.split("：", 1)[1].strip()

        if not result["内容摘要"]:
            result["内容摘要"] = content[:100]

        return result

    def _extract_audio(self, video_path: str) -> str | None:
        """Extract audio track from video using ffmpeg. Returns audio file path or None."""
        audio_path = video_path.rsplit(".", 1)[0] + "_audio.m4a"
        try:
            import imageio_ffmpeg
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            import subprocess

            # Try stream copy first (fast, no re-encoding)
            cmd = [
                ffmpeg_exe,
                "-i", video_path,
                "-vn",
                "-c:a", "copy",
                audio_path,
                "-y",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, errors="replace")
            if result.returncode == 0 and os.path.exists(audio_path):
                logger.info(f"Audio extracted (copy): {audio_path}")
                return audio_path

            # Fallback: re-encode to AAC (handles codec/container mismatches like MP3 in M4A)
            logger.debug(f"Audio copy failed, trying AAC re-encode")
            cmd = [
                ffmpeg_exe,
                "-i", video_path,
                "-vn",
                "-c:a", "aac",
                "-b:a", "128k",
                audio_path,
                "-y",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, errors="replace")
            if result.returncode == 0 and os.path.exists(audio_path):
                logger.info(f"Audio extracted (aac): {audio_path}")
                return audio_path

            logger.warning(f"Audio extraction failed: {result.stderr[:300]}")
            return None
        except Exception as e:
            logger.warning(f"Audio extraction error: {e}")
            return None

    def _audio_to_base64(self, audio_path: str) -> str | None:
        """Encode audio file to base64 data URL."""
        size_mb = os.path.getsize(audio_path) / (1024 * 1024)
        if size_mb > 50:
            logger.warning(f"Audio too large for base64: {size_mb:.1f}MB")
            return None
        with open(audio_path, "rb") as f:
            data = f.read()
        ext = Path(audio_path).suffix.lower()
        mime_map = {".m4a": "audio/m4a", ".mp3": "audio/mpeg", ".aac": "audio/aac", ".wav": "audio/wav"}
        mime = mime_map.get(ext, "audio/m4a")
        b64 = base64.b64encode(data).decode("utf-8")
        logger.info(f"Audio encoded to base64: {len(b64) / 1024 / 1024:.1f}MB")
        return f"data:{mime};base64,{b64}"

    def download_audio(self, url: str, referer: str = None) -> str | None:
        """Download audio to temp directory. Returns file path or None on failure."""
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        ext = ".m4a"
        for e in [".mp3", ".m4a", ".aac", ".wav", ".ogg"]:
            if e in url.lower():
                ext = e
                break
        audio_path = str(self.temp_dir / f"audio_{url_hash}{ext}")

        headers = {"User-Agent": config.DEFAULT_USER_AGENT}
        if referer:
            headers["Referer"] = referer
        else:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            headers["Referer"] = f"{parsed.scheme}://{parsed.hostname}/"

        try:
            with httpx.Client(timeout=config.VIDEO_DOWNLOAD_TIMEOUT, follow_redirects=True, headers=headers) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()

                    content_length = response.headers.get("content-length")
                    if content_length:
                        size_mb = int(content_length) / (1024 * 1024)
                        if size_mb > config.MAX_VIDEO_SIZE_MB:
                            logger.warning(f"Audio too large: {size_mb:.1f}MB limit")
                            return None

                    downloaded = 0
                    max_bytes = config.MAX_VIDEO_SIZE_MB * 1024 * 1024
                    with open(audio_path, "wb") as f:
                        for chunk in response.iter_bytes(chunk_size=8192):
                            downloaded += len(chunk)
                            if downloaded > max_bytes:
                                logger.warning("Audio download exceeded size limit")
                                f.close()
                                os.unlink(audio_path)
                                return None
                            f.write(chunk)

            logger.info(f"Audio downloaded: {audio_path} ({downloaded / 1024 / 1024:.1f}MB)")
            return audio_path

        except Exception as e:
            logger.warning(f"Audio download failed: {e}")
            if os.path.exists(audio_path):
                os.unlink(audio_path)
            return None

    def merge_video_audio(self, video_path: str, audio_path: str) -> str | None:
        """Merge video and audio using ffmpeg. Returns merged file path."""
        merged_path = video_path.replace(".mp4", "_merged.mp4")

        try:
            import imageio_ffmpeg
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

            import subprocess
            cmd = [
                ffmpeg_exe,
                "-i", video_path,
                "-i", audio_path,
                "-c:v", "copy",
                "-c:a", "aac",
                "-strict", "experimental",
                merged_path,
                "-y"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, errors="replace")

            if result.returncode == 0 and os.path.exists(merged_path):
                logger.info(f"Video and audio merged: {merged_path}")
                # Replace original
                os.replace(merged_path, video_path)
                return video_path
            else:
                logger.warning(f"FFmpeg merge failed: {result.stderr[:200]}")
                return None

        except Exception as e:
            logger.warning(f"Merge failed: {e}")
            return None

    def process_video(self, video_url: str, audio_url: str = None, analyze: bool = True) -> dict:
        """Full video processing pipeline.

        Args:
            video_url: Video URL
            audio_url: Separate audio URL (if video has no audio track)
            analyze: Whether to call MiMo API for analysis (False = download + audio only)

        Returns dict with keys:
            status: "success" | "download_failed" | "analysis_failed"
            label: "「拍摄XX视频」" (present on success)
            summary: str (present on success)
            key_info: str (present on success)
            audio_base64: str (present when analyze=False and audio extracted)
            transcription: str (raw transcription text)
        """
        logger.info(f"Processing video: {video_url[:80]}...")

        # Step 1: Download video
        video_path = self.download_video(video_url)
        if not video_path:
            return {"status": "download_failed"}

        # Step 1.5: Check if video has audio, merge if separate audio URL provided
        if audio_url:
            logger.info(f"Separate audio track detected, merging...")
            audio_path = self.download_audio(audio_url)
            if audio_path:
                self.merge_video_audio(video_path, audio_path)
                self.cleanup(audio_path)

        # Step 1.6: Transcribe audio
        transcription = self._transcribe_with_mimo(video_path)

        if not analyze:
            # Skip AI analysis: extract audio and convert to base64
            audio_b64 = None
            audio_path = self._extract_audio(video_path)
            if audio_path:
                audio_b64 = self._audio_to_base64(audio_path)
                self.cleanup(audio_path)
            self.cleanup(video_path)
            self.last_audio_base64 = audio_b64
            size_info = f"（{len(audio_b64) / 1024 / 1024:.1f}MB）" if audio_b64 else ""
            return {
                "status": "no_analysis",
                "label": "「视频已下载」",
                "summary": f"音频已提取{size_info}" if audio_b64 else "视频已下载（无音频轨道）",
                "key_info": "",
                "audio_base64": audio_b64,
                "transcription": transcription,
            }

        # Step 2: Analyze with MiMo-v2.5
        result = self._analyze_with_mimo(video_path)

        # Step 3: Cleanup
        self.cleanup(video_path)

        if not result:
            return {"status": "analysis_failed", "transcription": transcription}

        return {
            "status": "success",
            "label": f"「拍摄{result['拍摄对象']}视频」",
            "summary": result["内容摘要"],
            "key_info": result["关键信息"],
            "transcription": transcription,
        }

    def cleanup(self, *files):
        """Remove temporary files."""
        for f in files:
            if f and os.path.exists(f):
                try:
                    os.unlink(f)
                    logger.debug(f"Cleaned up: {f}")
                except Exception as e:
                    logger.warning(f"Failed to clean up {f}: {e}")
