# 多平台评论抓取工具设计文档

## 1. 项目概述

从 `example.xlsx` 中读取多个社交媒体平台的文章链接，使用 Playwright 浏览器自动化抓取每篇文章下的评论（含回复），每个帖子输出为独立的 Excel 文件。

## 2. 需求

### 2.1 输入
- 文件：`example.xlsx`（420条数据）
- 关键列：第10列 `原文/评论链接`

### 2.2 支持平台（第一期）
| 平台 | URL特征 |
|------|---------|
| 抖音 | `iesdouyin.com` |
| 小红书 | `xiaohongshu.com` |
| 微信公众号 | `mp.weixin.qq.com` |
| 今日头条 | `toutiao.com` |
| 新浪微博 | `weibo.com` |

### 2.3 输出
每个帖子生成独立的 Excel 文件，表头为：

| 序号 | ID名称 | 评论内容 |
|------|--------|---------|
| 1 | 用户A | 这条评论很好 |
| 2 | 用户B | 回复用户A：确实 |

- `序号`：该帖子内评论的递增编号
- `ID名称`：评论者昵称
- `评论内容`：评论文本（回复纯平铺，不标记）

额外生成汇总报告 `scrape_report.xlsx`，记录每个帖子的抓取状态。

### 2.4 约束
- 抓取方式：Playwright 浏览器自动化
- 登录：首次手动登录，Cookie 自动保存复用
- 失败处理：跳过 + 日志记录
- 评论数量：全部抓取
- 技术栈：Python

## 3. 架构设计

### 3.1 项目结构

```
auto-fetch-comment/
├── main.py                 # 主调度器
├── config.py               # 配置（延迟、超时等）
├── scrapers/
│   ├── __init__.py
│   ├── base.py             # 爬虫基类
│   ├── douyin.py           # 抖音
│   ├── xiaohongshu.py      # 小红书
│   ├── wechat.py           # 微信公众号
│   ├── toutiao.py          # 今日头条
│   └── weibo.py            # 新浪微博
├── cookies/                # 各平台Cookie存储
│   ├── douyin.json
│   ├── xiaohongshu.json
│   └── ...
├── logs/
│   └── scrape_YYYYMMDD.log
├── example.xlsx            # 输入文件
├── requirements.txt
└── output/                 # 输出文件夹
    ├── {平台}_{标题前20字}_{日期}/
    │   └── comments.xlsx
    └── scrape_report_YYYYMMDD.xlsx
```

### 3.2 数据流

```
example.xlsx
    │
    ▼
main.py (读取Excel，提取URL列表)
    │
    ├─ URL域名匹配 → 抖音爬虫 → scrape(url) → [(昵称, 评论)]
    ├─ URL域名匹配 → 小红书爬虫 → scrape(url) → [(昵称, 评论)]
    ├─ URL域名匹配 → 微信爬虫 → scrape(url) → [(昵称, 评论)]
    ├─ URL域名匹配 → 今日头条爬虫 → scrape(url) → [(昵称, 评论)]
    └─ URL域名匹配 → 微博爬虫 → scrape(url) → [(昵称, 评论)]
    │
    ▼
main.py (汇总结果，写入独立Excel + 汇总报告)
```

### 3.3 基类设计

```python
class BaseScraper:
    def __init__(self, platform_name: str):
        self.platform_name = platform_name
        self.cookie_path = f"cookies/{platform_name}.json"

    async def init_browser(self) -> Browser:
        """初始化Playwright浏览器，加载Cookie"""

    async def login_interactive(self):
        """首次运行：打开浏览器让用户手动登录，保存Cookie"""

    async def load_cookies(self) -> bool:
        """加载已保存的Cookie，返回是否成功"""

    async def save_cookies(self):
        """保存当前Cookie到文件"""

    async def scroll_and_collect(self, page, max_scroll=50) -> list:
        """通用滚动加载逻辑，子类可覆盖"""

    async def scrape(self, url: str) -> list[tuple[str, str]]:
        """核心方法：抓取评论，返回 [(昵称, 评论内容), ...]"""
        raise NotImplementedError
```

### 3.4 平台爬虫实现要点

| 平台 | 评论加载方式 | 关键挑战 |
|------|-------------|---------|
| 抖音 | 页面内嵌评论区，需滚动加载更多 | 需要登录态，评论区可能需要点击展开 |
| 小红书 | 评论区在笔记详情页，滚动加载 | 需要登录，评论区DOM结构频繁变化 |
| 微信公众号 | 评论在文章底部，点击"展开更多" | 部分文章不开放评论 |
| 今日头条 | 评论区在文章下方，滚动或点击加载 | 可能有反爬检测 |
| 新浪微博 | 评论在博文详情页，滚动加载 | 需要登录，有频率限制 |

### 3.5 错误处理

- 每个URL的抓取用 `try/except` 包裹
- 失败时记录到日志（URL、平台、错误信息），跳过继续
- Cookie过期时提示用户重新登录
- 每次页面操作后等待1-3秒随机延迟

### 3.6 日志格式

```
2026-05-15 14:30:01 [INFO] 开始抓取，共420条链接
2026-05-15 14:30:05 [INFO] [抖音] 正在抓取: https://www.iesdouyin.com/...
2026-05-15 14:30:15 [INFO] [抖音] 抓取成功，获得12条评论
2026-05-15 14:30:20 [ERROR] [微信] 抓取失败: https://mp.weixin.qq.com/... - 评论区未找到
2026-05-15 15:00:00 [INFO] 抓取完成，成功: 380条，失败: 40条
```

### 3.7 输出文件命名

- 帖子文件：`output/{平台}_{标题前20字}_{日期}/comments.xlsx`
- 标题中的非法字符（`/\:*?"<>|`）替换为下划线，去除首尾空格
- 汇总报告：`output/scrape_report_{日期}.xlsx`

汇总报告格式：

| 序号 | 平台 | 标题 | URL | 状态 | 评论数 | 错误信息 |
|------|------|------|-----|------|--------|---------|
| 1 | 抖音 | 你们都在玩梗... | https://... | 成功 | 12 | |
| 2 | 微信 | 酒店首店扑街... | https://... | 失败 | 0 | 评论区未找到 |

## 4. 依赖

```
playwright
openpyxl
```

## 5. 使用流程

1. `pip install -r requirements.txt`
2. `playwright install chromium`
3. 首次运行：`python main.py --login`（弹出浏览器，手动登录各平台，Cookie自动保存）
4. 后续运行：`python main.py`（自动使用保存的Cookie，批量抓取）
5. 结果输出到 `output/` 目录
