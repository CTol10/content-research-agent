# Video Speech Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add AI-powered video speech summarization to the existing comment scraper — capture video via Playwright network interception, extract audio with ffmpeg, transcribe with DashScope Paraformer-v2, summarize with DeepSeek, and output to Sheet 3.

**Architecture:** Each platform scraper intercepts video network requests during page load. A new `VideoProcessor` module handles the full pipeline: download → ffmpeg audio extraction → DashScope STT → DeepSeek summarization. Results integrate into the existing Excel output with yellow-highlighted fallback for no-speech videos.

**Tech Stack:** Playwright (video capture), ffmpeg (audio extraction), DashScope SDK (ASR), DeepSeek API (summarization), openpyxl (Excel output)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `config.py` | Modify | Add video processing and DashScope config constants |
| `video_processor.py` | Create | Core video processing pipeline (download, audio, STT, summarize) |
| `scrapers/base.py` | Modify | Add video capture infrastructure (`_setup_video_capture`, `_get_primary_video_url`) |
| `scrapers/douyin.py` | Modify | Call `_setup_video_capture` in `scrape()`, add `video_url` to return dict |
| `scrapers/xiaohongshu.py` | Modify | Same as above |
| `scrapers/weibo.py` | Modify | Same as above |
| `scrapers/toutiao.py` | Modify | Same as above, fix return type to dict |
| `main.py` | Modify | Integrate video processing in `scrape_all()`, add `--no-video` flag, ffmpeg setup |
| `requirements.txt` | Modify | Add `dashscope>=1.20.0` |
| `tests/test_video_processor.py` | Create | Unit tests for VideoProcessor |
| `tests/test_video_capture.py` | Create | Unit tests for video capture in BaseScraper |

---

### Task 1: Config — Add video processing and DashScope constants

**Files:**
- Modify: `config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Add config constants to `config.py`**

Append after line 96 (`DEEPSEEK_BASE_URL = ...`):

```python
# Video processing
VIDEO_TEMP_DIR = OUTPUT_DIR / "video_temp"
MAX_VIDEO_SIZE_MB = 200
MAX_TRANSCRIPT_SECONDS = 300
VIDEO_DOWNLOAD_TIMEOUT = 60
ENABLE_VIDEO_PROCESSING = True

# DashScope ASR
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
ASR_MODEL = "paraformer-v2"
ASR_LANGUAGE = "zh"

# Video URL filtering
VIDEO_URL_EXCLUDE_KEYWORDS = ["ad", "tracker", "analytics", "beacon", "log."]
VIDEO_EXTENSIONS = [".mp4", ".m3u8", ".webm", ".ts"]
```

- [ ] **Step 2: Add `dashscope` to `requirements.txt`**

Append to `requirements.txt`:

```
dashscope>=1.20.0
```

- [ ] **Step 3: Add test for new config constants**

Append to `tests/test_config.py`:

```python
def test_video_config_defaults():
    import config
    assert config.MAX_VIDEO_SIZE_MB == 200
    assert config.MAX_TRANSCRIPT_SECONDS == 300
    assert config.VIDEO_DOWNLOAD_TIMEOUT == 60
    assert config.ASR_MODEL == "paraformer-v2"
    assert config.ASR_LANGUAGE == "zh"
    assert isinstance(config.VIDEO_EXTENSIONS, list)
    assert ".mp4" in config.VIDEO_EXTENSIONS
```

- [ ] **Step 4: Run tests to verify**

Run: `python -m pytest tests/test_config.py -v`
Expected: All tests PASS (including new test)

- [ ] **Step 5: Commit**

```bash
git add config.py requirements.txt tests/test_config.py
git commit -m "feat: add video processing and DashScope config constants"
```

---

### Task 2: VideoProcessor — Download video

**Files:**
- Create: `video_processor.py`
- Create: `tests/test_video_processor.py`

- [ ] **Step 1: Write failing test for download_video**

Create `tests/test_video_processor.py`:

```python
# tests/test_video_processor.py
"""Tests for the video processor module."""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


class TestDownloadVideo:
    @patch("video_processor.httpx")
    def test_download_success(self, mock_httpx, tmp_path):
        """Test successful video download returns file path."""
        from video_processor import VideoProcessor

        # Mock httpx response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": "1000"}
        mock_response.iter_bytes = MagicMock(return_value=[b"fake_video_data"])
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_httpx.Client.return_value = mock_client

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.temp_dir = tmp_path
        vp.logger = MagicMock()

        result = vp.download_video("https://example.com/video.mp4")
        assert result is not None
        assert result.endswith(".mp4")
        assert Path(result).exists()

    @patch("video_processor.httpx")
    def test_download_too_large_returns_none(self, mock_httpx, tmp_path):
        """Test video exceeding size limit returns None."""
        from video_processor import VideoProcessor

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": str(300 * 1024 * 1024)}  # 300MB
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_httpx.Client.return_value = mock_client

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.temp_dir = tmp_path
        vp.logger = MagicMock()

        result = vp.download_video("https://example.com/huge_video.mp4")
        assert result is None

    @patch("video_processor.httpx")
    def test_download_failure_returns_none(self, mock_httpx, tmp_path):
        """Test download failure returns None."""
        import httpx as real_httpx
        from video_processor import VideoProcessor

        mock_client = MagicMock()
        mock_client.get.side_effect = real_httpx.TimeoutException("timeout")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_httpx.Client.return_value = mock_client
        mock_httpx.TimeoutException = real_httpx.TimeoutException

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.temp_dir = tmp_path
        vp.logger = MagicMock()

        result = vp.download_video("https://example.com/video.mp4")
        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_video_processor.py -v`
Expected: FAIL with "ModuleNotFoundError: No module named 'video_processor'"

- [ ] **Step 3: Implement VideoProcessor with download_video**

Create `video_processor.py`:

```python
# video_processor.py
"""Video processing pipeline: download → audio extraction → STT → summarization."""
import hashlib
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

