"""Analyze captured API calls from capture_api.py.

Usage: python analyze_api.py [api_captures.json]
"""
import json
import sys
from pathlib import Path
from collections import defaultdict


def main():
    filepath = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "output" / "api_captures.json"
    if not filepath.exists():
        print(f"File not found: {filepath}")
        print("Run capture_api.py first to capture API calls.")
        return

    captures = json.loads(filepath.read_text(encoding="utf-8"))
    print(f"Total captures: {len(captures)}\n")

    # Group by host + path pattern
    groups = defaultdict(list)
    for cap in captures:
        key = f"{cap['method']} {cap['host']}{cap['path'].split('?')[0]}"
        groups[key].append(cap)

    print("=" * 80)
    print("API ENDPOINTS SUMMARY")
    print("=" * 80)
    for key, caps in sorted(groups.items(), key=lambda x: -len(x[1])):
        print(f"\n{key}  (x{len(caps)})")
        # Show unique status codes
        statuses = set(c["response_status"] for c in caps)
        print(f"  Status codes: {statuses}")
        # Show request content type
        ctypes = set(c["request_content_type"] for c in caps if c["request_content_type"])
        if ctypes:
            print(f"  Request type: {ctypes}")

    # Show comment-related APIs in detail
    print("\n" + "=" * 80)
    print("COMMENT-RELATED APIs (detailed)")
    print("=" * 80)
    for cap in captures:
        path_lower = cap["path"].lower()
        if "comment" in path_lower or "reply" in path_lower or "discuss" in path_lower:
            print(f"\n--- {cap['method']} {cap['url'][:120]}")
            print(f"    Status: {cap['response_status']}")
            if cap.get("request_body"):
                try:
                    body = json.loads(cap["request_body"])
                    print(f"    Request body keys: {list(body.keys())}")
                    # Show non-sensitive fields
                    for k, v in body.items():
                        if isinstance(v, (str, int, float, bool)):
                            vstr = str(v)[:100]
                        elif isinstance(v, dict):
                            vstr = f"{{keys: {list(v.keys())}}}"
                        elif isinstance(v, list):
                            vstr = f"[len={len(v)}]"
                        else:
                            vstr = str(v)[:100]
                        print(f"      {k}: {vstr}")
                except (json.JSONDecodeError, TypeError):
                    print(f"    Request body (raw): {cap['request_body'][:200]}")
            if cap.get("response_body_json"):
                body = cap["response_body_json"]
                if isinstance(body, dict):
                    print(f"    Response keys: {list(body.keys())}")
                    # Look for comment data
                    for k in ["comments", "comment_list", "data", "result"]:
                        if k in body:
                            val = body[k]
                            if isinstance(val, list):
                                print(f"    -> {k}: list of {len(val)} items")
                                if val:
                                    print(f"       first item keys: {list(val[0].keys()) if isinstance(val[0], dict) else val[0]}")
                            elif isinstance(val, dict):
                                print(f"    -> {k}: dict with keys {list(val.keys())}")

    # Show auth-related headers
    print("\n" + "=" * 80)
    print("AUTH HEADERS (from first capture)")
    print("=" * 80)
    if captures:
        headers = captures[0].get("request_headers", {})
        for key in ["Cookie", "Authorization", "X-Wechat-Uin", "X-Request-ID",
                     "Referer", "User-Agent", "Content-Type"]:
            val = headers.get(key, headers.get(key.lower(), ""))
            if val:
                # Truncate long values
                print(f"  {key}: {val[:150]}{'...' if len(val) > 150 else ''}")


if __name__ == "__main__":
    main()
