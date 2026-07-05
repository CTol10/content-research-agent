# 帖子内容提取与分类设计

## 概述

为小红书、抖音、微博三个平台的 scraper 增加帖子正文提取功能，并使用 DeepSeek API 对内容进行分类和情感分析，输出到 Excel 的第三个 Sheet。

## 功能需求

### 1. 帖子正文提取

在 scraper 中提取帖子正文内容，与评论一起返回。

**修改 scrape() 返回值**：
```python
# 当前
scrape(url) -> list[tuple[str, str]]  # (nickname, comment_text)

# 修改后
scrape(url) -> dict
{
    "post_content": str,           # 帖子正文
    "comments": list[tuple[str, str]]  # (nickname, comment_text)
}
```

**各平台提取方式**：
- **抖音**：DOM 提取视频描述区域（`.video-info-detail` 或类似选择器）
- **小红书**：DOM 提取笔记内容区域（`.note-content` / `.desc`）
- **微博**：DOM 提取微博正文（`.weibo-text`）或 API 响应中的 `text_raw`

### 2. 内容分类

使用 DeepSeek API 对帖子正文进行分类。

**分类维度**：
- **标签**：从 16 个预定义标签中匹配（可多标签）
- **情感**：每个标签独立判定正面/中性/负面
- **比较检测**：是否提到其他酒店

**16 个标签**：
餐食、茶饮、服务、客房面积、装修、公区设计、客房设计、价格、客房用品、会员权益、卫生、睡眠、洗沐、地理位置、行业营建设计、行业观察

**情感判定标准**：
- 正面：明确夸奖全季大观，或发布看好大观的观点
- 中性：有说到优势也有说到无关痛痒的劣势；或不带任何正/负观点的行业媒体文章
- 负面：提到明确的劣势

**比较检测标准**：
- 内容中提到除全季大观外的酒店 → "是"
- 未提到其他酒店 → "否"

### 3. Excel 输出格式

**Sheet 1 - 原文内容**（保持现状，不修改）
- 从输入 Excel 读取的原始数据

**Sheet 2 - 评论**（保持现状）
- 序号 | ID名称 | 评论内容

**Sheet 3 - 内容分析**（新增）
- 序号 | 标题 | 内容 | 标签 | 是否比较其他酒店

**标签列格式**：多个标签用逗号分隔，格式为 `标签-情感`
- 示例：`睡眠-正面,服务-负面,餐食-中性`

## 技术实现

### 文件修改清单

1. `scrapers/xiaohongshu.py` - 提取帖子正文
2. `scrapers/douyin.py` - 提取帖子正文
3. `scrapers/weibo.py` - 提取帖子正文
4. `classifier.py` (新建) - DeepSeek API 分类模块
5. `main.py` - 调用分类器，修改 Excel 输出逻辑
6. `config.py` - 添加 DeepSeek API 配置

### DeepSeek API 集成

**配置**：
```python
# config.py
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
```

**分类模块** `classifier.py`：
```python
def classify_content(post_content: str) -> dict:
    """使用 DeepSeek API 分类帖子内容"""
    prompt = f"""
    分析以下帖子内容，返回 JSON 格式：
    - tags: 匹配的标签列表，每个标签带情感（正面/中性/负面）
    - has_comparison: 是否提到其他酒店（"是"/"否")

    帖子内容：{post_content}

    返回格式：
    {{
        "tags": ["睡眠-正面", "服务-负面"],
        "has_comparison": "是"
    }}
    """
    # 调用 DeepSeek API
    # 解析返回的 JSON
```

**错误处理**：
- API 调用失败时跳过该帖子，标签列留空
- 记录错误日志

### 主流程修改

```python
# main.py 中的 scrape_all 函数
for row in platform_rows:
    url = row["url"]
    result = await scraper.scrape(url)  # 返回 dict
    post_content = result["post_content"]
    comments = result["comments"]

    # 分类
    classification = classify_content(post_content)

    # 写入 Excel
    write_comments_excel(outdir, comments)
    write_content_analysis(outdir, row, classification)  # 新增
```

## 依赖

- `openpyxl` (已有)
- `requests` 或 `httpx` (调用 DeepSeek API)
- DeepSeek API Key (用户配置)

## 测试策略

1. 单元测试：分类模块的 prompt 构建和 JSON 解析
2. 集成测试：各平台 scraper 的正文提取
3. 端到端测试：完整流程（抓取 → 分类 → Excel 输出）