import httpx
import requests

import config

logger = logging.getLogger(__name__)


class VideoProcessor:
    def __init__(self):
        self.temp_dir = config.VIDEO_TEMP_DIR
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def download_video(self, url: str) -> str | None:
        """Download video to temp directory. Returns file path or None on failure."""
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
        ext = ".mp4"
        for e in config.VIDEO_EXTENSIONS:
            if e in url.lower():
                ext = e
                break
        video_path = str(self.temp_dir / f"video_{url_hash}{ext}")

        try:
            with httpx.Client(timeout=config.VIDEO_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()

                    # Check content-length
                    content_length = response.headers.get("content-length")
                    if content_length:
                        size_mb = int(content_length) / (1024 * 1024)
                        if size_mb > config.MAX_VIDEO_SIZE_MB:
                            logger.warning(f"Video too large: {size_mb:.1f}MB > {config.MAX_VIDEO_SIZE_MB}MB limit")
                            return None

                    # Download with size tracking
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

    def cleanup(self, *files):
        """Remove temporary files."""
        for f in files:
            if f and os.path.exists(f):
                try:
                    os.unlink(f)
                    logger.debug(f"Cleaned up: {f}")
                except Exception as e:
                    logger.warning(f"Failed to clean up {f}: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_video_processor.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add video_processor.py tests/test_video_processor.py
git commit -m "feat: add VideoProcessor with video download"
```

---

### Task 3: VideoProcessor — Audio extraction with ffmpeg

**Files:**
- Modify: `video_processor.py`
- Modify: `tests/test_video_processor.py`

- [ ] **Step 1: Write failing tests for extract_audio**

Append to `tests/test_video_processor.py`:

```python
class TestExtractAudio:
    @patch("video_processor.subprocess.run")
    @patch("video_processor.shutil.which", return_value="/usr/bin/ffmpeg")
    def test_extract_success(self, mock_which, mock_run, tmp_path):
        """Test successful audio extraction returns WAV path."""
        from video_processor import VideoProcessor

        mock_run.return_value = MagicMock(returncode=0, stderr=b"")

        # Create a fake video file
        video_path = tmp_path / "test.mp4"
        video_path.write_bytes(b"fake video")

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.temp_dir = tmp_path
        vp.logger = MagicMock()

        result = vp.extract_audio(str(video_path))
        assert result is not None
        assert result.endswith(".wav")

    @patch("video_processor.subprocess.run")
    @patch("video_processor.shutil.which", return_value="/usr/bin/ffmpeg")
    def test_extract_no_audio_track(self, mock_which, mock_run, tmp_path):
        """Test video with no audio track returns None."""
        from video_processor import VideoProcessor

        mock_run.return_value = MagicMock(
            returncode=1,
            stderr=b"Output file does not contain any stream"
        )

        video_path = tmp_path / "silent.mp4"
        video_path.write_bytes(b"fake video")

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.temp_dir = tmp_path
        vp.logger = MagicMock()

        result = vp.extract_audio(str(video_path))
        assert result is None

    @patch("video_processor.shutil.which", return_value=None)
    def test_ffmpeg_not_found(self, mock_which, tmp_path):
        """Test ffmpeg not installed returns None."""
        from video_processor import VideoProcessor

        video_path = tmp_path / "test.mp4"
        video_path.write_bytes(b"fake video")

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.temp_dir = tmp_path
        vp.logger = MagicMock()

        result = vp.extract_audio(str(video_path))
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_video_processor.py::TestExtractAudio -v`
Expected: FAIL (methods not implemented yet)

- [ ] **Step 3: Implement extract_audio**

Add to `video_processor.py` after `download_video`:

```python
    def extract_audio(self, video_path: str) -> str | None:
        """Extract audio from video using ffmpeg. Returns WAV path or None."""
        import shutil

        if not shutil.which("ffmpeg"):
            logger.error("ffmpeg not found in PATH. Install it with: winget install FFmpeg")
            return None

        audio_path = video_path.rsplit(".", 1)[0] + ".wav"

        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-i", video_path,
                    "-vn",                    # no video
                    "-acodec", "pcm_s16le",   # 16-bit PCM
                    "-ar", "16000",           # 16kHz sample rate
                    "-ac", "1",               # mono
                    "-y",                     # overwrite
                    audio_path,
                ],
                capture_output=True,
                timeout=120,
            )

            if result.returncode != 0:
                stderr_text = result.stderr.decode("utf-8", errors="replace")
                if "does not contain any stream" in stderr_text or "No audio" in stderr_text:
                    logger.info(f"Video has no audio track: {video_path}")
                else:
                    logger.warning(f"ffmpeg extraction failed: {stderr_text[:200]}")
                return None

            if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
                logger.warning(f"Extracted audio file is empty or missing")
                return None

            logger.info(f"Audio extracted: {audio_path}")
            return audio_path

        except subprocess.TimeoutExpired:
            logger.warning(f"ffmpeg timeout extracting audio from {video_path}")
            return None
        except Exception as e:
            logger.warning(f"Audio extraction failed: {e}")
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_video_processor.py::TestExtractAudio -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add video_processor.py tests/test_video_processor.py
git commit -m "feat: add audio extraction with ffmpeg"
```

---

### Task 4: VideoProcessor — Speech-to-text with DashScope

**Files:**
- Modify: `video_processor.py`
- Modify: `tests/test_video_processor.py`

- [ ] **Step 1: Write failing tests for transcribe_audio**

Append to `tests/test_video_processor.py`:

```python
class TestTranscribeAudio:
    @patch("video_processor.dashscope")
    def test_transcribe_success(self, mock_dashscope, tmp_path):
        """Test successful transcription returns text."""
        from video_processor import VideoProcessor

        # Mock file upload
        mock_upload_resp = MagicMock()
        mock_upload_resp.status_code = 200
        mock_upload_resp.output = {"uploaded_urls": ["https://dashscope.oss.com/audio.wav"]}

        # Mock transcription call
        mock_task_resp = MagicMock()
        mock_task_resp.status_code = 200
        mock_task_resp.output = {
            "task_id": "task_123",
            "task_status": "SUCCEEDED",
            "results": [
                {"transcription_url": "https://dashscope.oss.com/result.json"}
            ]
        }

        # Mock transcription result fetch
        mock_result_resp = MagicMock()
        mock_result_resp.status_code = 200
        mock_result_resp.output = {
            "transcripts": [
                {"text": "大家好，今天我们来看看这个客房", "sentences": []}
            ]
        }

        mock_dashscope.audio.asr.Transcription.async_call.return_value = mock_task_resp
        mock_dashscope.audio.asr.Transcription.wait.return_value = mock_task_resp

        # Mock the file upload and result fetch via requests
        with patch("video_processor.requests") as mock_requests:
            mock_requests.post.return_value = mock_upload_resp
            mock_requests.get.return_value = mock_result_resp

            vp = VideoProcessor.__new__(VideoProcessor)
            vp.temp_dir = tmp_path
            vp.logger = MagicMock()

            audio_path = tmp_path / "test.wav"
            audio_path.write_bytes(b"fake audio")

            result = vp.transcribe_audio(str(audio_path))
            assert result is not None
            assert "客房" in result

    @patch("video_processor.dashscope")
    def test_transcribe_empty_result(self, mock_dashscope, tmp_path):
        """Test empty transcription returns None."""
        from video_processor import VideoProcessor

        mock_task_resp = MagicMock()
        mock_task_resp.status_code = 200
        mock_task_resp.output = {
            "task_id": "task_123",
            "task_status": "SUCCEEDED",
            "results": []
        }

        mock_dashscope.audio.asr.Transcription.async_call.return_value = mock_task_resp
        mock_dashscope.audio.asr.Transcription.wait.return_value = mock_task_resp

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.temp_dir = tmp_path
        vp.logger = MagicMock()

        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"fake audio")

        with patch("video_processor.requests"):
            result = vp.transcribe_audio(str(audio_path))
            assert result is None

    def test_no_api_key_returns_none(self, tmp_path):
        """Test missing API key returns None."""
        from video_processor import VideoProcessor

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.temp_dir = tmp_path
        vp.logger = MagicMock()

        with patch("video_processor.config") as mock_config:
            mock_config.DASHSCOPE_API_KEY = ""
            mock_config.ASR_MODEL = "paraformer-v2"
            mock_config.ASR_LANGUAGE = "zh"
            mock_config.MAX_TRANSCRIPT_SECONDS = 300

            audio_path = tmp_path / "test.wav"
            audio_path.write_bytes(b"fake audio")

            result = vp.transcribe_audio(str(audio_path))
            assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_video_processor.py::TestTranscribeAudio -v`
Expected: FAIL (method not implemented)

- [ ] **Step 3: Implement transcribe_audio**

Add to `video_processor.py` after `extract_audio`:

```python
    def _truncate_audio(self, audio_path: str, max_seconds: int) -> str | None:
        """Truncate audio to max_seconds. Returns new path or original if within limit."""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
                capture_output=True, text=True, timeout=30,
            )
            duration = float(result.stdout.strip())
            if duration <= max_seconds:
                return audio_path

            truncated_path = audio_path.replace(".wav", "_truncated.wav")
            subprocess.run(
                ["ffmpeg", "-i", audio_path, "-t", str(max_seconds),
                 "-y", truncated_path],
                capture_output=True, timeout=60,
            )
            logger.info(f"Audio truncated from {duration:.0f}s to {max_seconds}s")
            return truncated_path
        except Exception as e:
            logger.warning(f"Audio truncation failed, using original: {e}")
            return audio_path

    def transcribe_audio(self, audio_path: str) -> str | None:
        """Transcribe audio using DashScope Paraformer-v2. Returns text or None."""
        if not config.DASHSCOPE_API_KEY:
            logger.warning("DASHSCOPE_API_KEY not set, skipping video transcription")
            return None

        try:
            import dashscope
            from dashscope.audio.asr import Transcription

            dashscope.api_key = config.DASHSCOPE_API_KEY

            # Truncate if too long
            audio_path = self._truncate_audio(audio_path, config.MAX_TRANSCRIPT_SECONDS)

            # Upload file to DashScope
            upload_resp = dashscope.Uploads.upload(
                model=config.ASR_MODEL,
                file=audio_path,
            )
            if upload_resp.status_code != 200:
                logger.warning(f"DashScope file upload failed: {upload_resp}")
                return None

            file_url = upload_resp.output["uploaded_urls"][0]

            # Submit transcription task
            task_response = Transcription.async_call(
                model=config.ASR_MODEL,
                file_urls=[file_url],
                language_hints=[config.ASR_LANGUAGE],
            )

            if task_response.status_code != 200:
                logger.warning(f"DashScope transcription submit failed: {task_response}")
                return None

            # Wait for result
            result = Transcription.wait(task_response.output["task_id"])

            if result.status_code != 200 or result.output.get("task_status") != "SUCCEEDED":
                logger.warning(f"DashScope transcription failed: {result}")
                return None

            # Fetch transcription result
            results = result.output.get("results", [])
            if not results:
                logger.info("Transcription returned empty results (no speech detected)")
                return None

            transcription_url = results[0].get("transcription_url")
            if not transcription_url:
                return None

            resp = requests.get(transcription_url, timeout=30)
            resp.raise_for_status()
            transcript_data = resp.json()

            # Concatenate all transcript text
            texts = []
            for transcript in transcript_data.get("transcripts", []):
                text = transcript.get("text", "").strip()
                if text:
                    texts.append(text)

            full_text = " ".join(texts)
            if not full_text:
                logger.info("Transcription result is empty (no speech content)")
                return None

            logger.info(f"Transcription complete: {len(full_text)} chars")
            return full_text

        except ImportError:
            logger.error("dashscope package not installed. Run: pip install dashscope")
            return None
        except Exception as e:
            logger.warning(f"Transcription failed: {e}")
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_video_processor.py::TestTranscribeAudio -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add video_processor.py tests/test_video_processor.py
git commit -m "feat: add speech-to-text with DashScope Paraformer-v2"
```

---

### Task 5: VideoProcessor — LLM summarization with DeepSeek

**Files:**
- Modify: `video_processor.py`
- Modify: `tests/test_video_processor.py`

- [ ] **Step 1: Write failing tests for summarize_transcript**

Append to `tests/test_video_processor.py`:

```python
class TestSummarizeTranscript:
    @patch("video_processor.requests.post")
    def test_summarize_success(self, mock_post):
        """Test successful summarization returns structured result."""
        from video_processor import VideoProcessor

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": "拍摄对象：客房\n内容摘要：展示了全季大观客房的整体布局和装修风格\n关键信息：房间面积约40平，装修偏中式禅意"
                }
            }]
        }
        mock_post.return_value = mock_response

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.logger = MagicMock()

        result = vp.summarize_transcript("大家好今天来看看这个客房的装修和布局")
        assert result is not None
        assert result["拍摄对象"] == "客房"
        assert "客房" in result["内容摘要"]

    @patch("video_processor.requests.post")
    def test_summarize_api_failure(self, mock_post):
        """Test API failure returns fallback result."""
        import requests as real_requests
        from video_processor import VideoProcessor

        mock_post.side_effect = real_requests.RequestException("timeout")

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.logger = MagicMock()

        result = vp.summarize_transcript("这是一段很长的口播内容，关于酒店客房的介绍")
        assert result is not None
        assert "拍摄对象" in result
        assert "内容摘要" in result

    def test_empty_transcript_returns_none(self):
        """Test empty transcript returns None."""
        from video_processor import VideoProcessor

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.logger = MagicMock()

        result = vp.summarize_transcript("")
        assert result is None

        result = vp.summarize_transcript(None)
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_video_processor.py::TestSummarizeTranscript -v`
Expected: FAIL (method not implemented)

- [ ] **Step 3: Implement summarize_transcript**

Add to `video_processor.py` after `transcribe_audio`:

```python
    VIDEO_SUMMARY_PROMPT = """你是一个视频内容分析助手。根据视频口播转录文本，生成简洁的内容摘要。

要求：
1. 用一句话概括视频的核心内容
2. 识别视频拍摄的对象（如：客房、大堂、餐厅、泳池、酒店外观等）
3. 提取关键信息点

请严格返回以下格式，不要添加其他内容：
拍摄对象：[对象]
内容摘要：[一句话总结]
关键信息：[要点1, 要点2, ...]

示例：
输入："大家好今天来看看全季大观的客房，这个房间大概四十平，装修很有禅意，床品也很舒服"
输出：
拍摄对象：客房
内容摘要：展示全季大观客房的面积、装修风格和床品体验
关键信息：房间约40平，中式禅意装修，床品舒适"""

    def summarize_transcript(self, transcript: str) -> dict | None:
        """Summarize transcript using DeepSeek API. Returns parsed dict or None."""
        if not transcript or not transcript.strip():
            return None

        if not config.DEEPSEEK_API_KEY:
            logger.warning("DEEPSEEK_API_KEY not set, skipping video summarization")
            return None

        default_result = {
            "拍摄对象": "酒店",
            "内容摘要": transcript[:100] if transcript else "",
            "关键信息": "",
        }

        for attempt in range(3):
            try:
                response = requests.post(
                    f"{config.DEEPSEEK_BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.DEEPSEEK_MODEL,
                        "messages": [
                            {"role": "system", "content": self.VIDEO_SUMMARY_PROMPT},
                            {"role": "user", "content": transcript},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 300,
                    },
                    timeout=30,
                )
                response.raise_for_status()

                data = response.json()
                content = data["choices"][0]["message"]["content"]

                return self._parse_summary(content)

            except Exception as e:
                logger.warning(f"Video summarization failed (attempt {attempt+1}): {e}")
                if attempt < 2:
                    continue

        # All retries exhausted — use fallback
        logger.warning("All summarization attempts failed, using fallback")
        return default_result

    def _parse_summary(self, content: str) -> dict:
        """Parse LLM summary response into structured dict."""
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

        # Fallback: use raw content as summary if parsing failed
        if not result["内容摘要"]:
            result["内容摘要"] = content[:100]

        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_video_processor.py::TestSummarizeTranscript -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add video_processor.py tests/test_video_processor.py
