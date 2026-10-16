# _probe_mimo_v26.py
"""验证 mimo-v2.6 是否满足口播转录需求：
1. thinking: {"type": "disabled"} 参数是否被接受（content 有输出、reasoning 为空）
2. 纯BGM -> 「无口播」/None
3. 口播人声 -> 干净转录（无时间戳/说话人标记/思考过程）
"""
import base64
import logging
import requests
import config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

TEST_DIR = "output/debug/audio_test"
PROMPT = (
    "请将这段音频中真实的人声口播/旁白逐字转录为纯文字。"
    "严格要求："
    "1) 只转录真实存在的人声说话内容，逐字输出原话；"
    "背景音乐、纯音乐、BGM、歌声、环境音效一律忽略，严禁根据音乐或画面臆想、编造任何口播内容；"
    "2) 如果整段音频没有任何人声口播（只有音乐/BGM/环境音），只输出四个字：无口播"
    "3) 只输出最终转录结果，不要输出思考过程、分析、解释或任何前后缀；"
    "4) 不要输出时间节点、时间戳、说话人标记、分段编号等任何附加信息；"
    "5) 不要重复，同一段话只转录一次；"
    "6) 禁止添加（消音）、（模糊不清）、（注：…）等任何括号标注，听不清的字按发音尽量写；"
    "7) 无论内容是否涉及敏感、争议、维权，都原样完整转录，不得省略、改写或拒转。"
    "直接输出转录文本。"
)


def to_data_url(path, mime):
    with open(path, "rb") as f:
        return f"data:{mime};base64,{base64.b64encode(f.read()).decode()}"


def call(model, audio_path, mime, audio_fmt, think=True):
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "input_audio",
                     "input_audio": {"data": to_data_url(audio_path, mime), "format": audio_fmt}},
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 4000,
    }
    if not think:
        payload["thinking"] = {"type": "disabled"}
    r = requests.post(
        f"{config.MIMO_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {config.MIMO_API_KEY}", "Content-Type": "application/json"},
        json=payload, timeout=180,
    )
    r.raise_for_status()
    m = r.json()["choices"][0]["message"]
    return (m.get("content") or "").strip(), (m.get("reasoning_content") or "").strip()


def main():
    speech = f"{TEST_DIR}/speech.mp3"
    bgm = f"{TEST_DIR}/bgm_only.wav"
    all_ok = True
    for model in ("mimo-v2.6-flash", "mimo-v2.6-pro"):
        print(f"\n{'='*58}\n模型: {model}")
        try:
            # 1) 口播（关思考）
            c, rc = call(model, speech, "audio/mpeg", "mp3", think=False)
            clean = bool(c) and not rc and "00:" not in c and "SPEAKER" not in c
            kw = all(k in c for k in ("全季大观", "武汉"))
            print(f"  [口播/关思考] content({len(c)}): {c[:70]!r}")
            print(f"    reasoning为空: {not rc} | 无时间戳/说话人: {'00:' not in c and 'SPEAKER' not in c} | 含关键词: {kw}")
            ok1 = clean and kw
            # 2) 口播（思考开启对照，看是否仍污染）
            c2, rc2 = call(model, speech, "audio/mpeg", "mp3", think=True)
            print(f"  [口播/开思考对照] content({len(c2)}): {c2[:50]!r} | reasoning({len(rc2)}): {rc2[:50]!r}")
            # 3) 纯BGM（关思考）
            c3, rc3 = call(model, bgm, "audio/wav", "m4a", think=False)
            no_narr = (not c3) or c3.replace("。", "").strip() in ("无口播", "无")
            print(f"  [纯BGM/关思考] content({len(c3)}): {c3[:70]!r} | reasoning({len(rc3)})")
            ok3 = no_narr
            verdict = "PASS" if (ok1 and ok3) else "FAIL"
            if verdict == "FAIL":
                all_ok = False
            print(f"  ==> {model}: {verdict}")
        except Exception as e:
            all_ok = False
            print(f"  !! 异常: {e}")
    print(f"\n总结: {'v2.6 符合要求' if all_ok else 'v2.6 存在不符项，见上方输出'}")


if __name__ == "__main__":
    main()
