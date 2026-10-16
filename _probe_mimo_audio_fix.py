# _probe_mimo_audio_fix.py
"""直接调用真实 MiMo API 验证口播 prompt 修复：
1. 纯音乐音轨（stdlib 合成的正弦和弦）-> 应返回 None（模型回「无口播」）
2. edge-tts 合成的口播人声 -> 应只输出最终转录，无时间戳/思考过程/重复
"""
import base64
import logging
import math
import os
import struct
import sys
import wave

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

import config  # noqa: E402
from video_processor import VideoProcessor  # noqa: E402

TEST_DIR = os.path.join("output", "debug", "audio_test")


def make_bgm_wav(path: str, seconds: int = 20, rate: int = 16000):
    """生成 20s 三音正弦和弦（模拟纯音乐 BGM，无人声）。"""
    freqs = (261.63, 329.63, 392.0)  # C-E-G 大三和弦
    n = seconds * rate
    frames = bytearray()
    for i in range(n):
        t = i / rate
        v = sum(math.sin(2 * math.pi * f * t) for f in freqs) / 3 * 0.6
        # 音量包络，避免过于机械
        env = 0.7 + 0.3 * math.sin(2 * math.pi * 0.25 * t)
        frames += struct.pack("<h", int(v * env * 32767))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


def to_data_url(path: str, mime: str) -> str:
    with open(path, "rb") as f:
        return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"


def main():
    assert config.MIMO_API_KEY, "MIMO_API_KEY 未配置"
    os.makedirs(TEST_DIR, exist_ok=True)
    bgm_path = os.path.join(TEST_DIR, "bgm_only.wav")
    speech_path = os.path.join(TEST_DIR, "speech.mp3")
    assert os.path.exists(speech_path), "先运行 edge-tts 生成 speech.mp3"
    make_bgm_wav(bgm_path)
    print(f"bgm_only.wav: {os.path.getsize(bgm_path)} bytes")
    print(f"speech.mp3:  {os.path.getsize(speech_path)} bytes")

    vp = VideoProcessor()

    print("\n===== 用例1: 纯音乐BGM（无人声）—— 期望返回 None =====")
    r1 = vp._transcribe_audio_with_mimo(to_data_url(bgm_path, "audio/wav"))
    print(f"结果: {repr(r1)[:200]}")
    ok1 = r1 is None

    print("\n===== 用例2: 口播人声 —— 期望只输出最终转录 =====")
    r2 = vp._transcribe_audio_with_mimo(to_data_url(speech_path, "audio/mpeg"))
    print(f"结果: {repr(r2)[:400] if r2 else 'None'}")
    if r2:
        has_ts = ("00:" in r2) or ("[0" in r2) or ("（0" in r2)
        keywords = ["全季大观", "武汉"]
        kw_ok = all(k in r2 for k in keywords)
        clean = not has_ts and kw_ok
        print(f"含关键词(全季大观/武汉): {kw_ok} | 无时间戳: {not has_ts}")
    else:
        clean = False
    ok2 = clean

    print("\n===== 结论 =====")
    print(f"用例1 纯BGM -> {'PASS' if ok1 else 'FAIL'}")
    print(f"用例2 口播 -> {'PASS' if ok2 else 'FAIL'}")
    sys.exit(0 if (ok1 and ok2) else 1)


if __name__ == "__main__":
    main()