git commit -m "feat: add video transcript summarization with DeepSeek"
```

---

### Task 6: VideoProcessor — Full pipeline (process_video)

**Files:**
- Modify: `video_processor.py`
- Modify: `tests/test_video_processor.py`

- [ ] **Step 1: Write failing tests for process_video**

Append to `tests/test_video_processor.py`:

```python
class TestProcessVideo:
    @patch.object(VideoProcessor, "summarize_transcript")
    @patch.object(VideoProcessor, "transcribe_audio")
    @patch.object(VideoProcessor, "extract_audio")
    @patch.object(VideoProcessor, "download_video")
    def test_full_pipeline_success(self, mock_download, mock_extract, mock_transcribe, mock_summarize, tmp_path):
        """Test full pipeline returns success result."""
        from video_processor import VideoProcessor

        mock_download.return_value = str(tmp_path / "video.mp4")
        mock_extract.return_value = str(tmp_path / "video.wav")
        mock_transcribe.return_value = "大家好看看这个客房"
        mock_summarize.return_value = {
            "拍摄对象": "客房",
            "内容摘要": "展示客房环境",
            "关键信息": "房间宽敞",
        }

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.temp_dir = tmp_path
        vp.logger = MagicMock()

        result = vp.process_video("https://example.com/video.mp4")
        assert result["status"] == "success"
        assert "客房" in result["label"]

    @patch.object(VideoProcessor, "download_video")
    def test_download_failure(self, mock_download, tmp_path):
        """Test download failure returns download_failed status."""
        from video_processor import VideoProcessor

        mock_download.return_value = None

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.temp_dir = tmp_path
        vp.logger = MagicMock()

        result = vp.process_video("https://example.com/video.mp4")
        assert result["status"] == "download_failed"

    @patch.object(VideoProcessor, "extract_audio")
    @patch.object(VideoProcessor, "download_video")
    def test_no_audio(self, mock_download, mock_extract, tmp_path):
        """Test no audio track returns no_audio status."""
        from video_processor import VideoProcessor

        mock_download.return_value = str(tmp_path / "video.mp4")
        mock_extract.return_value = None

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.temp_dir = tmp_path
        vp.logger = MagicMock()

        result = vp.process_video("https://example.com/video.mp4")
        assert result["status"] == "no_audio"
        assert result["label"] == "「无口播视频」"

    @patch.object(VideoProcessor, "transcribe_audio")
    @patch.object(VideoProcessor, "extract_audio")
    @patch.object(VideoProcessor, "download_video")
    def test_no_speech(self, mock_download, mock_extract, mock_transcribe, tmp_path):
        """Test empty transcription returns no_speech status."""
        from video_processor import VideoProcessor

        mock_download.return_value = str(tmp_path / "video.mp4")
        mock_extract.return_value = str(tmp_path / "video.wav")
        mock_transcribe.return_value = None

        vp = VideoProcessor.__new__(VideoProcessor)
        vp.temp_dir = tmp_path
        vp.logger = MagicMock()

        result = vp.process_video("https://example.com/video.mp4")
        assert result["status"] == "no_speech"
        assert result["label"] == "「无口播视频」"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_video_processor.py::TestProcessVideo -v`
Expected: FAIL (process_video not implemented)

- [ ] **Step 3: Implement process_video**

Add to `video_processor.py` after `_parse_summary`:

```python
    def process_video(self, video_url: str) -> dict:
        """Full video processing pipeline.

        Returns dict with keys:
            status: "success" | "download_failed" | "no_audio" | "no_speech"
            label: "「拍摄XX视频」" or "「无口播视频」" (present when not download_failed)
            summary: str (present on success)
            transcript: str (present on success)
            key_info: str (present on success)
        """
        logger.info(f"Processing video: {video_url[:80]}...")

        # Step 1: Download
        video_path = self.download_video(video_url)
        if not video_path:
            return {"status": "download_failed"}

        # Step 2: Extract audio
        audio_path = self.extract_audio(video_path)
        if not audio_path:
            self.cleanup(video_path)
            return {"status": "no_audio", "label": "「无口播视频」"}

        # Step 3: Transcribe
        transcript = self.transcribe_audio(audio_path)
        if not transcript:
            self.cleanup(video_path, audio_path)
            return {"status": "no_speech", "label": "「无口播视频」"}

        # Step 4: Summarize
        summary = self.summarize_transcript(transcript)

        # Step 5: Cleanup
        self.cleanup(video_path, audio_path)

        if not summary:
            # Summarization failed but we have transcript
            return {
                "status": "success",
                "label": "「拍摄酒店视频」",
                "summary": transcript[:100],
                "transcript": transcript,
                "key_info": "",
            }

        return {
            "status": "success",
            "label": f"「拍摄{summary['拍摄对象']}视频」",
            "summary": summary["内容摘要"],
            "transcript": transcript,
            "key_info": summary["关键信息"],
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_video_processor.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add video_processor.py tests/test_video_processor.py
git commit -m "feat: add full video processing pipeline"
```

---

### Task 7: BaseScraper — Video capture infrastructure

**Files:**
- Modify: `scrapers/base.py`
- Create: `tests/test_video_capture.py`

- [ ] **Step 1: Write failing tests for video capture**

Create `tests/test_video_capture.py`:

```python
# tests/test_video_capture.py
"""Tests for video capture in BaseScraper."""
from unittest.mock import MagicMock, AsyncMock

import pytest

from scrapers.base import BaseScraper


class TestVideoCapture:
    def test_setup_video_capture_initializes_list(self):
        """Test _setup_video_capture initializes video URL list."""
        scraper = BaseScraper.__new__(BaseScraper)
        page = MagicMock()
        page.on = MagicMock()

        scraper._setup_video_capture(page)

        assert scraper._video_urls == []
        assert scraper._video_content_lengths == {}
        page.on.assert_called_once_with("response", page.on.call_args[0][0])

    def test_capture_video_response_mp4(self):
        """Test capturing a .mp4 video response."""
        scraper = BaseScraper.__new__(BaseScraper)
        scraper._video_urls = []
        scraper._video_content_lengths = {}
        scraper._video_exclude = ["ad", "tracker"]

        response = MagicMock()
        response.url = "https://v1.douyinvod.com/video123.mp4"
        response.headers = {"content-type": "video/mp4", "content-length": "5000000"}

        scraper._capture_video_response(response)

        assert len(scraper._video_urls) == 1
        assert "douyinvod" in scraper._video_urls[0]

    def test_capture_video_response_excluded(self):
        """Test that ad/tracker URLs are excluded."""
        scraper = BaseScraper.__new__(BaseScraper)
        scraper._video_urls = []
        scraper._video_content_lengths = {}
        scraper._video_exclude = ["ad", "tracker", "analytics", "beacon"]

        response = MagicMock()
        response.url = "https://ads.example.com/tracker/video.mp4"
        response.headers = {"content-type": "video/mp4", "content-length": "1000"}

        scraper._capture_video_response(response)

        assert len(scraper._video_urls) == 0

    def test_get_primary_video_url_returns_largest(self):
        """Test _get_primary_video_url returns URL with largest content-length."""
        scraper = BaseScraper.__new__(BaseScraper)
        scraper._video_urls = [
            "https://cdn.example.com/small.mp4",
            "https://cdn.example.com/large.mp4",
        ]
        scraper._video_content_lengths = {
            "https://cdn.example.com/small.mp4": 1000000,
            "https://cdn.example.com/large.mp4": 50000000,
        }

        result = scraper._get_primary_video_url()
        assert result == "https://cdn.example.com/large.mp4"

    def test_get_primary_video_url_empty(self):
        """Test _get_primary_video_url returns None when no videos."""
        scraper = BaseScraper.__new__(BaseScraper)
        scraper._video_urls = []
        scraper._video_content_lengths = {}

        result = scraper._get_primary_video_url()
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_video_capture.py -v`
Expected: FAIL (methods not implemented)

- [ ] **Step 3: Add video capture methods to BaseScraper**

Add to `scrapers/base.py` after `_verify_comment_count` (before the end of the class):

```python
    def _setup_video_capture(self, page: Page):
        """Register network response listener to capture video URLs."""
        self._video_urls = []
        self._video_content_lengths = {}
        self._video_exclude = getattr(config, "VIDEO_URL_EXCLUDE_KEYWORDS", ["ad", "tracker", "analytics", "beacon"])

        def on_response(response):
            self._capture_video_response(response)

        page.on("response", on_response)
        logger.debug(f"[{self.platform_name}] Video capture listener registered")

    def _capture_video_response(self, response):
        """Check if response is a video and record its URL."""
        try:
            url = response.url
            content_type = response.headers.get("content-type", "")
            headers = response.headers

            # Check if video by content-type or URL extension
            is_video = "video" in content_type
            if not is_video:
                video_extensions = getattr(config, "VIDEO_EXTENSIONS", [".mp4", ".m3u8", ".webm", ".ts"])
                url_lower = url.split("?")[0].lower()
                is_video = any(url_lower.endswith(ext) for ext in video_extensions)

            if not is_video:
                return

            # Filter out ads/tracking
            url_lower = url.lower()
            if any(kw in url_lower for kw in self._video_exclude):
                return

            content_length = int(headers.get("content-length", 0))
            self._video_urls.append(url)
            self._video_content_lengths[url] = content_length
            logger.debug(f"[{self.platform_name}] Video captured: {url[:80]} ({content_length / 1024 / 1024:.1f}MB)")

        except Exception:
            pass  # Don't let capture errors affect scraping

    def _get_primary_video_url(self) -> str | None:
        """Return the URL of the largest captured video, or None."""
        if not self._video_urls:
            return None

        # Deduplicate
        unique_urls = list(dict.fromkeys(self._video_urls))

        if len(unique_urls) == 1:
            return unique_urls[0]

        # Return largest by content-length
        best = max(unique_urls, key=lambda u: self._video_content_lengths.get(u, 0))
        logger.info(f"[{self.platform_name}] Selected primary video: {best[:80]}")
        return best
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_video_capture.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scrapers/base.py tests/test_video_capture.py
git commit -m "feat: add video capture infrastructure to BaseScraper"
```

---

### Task 8: Platform scrapers — Integrate video capture

**Files:**
- Modify: `scrapers/douyin.py`
- Modify: `scrapers/xiaohongshu.py`
- Modify: `scrapers/weibo.py`
- Modify: `scrapers/toutiao.py`

- [ ] **Step 1: Integrate video capture in DouyinScraper**

In `scrapers/douyin.py`, in the `scrape()` method, add video capture setup after page creation and before navigation. Find this block (around line 26-32):

```python
        try:
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            await self._install_media_pause_guard(page)
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
```

Replace with:

```python
        try:
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            self._setup_video_capture(page)
            await self._install_media_pause_guard(page)
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
```

Also update the return statement (around line 72) to include `video_url`:

```python
            return {"post_content": post_content, "comments": comments, "video_url": self._get_primary_video_url()}
```

- [ ] **Step 2: Integrate video capture in XiaohongshuScraper**

In `scrapers/xiaohongshu.py`, in the `scrape()` method. Find:

```python
        try:
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))

            if not await self._navigate_with_retry(page, url, context):
