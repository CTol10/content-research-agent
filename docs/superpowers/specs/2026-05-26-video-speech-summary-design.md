# 视频口播内容 AI 总结设计

## 概述

在现有爬虫基础上，增加对抖音、小红书、微博、头条四个平台视频帖子的口播内容进行 AI 总结理解。通过 Playwright 网络拦截捕获视频 URL，下载后用 ffmpeg 提取音频，调用阿里 DashScope Paraformer-v2 进行语音转文字，再用 DeepSeek 生成结构化总结，最后复用现有酒店标签分类器进行分类。

## 功能需求

### 1. 视频捕获

在各平台 scraper 的 `scrape()` 方法中，利用已有的 Playwright 页面上下文，在浏览页面时同步拦截视频请求。

**拦截规则**：
- 匹配响应头 `Content-Type: video/*` 或 URL 后缀 `.mp4`, `.m3u8`, `.webm`, `.ts`
- 过滤广告、追踪等无关视频（通过 URL 路径关键词过滤）
- 只保留最大的视频文件（按 `Content-Length` 排序，取最大的一个）
- 页面加载后等待最多 5 秒收集视频请求

**各平台视频 URL 特征**：

| 平台 | 视频 URL 特征 | 备注 |
|------|-------------|------|
| 抖音 | `v*.douyinvod.com/*.mp4` | 主视频通常在页面加载时就发起 |
| 小红书 | `sns-video*.xhscdn.com/*` | 视频笔记才有 |
| 微博 | `video.weibo.com/*` 或 `*.mp4` | 视频微博才有 |
| 头条 | `v*.toutiao*.com/*` | 视频文章才有 |

**与媒体暂停守卫的交互**：
- 现有的 `_install_media_pause_guard()` 会暂停页面上的 `<video>` 和 `<audio>` 元素播放
- 这不影响网络拦截——视频流请求在页面加载时就已经发出，暂停守卫只控制 DOM 元素的播放状态
- 两者互不干扰，无需修改现有暂停守卫逻辑

### 2. 音频提取

从下载的视频中用 ffmpeg 提取音频：

```bash
ffmpeg -i input.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 output.wav
```

- 输出格式：16kHz 单声道 WAV（DashScope Paraformer-v2 推荐输入格式）
- 视频无音频轨（纯画面视频）→ 直接标记为「无口播视频」
- 提取完成后删除原始视频文件

### 3. 语音转文字（DashScope Paraformer-v2）

调用阿里 DashScope ASR API：

```python
import dashscope
from dashscope.audio.asr import Transcription

task_response = Transcription.async_call(
    model='paraformer-v2',
    file_urls=[audio_url],
    language_hints=['zh']
)
```

**关键细节**：
- 本地文件需先通过 DashScope 文件上传 API 上传
- 转录结果包含带时间戳的文本段落，拼接为完整转录
- 音频超过 5 分钟 → 截取前 5 分钟处理

### 4. 内容总结（DeepSeek API）

拿到转录文本后，调用 DeepSeek 生成结构化总结：

**Prompt**：
```
你是一个视频内容分析助手。根据视频口播转录文本，生成简洁的内容摘要。

要求：
1. 用一句话概括视频的核心内容
2. 识别视频拍摄的对象（如：客房、大堂、餐厅、泳池等）
3. 提取关键信息点

输出格式：
拍摄对象：[对象]
内容摘要：[一句话总结]
关键信息：[要点1, 要点2, ...]
```

**输出解析**：
- 提取「拍摄对象」字段，用于生成 `「拍摄XX视频」` 格式
- 提取「内容摘要」字段，作为后续分类的输入

### 5. 分类集成

- 将 LLM 生成的「内容摘要」传入现有的 `classify_content_expanded()`
- 和文字内容走完全相同的分类流程
- 分类结果写入对应的标签和情感列

### 6. Excel 输出格式

**Sheet 3「正文内容+标签+情感」** 的「内容摘要」列：

**正常视频（有口播）**：
- 内容摘要列：`「拍摄客房视频」\n\n视频核心内容：xxx`（标签 + 摘要合并）
- 正常走分类器，标签/情感列正常填写
- 字体颜色：默认黑色

**无口播视频**：
- 内容摘要列：`「无口播视频」`
- 分类标签写入 `/`，情感写入 `/`
- 字体颜色：**黄色**（用 openpyxl 的 Font 设置）
- 加批注（Comment）标注"需人工复核"

**无视频的帖子**：
- 内容摘要列保持现有的文字描述内容，不受影响

## 技术实现

### 新增文件

**`video_processor.py`** — 视频处理核心模块：

