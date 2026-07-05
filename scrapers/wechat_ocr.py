# scrapers/wechat_ocr.py
"""OCR-based comment extraction for WeChat PC desktop.

Wraps RapidOCR for screenshot text recognition and provides
WeChat-specific comment parsing (nickname extraction, comment
structure recognition).
"""
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Lazy-loaded OCR engine
_ocr_engine = None


def _get_ocr():
    """Get or initialize the RapidOCR engine."""
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
        except ImportError:
            raise RuntimeError(
                "rapidocr-onnxruntime 未安装。请运行: pip install rapidocr-onnxruntime"
            )
    return _ocr_engine


def clean_nickname(line: str) -> str:
    """Extract nickname from a comment info line.

    Input examples:
      "专业专注施工 V 浙江 2天前"
      "胡向东&卓迈家具作者浙江3天前"
      "用户昵称 广东 刚刚"

    Strips: time suffixes, province names, "作者"/"V" markers.
    """
    # Remove time suffixes
    line = re.sub(r'\d+天前|\d+小时前|\d+分钟前|刚刚', '', line)
    # Remove Chinese province/region names
    line = re.sub(
        r'浙江|广东|北京|上海|江苏|山东|四川|河南|湖北|湖南|福建|安徽|'
        r'辽宁|重庆|天津|河北|山西|吉林|黑龙江|江西|广西|海南|贵州|云南|'
        r'西藏|陕西|甘肃|青海|宁夏|新疆|内蒙古', '', line
    )
    # Remove markers
    line = line.replace('作者', '').replace('V', '')
    return line.strip()


def parse_ocr_comments(lines: list[str]) -> list[tuple[str, str]]:
    """Parse OCR output lines into (nickname, comment_text) pairs.

    Expected comment panel structure from WeChat:
      - Comment count header (e.g. "评论 4")
      - Author caption block (skipped on first occurrence)
      - Commenter info line: nickname + time/location markers
      - Comment content line(s)
      - "回复" button (skipped)
    """
    comments = []
    skip_author_caption = True
    i = 0

    while i < len(lines):
        line = lines[i]

        # Skip header/navigation text ("评论 4" count header, not "评论者..." nicknames)
        if re.match(r'^评论\s*\d', line) or line == "回复" or line in ("视频号", "搜索"):
            i += 1
            continue

        # Detect commenter info line: has time marker or author/V markers
        has_time = bool(re.search(r'\d+天前|\d+小时前|\d+分钟前|刚刚', line))
        is_author = "作者" in line
        is_commenter = is_author or "V" in line or has_time

        if is_commenter:
            # First author line is the video/post caption — skip it
            if is_author and skip_author_caption:
                skip_author_caption = False
                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    if re.search(
                        r'\d+天前|\d+小时前|\d+分钟前|刚刚', next_line
                    ) and "作者" not in next_line:
                        break
                    i += 1
                continue

            # Extract nickname
            nickname = clean_nickname(line)

            # Collect content lines (until next commenter info or end marker)
            content_lines = []
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if re.search(r'\d+天前|\d+小时前|\d+分钟前|刚刚', next_line):
                    break
                if next_line in ("回复", "评论") or re.match(r'^评论\s*\d', next_line):
                    break
                content_lines.append(next_line)
                i += 1

            content = " ".join(content_lines).strip()
            if content and nickname:
                comments.append((nickname, content))
        else:
            i += 1

    return comments


class WechatOcr:
    """OCR extraction for WeChat comment screenshots."""

    def __init__(self):
        self._ocr = None

    def _ensure_engine(self):
        if self._ocr is None:
            self._ocr = _get_ocr()

    def extract_text(self, image_path: str | Path) -> list[str]:
        """Run OCR on an image file. Returns list of recognized text lines."""
        self._ensure_engine()
        result, _ = self._ocr(str(image_path))
        if not result:
            return []
        return [line[1].strip() for line in result if line[1].strip()]

    def extract_comments(self, image_path: str | Path) -> list[tuple[str, str]]:
        """Run OCR on a screenshot and parse into (nickname, text) pairs."""
        lines = self.extract_text(image_path)
        return parse_ocr_comments(lines)