```

Replace with:

```python
        try:
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            self._setup_video_capture(page)

            if not await self._navigate_with_retry(page, url, context):
```

Also update the return statement (around line 187):

```python
            return {"post_content": post_content, "comments": comments, "video_url": self._get_primary_video_url()}
```

- [ ] **Step 3: Integrate video capture in WeiboScraper**

In `scrapers/weibo.py`, in the `scrape()` method. Find:

```python
        try:
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
```

Replace with:

```python
        try:
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            self._setup_video_capture(page)
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
```

Also find the return statement (around line 99) and add `video_url`:

```python
            return {"post_content": post_content, "comments": all_comments, "video_url": self._get_primary_video_url()}
```

- [ ] **Step 4: Fix ToutiaoScraper return type and integrate video capture**

In `scrapers/toutiao.py`, the `scrape()` method has type hint `-> list[tuple[str, str]]` but actually returns a dict. Fix the type hint and add video capture.

Find (line 19):

```python
    async def scrape(self, url: str) -> list[tuple[str, str]]:
```

Replace with:

```python
    async def scrape(self, url: str) -> dict:
```

Find:

```python
        try:
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            await self._install_media_pause_guard(page)
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
```

Replace with:

```python
        try:
            page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))
            self._setup_video_capture(page)
            await self._install_media_pause_guard(page)
            await page.goto(url, timeout=30000, wait_until="domcontentloaded")
