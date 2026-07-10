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
    # Remove time suffixes (both relative and absolute)
    line = re.sub(r'\d+天前|\d+小时前|\d+分钟前|刚刚|昨天|前天', '', line)
    line = re.sub(r'\d+月\d+日|\d+年\d+月\d+日', '', line)
    # Remove Chinese province/region names
    line = re.sub(
        r'浙江|广东|北京|上海|江苏|山东|四川|河南|湖北|湖南|福建|安徽|'
        r'辽宁|重庆|天津|河北|山西|吉林|黑龙江|江西|广西|海南|贵州|云南|'
        r'西藏|陕西|甘肃|青海|宁夏|新疆|内蒙古', '', line
    )
    # Remove markers
    line = line.replace('作者', '').replace('V', '')
    return line.strip()


def clean_comment_content(text: str) -> str:
    """Strip metadata noise from OCR comment content.

    Removes "作者赞过", time markers, province names, reply counts,
    like counts, ad markers, and other UI artifacts that OCR may pick up.
    """
    # Remove "作者赞过"
    text = text.replace('作者赞过', '')
    # Remove reply prefix: "回复XXX：" or "回复XXX:" at the start
    # (OCR may merge the reply marker into comment content)
    text = re.sub(r'^回复\s*\S+?\s*[：:]\s*', '', text)
    # Remove ad markers
    text = re.sub(r'\b广告\s*v?\b', '', text)
    # Remove time markers within content
    text = re.sub(r'\d+天前|\d+小时前|\d+分钟前|刚刚|昨天|前天', '', text)
    # Remove province names (leaked from adjacent comment headers)
    text = re.sub(
        r'浙江|广东|北京|上海|江苏|山东|四川|河南|湖北|湖南|福建|安徽|'
        r'辽宁|重庆|天津|河北|山西|吉林|黑龙江|江西|广西|海南|贵州|云南|'
        r'西藏|陕西|甘肃|青海|宁夏|新疆|内蒙古', '', text
    )
    # Remove reply count markers ("4条回复", "7条回复v" etc.)
    text = re.sub(r'\d+条回复\s*v?', '', text)
    # Remove isolated like counts (standalone 1-3 digit numbers)
    text = re.sub(r'\b\d{1,3}\b', '', text)
    # Remove standalone "V" marker
    text = re.sub(r'\bV\b', '', text)
    # Remove ad-like phrases
    text = re.sub(r'低至[\d.]+元.*?(?:\s|$)', '', text)
    text = re.sub(r'先运后付', '', text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Remove leading/trailing punctuation noise
    text = text.strip('，,.。、· \t')
    return text


def parse_ocr_comments(lines: list[str]) -> list[tuple[str, str]]:
    """Parse OCR output lines into (nickname, comment_text) pairs.

    Expected comment panel structure from WeChat:
      - Comment count header (e.g. "评论 4")
      - Author caption block (skipped on first occurrence)
      - Commenter info line: nickname + time/location markers
      - Comment content line(s)
      - "回复" / "作者赞过" / "写留言" buttons (skipped)
    """
    # Metadata lines to skip (not comment content)
    _SKIP_CONTENT = frozenset({
        "回复", "评论", "写留言", "作者赞过",
        "视频号", "搜索", "添加评论", "暂无评论",
        "广告", "进入小程序", "首评",
    })

    # Time markers for commenter info lines: relative time + absolute dates
    _TIME_RE = re.compile(
        r'\d+天前|\d+小时前|\d+分钟前|刚刚|昨天|前天'
        r'|\d+月\d+日'
        r'|\d+年\d+月\d+日'
    )
    # Province names — WeChat commenter info lines often show "昵称 省份"
    _PROVINCE_RE = re.compile(
        r'浙江|广东|北京|上海|江苏|山东|四川|河南|湖北|湖南|福建|安徽|'
        r'辽宁|重庆|天津|河北|山西|吉林|黑龙江|江西|广西|海南|贵州|云南|'
        r'西藏|陕西|甘肃|青海|宁夏|新疆|内蒙古'
    )

    def _is_commenter_line(line: str) -> bool:
        """Check if a line looks like a commenter info line (nickname + meta)."""
        return (
            "作者" in line
            or "V" in line
            or bool(_TIME_RE.search(line))
            or bool(_PROVINCE_RE.search(line))
        )

    comments = []
    skip_author_caption = True
    i = 0

    while i < len(lines):
        line = lines[i]

        # Skip header / navigation / UI text
        if (re.match(r'^(评论|留言)\s*\d', line) or line in _SKIP_CONTENT):
            i += 1
            continue

        # Detect commenter info line
        is_commenter = _is_commenter_line(line)

        if is_commenter:
            # First author line is the video/post caption — skip it
            if "作者" in line and skip_author_caption:
                skip_author_caption = False
                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    if _is_commenter_line(next_line) and "作者" not in next_line:
                        break
                    i += 1
                continue

            # Extract nickname
            nickname = clean_nickname(line)

            # Collect content lines (until next commenter info or UI marker)
            content_lines = []
            i += 1
            while i < len(lines):
                next_line = lines[i]
                # Stop at next commenter
                if _is_commenter_line(next_line):
                    break
                # Stop at UI markers
                if next_line in _SKIP_CONTENT or re.match(r'^(评论|留言)\s*\d', next_line):
                    i += 1
                    continue
                content_lines.append(next_line)
                i += 1

            content = clean_comment_content(" ".join(content_lines))
            if content and nickname:
                comments.append((nickname, content))
        else:
            i += 1

    return comments


# ── Cross-screen fragment splicing ────────────────────────────────

_MIN_OVERLAP = 8  # minimum chars for tail↔head overlap to be spliced


def _try_splice(a: str, b: str) -> str | None:
    """Try to splice two same-nickname comment fragments into one.

    ``a`` is a fragment from an earlier screen (seen first), ``b`` is
    from a later screen.  Returns the spliced result or None when the
    two fragments appear to be *different* comments by the same person
    (no meaningful overlap).

    Merging rules (in order):
      1. a == b          → pure dedup
      2. b starts with a → b is a superset (comment short enough to fit
           both screens fully, later OCR is better)
      3. a ends with b   → a already contains b
      4. a's tail overlaps b's head (>={_MIN_OVERLAP} chars) → splice
    """
    a, b = a.strip(), b.strip()
    if not a or not b:
        return None
    if a == b:
        return a
    if b.startswith(a):
        return b
    if a.endswith(b):
        return a

    # Find the longest overlap: a's suffix == b's prefix
    max_check = min(len(a), len(b)) - 1
    for k in range(max_check, _MIN_OVERLAP - 1, -1):
        if a[-k:] == b[:k]:
            return a + b[k:]

    return None


def merge_comment_fragments(
    pairs: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Merge cross-screen fragments of the same comment.

    Walks ``pairs`` in screen order.  For each (nickname, content),
    looks back at the most-recently-appended entry with the same
    nickname and tries to splice the new fragment into it via
    :func:`_try_splice`.  Spliced entries replace the old one when the
    result is longer; otherwise the fragment is appended as a genuinely
    new comment.
    """
    result: list[tuple[str, str]] = []

    for nick, content in pairs:
        merged = False
        # Scan result backwards — the most recent same-nickname entry
        # is the one that may be a split fragment of the same comment
        # (N's clipped tail is immediately before N+1's head in
        # screen-order pairs).
        for j in range(len(result) - 1, -1, -1):
            rnick, rcontent = result[j]
            if rnick != nick:
                continue
            spliced = _try_splice(rcontent, content)
            if spliced is not None:
                # Replace with spliced version only when it's longer;
                # always mark as merged (skip append) when spliceable.
                if len(spliced) > len(rcontent):
                    result[j] = (rnick, spliced)
                merged = True
                break
            # Stop scanning once we find same-nickname — no need to
            # look further back (that's an earlier distinct comment).
            break
        if not merged:
            result.append((nick, content))

    return result


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

    def extract_text_with_boxes(self, image_path: str | Path) -> list[dict]:
        """Run OCR and return text WITH bounding-box positions.

        Returns list of dicts:
          {text, cx, cy} — cx/cy are the center of the text box,
          relative to the image origin (top-left = 0,0).
        """
        self._ensure_engine()
        result, _ = self._ocr(str(image_path))
        return self._boxes_from_result(result)

    def extract_text_with_boxes_from_image(self, image) -> list[dict]:
        """Run OCR directly on a PIL Image (no file I/O)."""
        import numpy as np
        self._ensure_engine()
        arr = np.array(image.convert("RGB"))
        result, _ = self._ocr(arr)
        return self._boxes_from_result(result)

    @staticmethod
    def _boxes_from_result(result) -> list[dict]:
        if not result:
            return []
        items = []
        for bbox, text, _confidence in result:
            text = text.strip() if text else ""
            if not text:
                continue
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            items.append({
                "text": text,
                "cx": round(sum(xs) / len(xs)),
                "cy": round(sum(ys) / len(ys)),
                "top": min(ys),
                "bottom": max(ys),
                "left": min(xs),
                "right": max(xs),
            })
        return items

    def extract_comments(self, image_path: str | Path) -> list[tuple[str, str]]:
        """Run OCR on a screenshot and parse into (nickname, text) pairs."""
        lines = self.extract_text(image_path)
        return parse_ocr_comments(lines)
