"""Launch WeChat with remote debugging port for CDP automation.

Usage:
    python launch_wechat.py          # launch with default port 9222
    python launch_wechat.py --port 9333
"""
import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path

WECHAT_PATHS = [
    Path(r"D:\Weixin\Weixin.exe"),
    Path(r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe"),
    Path(r"C:\Program Files\Tencent\WeChat\WeChat.exe"),
    Path(r"D:\Program Files (x86)\Tencent\WeChat\WeChat.exe"),
    Path(r"D:\Program Files\Tencent\WeChat\WeChat.exe"),
]


def find_wechat() -> Path:
    for p in WECHAT_PATHS:
        if p.exists():
            return p
    raise FileNotFoundError(
        "找不到 WeChat.exe，请手动启动微信:\n"
        '  "C:\\...\\WeChat.exe" --remote-debugging-port=9222'
    )


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description="启动微信并开启调试端口")
    parser.add_argument("--port", type=int, default=9222, help="调试端口 (默认 9222)")
    args = parser.parse_args()

    if port_open(args.port):
        print(f"端口 {args.port} 已被占用，微信可能已在调试模式下运行。")
        print("可以直接运行 main.py 进行抓取。")
        return

    exe = find_wechat()
    print(f"启动微信: {exe}")
    print(f"调试端口: {args.port}")
    subprocess.Popen([str(exe), f"--remote-debugging-port={args.port}"])

    print("等待调试端口就绪...")
    for i in range(30):
        time.sleep(1)
        if port_open(args.port):
            print(f"微信调试端口 {args.port} 已就绪！")
            print("现在可以运行 main.py 进行抓取。")
            return
        if i % 5 == 4:
            print(f"  等待中... ({i+1}s)")

    print("警告: 30秒后端口仍未就绪，请检查微信是否正常启动。")
    print(f"也可以手动尝试: WeChat.exe --remote-debugging-port={args.port}")


if __name__ == "__main__":
    main()