```

Also update the return statement (around line 56):

```python
            return {"post_content": "", "comments": comments, "video_url": self._get_primary_video_url()}
```

- [ ] **Step 5: Run existing tests to verify nothing broke**

Run: `python -m pytest tests/ -v`
Expected: All existing tests PASS

- [ ] **Step 6: Commit**

```bash
git add scrapers/douyin.py scrapers/xiaohongshu.py scrapers/weibo.py scrapers/toutiao.py
git commit -m "feat: integrate video capture into all platform scrapers"
```

---

### Task 9: main.py — Integrate video processing and add --no-video flag

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add --no-video CLI argument**

In `main.py`, find the argparse section (around line 784):

```python
    parser.add_argument("--no-classify", action="store_true", help="跳过标签分类（只抓取不分析，加快速度）")
```

Add after it:

```python
    parser.add_argument("--no-video", action="store_true", help="跳过视频口播处理（不下载视频、不转录）")
```

- [ ] **Step 2: Pass video parameter through the call chain**

Find `run_all` function signature (line 410):

```python
async def run_all(input_file, platforms_filter: list[str] = None, resume: bool = False, batch_size: int = 0, login_timeout: int = 300, classify: bool = True):
```

Replace with:

```python
async def run_all(input_file, platforms_filter: list[str] = None, resume: bool = False, batch_size: int = 0, login_timeout: int = 300, classify: bool = True, process_video: bool = True):
```

Find the call to `scrape_all` at the end of `run_all` (around line 464):

```python
    return await scrape_all(input_file, platforms_filter, resume=resume, batch_size=batch_size, classify=classify)
