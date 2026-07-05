"""Capture WeChat Video Channel API calls via mitmproxy.

Usage:
    1. python capture_api.py
    2. Set system proxy to 127.0.0.1:8080 (or WeChat proxy if supported)
    3. Open a video channel video in WeChat, browse comments
    4. Press Ctrl+C to stop — captured APIs are saved to api_captures.json

Then use the captured endpoints to build the scraper.
"""
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from mitmproxy import http, options
from mitmproxy.tools.dump import DumpMaster

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_FILE = Path(__file__).parent / "output" / "api_captures.json"

# Filter: only capture video channel related requests
VIDEO_CHANNEL_HOSTS = [
    "channels.weixin.qq.com",
    "finder.tencent.com",
    "weixin.qq.com",
]

# Interesting API paths (comments, video info, etc.)
API_KEYWORDS = [
    "comment", "Comment",
    "reply", "Reply",
    "feed", "Feed",
    "detail", "Detail",
    "like", "Like",
    "finder", "Finder",
]


class VideoChannelCapture:
    """mitmproxy addon that captures video channel API calls."""

    def __init__(self):
        self.captures: list[dict] = []
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    def response(self, flow: http.HTTPFlow):
        host = flow.request.pretty_host
        path = flow.request.path

        # Filter: must be a video channel host
        if not any(h in host for h in VIDEO_CHANNEL_HOSTS):
            return

        # Filter: must be an interesting API (not static assets)
        if not any(kw in path for kw in API_KEYWORDS):
            # Still capture if it's a POST to channels.weixin.qq.com
            if "channels.weixin.qq.com" not in host or flow.request.method != "POST":
                return

        try:
            body = flow.response.get_text(strict=False)
            # Try to parse as JSON
            try:
                body_json = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                body_json = None

            capture = {
                "timestamp": datetime.now().isoformat(),
                "method": flow.request.method,
                "url": flow.request.pretty_url,
                "host": host,
                "path": path,
                "request_headers": dict(flow.request.headers),
                "request_content_type": flow.request.headers.get("content-type", ""),
                "request_body": None,
                "response_status": flow.response.status_code,
                "response_content_type": flow.response.headers.get("content-type", ""),
                "response_body_preview": body[:2000] if body else None,
                "response_body_json": body_json,
            }

            # Capture request body for POST requests
            if flow.request.method == "POST":
                req_body = flow.request.get_text(strict=False)
                capture["request_body"] = req_body[:5000] if req_body else None

            self.captures.append(capture)
            self._save()

            # Log for immediate feedback
            status = flow.response.status_code
            size = len(body) if body else 0
            logger.info(f"[{status}] {flow.request.method} {host}{path[:80]}  ({size} bytes)")

        except Exception as e:
            logger.error(f"Error capturing {flow.request.pretty_url}: {e}")

    def _save(self):
        OUTPUT_FILE.write_text(
            json.dumps(self.captures, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


async def start_proxy(port: int = 8080):
    """Start mitmproxy with the capture addon."""
    addon = VideoChannelCapture()

    opts = options.Options(listen_port=port, ssl_insecure=True)
    master = DumpMaster(opts)
    master.addons.add(addon)

    logger.info(f"Proxy started on 127.0.0.1:{port}")
    logger.info("Set your system proxy or WeChat proxy to this address.")
    logger.info("Open a video channel video in WeChat and browse comments.")
    logger.info(f"Captures will be saved to: {OUTPUT_FILE}")
    logger.info("Press Ctrl+C to stop.\n")

    try:
        await master.run()
    except KeyboardInterrupt:
        pass
    finally:
        master.shutdown()
        logger.info(f"\nSaved {len(addon.captures)} API captures to {OUTPUT_FILE}")


if __name__ == "__main__":
    import asyncio
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    asyncio.run(start_proxy(port))
