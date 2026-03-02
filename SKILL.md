---
name: sogou-wechat-search
description: 双引擎微信公众号搜索：Tavily API（精准，优先）+ 搜狗微信爬虫（兜底），搜索公众号文章或账号信息并以结构化格式展示。支持时间过滤（--days）和多关键词（逗号/OR）。
version: 2.1.0
author: custom
tags: [wechat, search, scraper, tavily, 微信, 公众号]
requirements:
  - python3
  - requests
  - beautifulsoup4
  - tavily-python
  - python-dotenv
---

# 微信公众号双引擎搜索 Skill

## 功能

双引擎搜索微信公众号内容：

| 引擎 | 优先级 | 特点 |
|------|--------|------|
| **Tavily API** | 第一层（优先） | 精准、直接返回 mp.weixin.qq.com 真实链接、内容最新 |
| **搜狗微信爬虫** | 第二层（兜底） | 无需 API Key、包含公众号名/发布时间/封面图 |

**两种搜索模式：**
1. **文章搜索**（默认）：按关键词搜索公众号文章，返回标题、摘要、公众号名、时间、链接、来源
2. **公众号搜索**：聚合文章结果，按公众号名汇总，展示相关公众号及近期文章

---

## 使用方式

当用户提出以下类型的请求时，触发本 Skill：

- "搜一下微信公众号关于 XXX 的文章"
- "帮我找 XXX 的公众号"
- "微信上有没有关于 XXX 的内容"
- "搜狗微信搜索 XXX"

---

## 执行步骤

### Step 1：判断搜索模式

- 用户想找**文章内容** → `--type article`（默认）
- 用户想找**公众号账号** → `--type account`

### Step 2：运行爬虫脚本

脚本路径：`.claude/skills/sogou-wechat-search/sogou_search.py`

```bash
# 搜索文章（默认，抓1页约10条）
python ".claude/skills/sogou-wechat-search/sogou_search.py" --query "关键词" --type article --pages 1

# 搜索文章，多页
python ".claude/skills/sogou-wechat-search/sogou_search.py" --query "关键词" --type article --pages 3

# 搜索公众号
python ".claude/skills/sogou-wechat-search/sogou_search.py" --query "公众号名称" --type account

# 时间过滤：只看最近 N 天的文章
python ".claude/skills/sogou-wechat-search/sogou_search.py" --query "关键词" --days 1
python ".claude/skills/sogou-wechat-search/sogou_search.py" --query "关键词" --days 7

# 多关键词（逗号分隔 或 OR 语法）
python ".claude/skills/sogou-wechat-search/sogou_search.py" --query "关键词A,关键词B"
python ".claude/skills/sogou-wechat-search/sogou_search.py" --query "关键词A OR 关键词B"

# 组合用法
python ".claude/skills/sogou-wechat-search/sogou_search.py" --query "昇腾950,昇腾AI" --days 3
```

> **编码说明（Windows）**：在 Windows 上运行前，先执行 `chcp 65001` 或使用以下完整命令：
> ```bash
> cmd /c "chcp 65001 >nul & python \".claude/skills/sogou-wechat-search/sogou_search.py\" --query \"关键词\" --type article"
> ```

### Step 3：解析 JSON 输出

脚本输出为 JSON 格式，字段说明：

**成功响应：**
```json
{
  "success": true,
  "query": "搜索词",
  "mode": "article",
  "total_found": 10,
  "pages_fetched": 1,
  "results": [
    {
      "title": "文章标题",
      "account": "公众号名称",
      "date": "2025-03-01 10:00",
      "abstract": "文章摘要...",
      "link": "https://...",
      "cover": "封面图URL"
    }
  ]
}
```

**失败响应（触发验证码）：**
```json
{
  "success": false,
  "error": "触发反爬验证码，请稍后重试..."
}
```

### Step 4：格式化展示结果

将结果整理为用户友好的格式：

**文章搜索结果示例：**
```
📰 搜索「量化投资」找到 10 篇文章

1. **标题**
   📌 公众号：XXX  |  🕐 2025-03-01
   摘要内容...
   🔗 [阅读原文](链接)

2. ...
```

**公众号搜索结果示例：**
```
🔍 搜索「招商证券」找到 5 个公众号

1. **公众号名称**
   微信号：ID
   简介：...
   近期文章：
   - 文章标题1
   - 文章标题2
   🔗 [查看主页](链接)
```

---

## 注意事项 & 限制

| 项目 | 说明 |
|------|------|
| 🚫 反爬限制 | 搜狗有频率限制，建议每次搜索间隔 ≥ 5 秒 |
| 🔒 验证码 | 频繁请求会触发滑块验证码，脚本会返回 error 提示 |
| 📄 内容获取 | 本 Skill 只获取**搜索列表页**信息，不抓取文章全文 |
| 🌐 网络 | 需要能访问 weixin.sogou.com |
| 📦 依赖 | 首次使用需安装：`pip install requests beautifulsoup4` |

---

## 依赖安装

如果运行报错"缺少依赖"，执行：

```bash
pip install requests beautifulsoup4 -q
```

---

## 错误处理

| 错误情况 | 处理方式 |
|----------|----------|
| 触发验证码 | 告知用户等待 5-10 分钟后重试 |
| 网络超时 | 提示检查网络连接 |
| 结果为空 | 建议用户换用其他关键词 |
| 依赖缺失 | 提示安装命令 |