```

Replace with:

```python
    return await scrape_all(input_file, platforms_filter, resume=resume, batch_size=batch_size, classify=classify, process_video=process_video)
```

Find `scrape_all` function signature (line 467):

```python
async def scrape_all(input_file, platforms_filter: list[str] = None, resume: bool = False, batch_size: int = 0, classify: bool = True):
```

Replace with:

```python
async def scrape_all(input_file, platforms_filter: list[str] = None, resume: bool = False, batch_size: int = 0, classify: bool = True, process_video: bool = True):
```

- [ ] **Step 3: Add video processing to scrape_all loop**

In `scrape_all`, add video processor initialization. Find the section after `ws_analysis` and `analysis_counter` (around line 580):

```python
    ws_analysis = wb["正文内容+标签+情感"]
    analysis_counter = 1
    comment_counter = 1
```

Add after it:

```python
    # Video processor initialization
    video_processor = None
    if process_video:
        from video_processor import VideoProcessor
        import shutil
        if not shutil.which("ffmpeg"):
            print("\n[警告] 未检测到 ffmpeg，视频处理将被跳过。安装方法: winget install FFmpeg")
            process_video = False
        elif not config.DASHSCOPE_API_KEY:
            print("\n[警告] 未设置 DASHSCOPE_API_KEY，视频处理将被跳过。")
            process_video = False
        else:
            video_processor = VideoProcessor()