```python
class VideoProcessor:
    def __init__(self, dashscope_api_key: str, deepseek_api_key: str):
        self.temp_dir = config.VIDEO_TEMP_DIR
        os.makedirs(self.temp_dir, exist_ok=True)

    def process_video(self, video_url: str) -> dict:
        """完整处理流水线"""
        # 1. 下载视频
        video_path = self.download_video(video_url)
        if not video_path:
            return {"status": "download_failed"}

        # 2. 提取音频
        audio_path = self.extract_audio(video_path)
        if not audio_path:
            return {"status": "no_audio", "label": "「无口播视频」"}

        # 3. 语音转文字
        transcript = self.transcribe_audio(audio_path)
        if not transcript:
            return {"status": "no_speech", "label": "「无口播视频」"}

        # 4. LLM 总结
        summary = self.summarize_transcript(transcript)

        # 5. 清理临时文件
        self.cleanup(video_path, audio_path)

        return {
            "status": "success",
            "label": f"「拍摄{summary['拍摄对象']}视频」",
            "summary": summary["内容摘要"],
            "transcript": transcript,
            "key_info": summary["关键信息"]
        }

    def download_video(self, url: str) -> str | None:
        """用 httpx 下载视频到临时目录"""

    def extract_audio(self, video_path: str) -> str | None:
        """用 ffmpeg 提取音频"""

    def transcribe_audio(self, audio_path: str) -> str | None:
        """调用 DashScope Paraformer-v2 转录"""

    def summarize_transcript(self, transcript: str) -> dict:
        """调用 DeepSeek 生成结构化总结"""

    def cleanup(self, *files):
        """清理临时文件"""
```

### 修改文件清单

1. **`scrapers/base.py`** — 添加视频捕获基础设施
   - `_setup_video_capture(page)` — 注册网络拦截器
   - `_capture_video_response(response)` — 判断并记录视频 URL
   - `_get_primary_video_url()` — 返回主视频 URL

2. **`scrapers/douyin.py`** / **`xiaohongshu.py`** / **`weibo.py`** / **`toutiao.py`**
   - 在 `scrape()` 中调用 `_setup_video_capture(page)`
   - 返回值新增 `video_url` 字段

3. **`main.py`**
   - `scrape_all()` 中集成视频处理流程
   - 新增 `--no-video` CLI 参数
   - Sheet 3 写入逻辑适配视频总结结果
   - `--setup` 流程中增加 ffmpeg 安装

4. **`config.py`**
   - 新增视频处理配置项
   - 新增 DashScope API 配置项

5. **`requirements.txt`**
   - 新增 `dashscope>=1.20.0`

### 新增依赖

```
dashscope>=1.20.0      # 阿里 DashScope SDK（ASR）
```

`ffmpeg` 需要系统安装，在 `--setup` 流程中自动处理（Windows 通过 winget 安装）。

### 配置项（config.py 新增）

```python
# 视频处理配置
VIDEO_TEMP_DIR = os.path.join(OUTPUT_DIR, "video_temp")
MAX_VIDEO_SIZE_MB = 200
MAX_TRANSCRIPT_SECONDS = 300
VIDEO_DOWNLOAD_TIMEOUT = 60
ENABLE_VIDEO_PROCESSING = True

# DashScope 配置
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
ASR_MODEL = "paraformer-v2"
ASR_LANGUAGE = "zh"

# 视频 URL 过滤关键词
VIDEO_URL_EXCLUDE_KEYWORDS = ["ad", "tracker", "analytics", "beacon"]
```

## 错误处理

| 场景 | 处理方式 |
|------|---------|
| 无视频请求 | 跳过视频处理，使用原有文字描述 |
| 视频下载失败（超时/403） | 记录警告日志，跳过，使用原有文字描述 |
| 视频无音频轨 | 标记「无口播视频」，黄色字体 |
| 音频提取失败 | 同上 |
| DashScope API 调用失败 | 重试 2 次，仍失败则记录错误，跳过 |
| 转录结果为空（纯音乐/噪音） | 标记「无口播视频」，黄色字体 |
| DeepSeek 总结失败 | 重试 2 次，仍失败则使用原始转录文本截断作为摘要 |
| 视频文件过大（>200MB） | 跳过，记录警告 |
| DashScope API key 未配置 | 打印提示信息，跳过视频处理（不中断整个流程） |

## Checkpoint 兼容

- 视频处理结果随 URL 一起写入 checkpoint
- `--resume` 时，已完成的 URL 不会重新处理视频
- checkpoint 新增 `video_summary` 字段存储处理结果

## 日志策略

- 视频捕获：`DEBUG` 级别记录捕获到的视频 URL
- 下载进度：`INFO` 级别记录下载开始/完成/失败
- STT 结果：`DEBUG` 级别记录转录文本
- 总结结果：`INFO` 级别记录生成的摘要
- 临时文件：处理完成后自动清理，`DEBUG` 级别记录清理操作

## 测试策略

1. 单元测试：`VideoProcessor` 各方法（mock API 调用）
2. 集成测试：各平台 scraper 的视频捕获
3. 端到端测试：完整流程（抓取 → 视频处理 → 分类 → Excel 输出）