```

Now find the section that writes post content to Sheet 3 (around lines 643-663). This is the block that starts with `# Write post content to Excel`:

```python
                    # Write post content to Excel (one row per tag)
                    if post_content:
                        expanded = []
                        if classify and post_content.strip():
                            print(f"    分类正文...", end=" ", flush=True)
                            expanded = classify_content_expanded(post_content)
                            tags_display = ", ".join(f"{e['tag']}-{e['sentiment']}" for e in expanded) if expanded else "/"
                            print(f"{tags_display}")
                        if expanded:
                            for item in expanded:
                                ws_analysis.append([
                                    analysis_counter, row.get("title", ""), platform_name,
                                    post_content, item["sentiment"], item["tag"],
                                    item["has_comparison"], url
                                ])
                        else:
                            ws_analysis.append([
                                analysis_counter, row.get("title", ""), platform_name,
                                post_content, "/", "/", "否", url
                            ])
                        analysis_counter += 1
```

Replace with:

```python
                    # Video processing
                    video_result = None
                    if video_processor and result.get("video_url"):
                        print(f"    处理视频...", end=" ", flush=True)
                        video_result = video_processor.process_video(result["video_url"])
                        if video_result["status"] == "success":
                            print(f"「拍摄{video_result.get('拍摄对象', '酒店')}视频」")
                        elif video_result["status"] in ("no_audio", "no_speech"):
                            print("「无口播视频」")
                        else:
                            print("跳过")

                    # Determine content display for Sheet 3
                    if video_result and video_result["status"] == "success":
                        content_display = f"{video_result['label']}\n\n{video_result['summary']}"
                        classify_text = video_result["summary"]
                    elif video_result and video_result["status"] in ("no_audio", "no_speech"):
                        content_display = "「无口播视频」"
                        classify_text = ""
                    else:
                        content_display = post_content
                        classify_text = post_content

                    # Write post content to Excel (one row per tag)
                    if content_display:
                        expanded = []
                        if classify and classify_text.strip():
                            print(f"    分类正文...", end=" ", flush=True)
                            expanded = classify_content_expanded(classify_text)
                            tags_display = ", ".join(f"{e['tag']}-{e['sentiment']}" for e in expanded) if expanded else "/"
                            print(f"{tags_display}")

                        is_no_speech = video_result and video_result["status"] in ("no_audio", "no_speech")

                        if expanded:
                            for item in expanded:
                                row_data = [
                                    analysis_counter, row.get("title", ""), platform_name,
                                    content_display, item["sentiment"], item["tag"],
                                    item["has_comparison"], url
                                ]
                                ws_analysis.append(row_data)
                        else:
                            row_data = [
                                analysis_counter, row.get("title", ""), platform_name,
                                content_display, "/", "/", "否", url
                            ]
                            ws_analysis.append(row_data)

                        # Apply yellow font for no-speech videos
                        if is_no_speech:
                            from openpyxl.styles import Font
                            yellow_font = Font(color="FFD700")
                            max_row = ws_analysis.max_row
                            for col in range(1, ws_analysis.max_column + 1):
                                ws_analysis.cell(row=max_row, column=col).font = yellow_font
                            # Add comment for manual review
                            from openpyxl.comments import Comment
                            content_cell = ws_analysis.cell(row=max_row, column=4)
                            content_cell.comment = Comment("需人工复核：视频无口播内容", "auto-scraper")

                        analysis_counter += 1
```

- [ ] **Step 4: Update CLI dispatch to pass process_video**

Find the `--run` dispatch (around line 829):

```python
        asyncio.run(run_all(input_file, platforms_filter, resume=args.resume, batch_size=args.batch_size, login_timeout=args.login_timeout, classify=not args.no_classify))
```

Replace with:

```python
        asyncio.run(run_all(input_file, platforms_filter, resume=args.resume, batch_size=args.batch_size, login_timeout=args.login_timeout, classify=not args.no_classify, process_video=not args.no_video))
```

Find the default dispatch (around line 848):

```python
        asyncio.run(scrape_all(input_file, platforms_filter, resume=args.resume, batch_size=args.batch_size, classify=not args.no_classify))
```

Replace with:

```python
        asyncio.run(scrape_all(input_file, platforms_filter, resume=args.resume, batch_size=args.batch_size, classify=not args.no_classify, process_video=not args.no_video))
```

- [ ] **Step 5: Add ffmpeg install to setup flow**

In `install_dependencies()` function, find the section after Playwright browser check (around line 167):

```python
    print("\n" + "=" * 60)
    print("  环境检查通过！可以开始使用了。")
    print("=" * 60)
```

Add before it:

```python
    # Check ffmpeg
    print("[3/3] 检查 ffmpeg...")
    if shutil.which("ffmpeg"):
        print("  -> ffmpeg 已就绪")
    else:
        print("  -> ffmpeg 未安装，正在安装...")
        try:
            subprocess.check_call(
                ["winget", "install", "FFmpeg", "--accept-package-agreements", "--accept-source-agreements"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            print("  -> ffmpeg 安装成功")
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("  -> ffmpeg 自动安装失败，请手动安装:")
            print("     winget install FFmpeg")
            print("     或从 https://ffmpeg.org/download.html 下载")
```

Also add `import shutil` at the top of the function if not already imported.

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add main.py
git commit -m "feat: integrate video processing into main pipeline with --no-video flag"
```

---

### Task 10: End-to-end verification

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Verify CLI help includes new flag**

Run: `python main.py --help`
Expected: Output includes `--no-video` flag description

- [ ] **Step 3: Verify import chain works**

Run: `python -c "from video_processor import VideoProcessor; vp = VideoProcessor(); print('OK')"`
Expected: `OK` (or a warning about DASHSCOPE_API_KEY, but no import error)

- [ ] **Step 4: Commit any final fixes**

```bash
git add -A
git commit -m "fix: address any issues found during end-to-end verification"
```
