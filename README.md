# wechat-search-skill

> 微信公众号双引擎搜索 Claude Skill
> **Tavily API（精准）+ 搜狗微信爬虫（兜底）**，无缝切换，零中断。

## 功能特性

- 🔍 **文章搜索**：按关键词搜索微信公众号文章，返回标题、摘要、公众号名、发布时间、链接
- 📋 **公众号搜索**：聚合相关公众号及其近期文章
- ⚡ **双引擎自动调度**：Tavily 优先（精准，直链 `mp.weixin.qq.com`），搜狗兜底（无需 API Key）
- 🔄 **去重合并**：两个引擎结果自动去重
- 🛡️ **反爬保护**：多页请求随机延时，降低被封风险

## 搜索引擎说明

| 引擎 | 优先级 | Key 要求 | 特点 |
|------|--------|----------|------|
| Tavily API | 第一层 | 需要 `TAVILY_API_KEY` | 精准、返回真实微信链接、内容最新 |
| 搜狗微信爬虫 | 第二层（兜底） | 无需 | 包含公众号名/时间/封面图，可能触发验证码 |

## 安装

```bash
# 1. 克隆到你的 Claude skills 目录
git clone https://github.com/WupfAGI/wechat-search-skill \
  ~/.claude/skills/sogou-wechat-search

# 2. 安装 Python 依赖
pip install -r ~/.claude/skills/sogou-wechat-search/requirements.txt
```

## 配置（可选）

在 `~/.claude/scripts/.env` 中添加 Tavily API Key（无 Key 也能用搜狗兜底）：

```env
TAVILY_API_KEY=tvly-xxxxxxxxxxxxxxxx
```

> 获取免费 Key：https://tavily.com

## 命令行用法

```bash
# 搜索文章（auto 模式：Tavily 优先，不足则补充搜狗）
python sogou_search.py --query "量化投资" --type article --max-results 10

# 仅用搜狗，搜索 2 页
python sogou_search.py --query "AI大模型" --source sogou --pages 2

# 仅用 Tavily
python sogou_search.py --query "DeepSeek" --source tavily --max-results 20

# 搜索公众号（聚合模式）
python sogou_search.py --query "招商证券" --type account

# 时间过滤：只返回最近 1 天 / 7 天的文章
python sogou_search.py --query "昇腾950" --days 1
python sogou_search.py --query "大模型" --days 7 --source tavily

# 多关键词（逗号分隔 或 OR 语法，自动合并去重）
python sogou_search.py --query "昇腾950,昇腾AI" --days 3
python sogou_search.py --query "DeepSeek OR Qwen" --max-results 15
```

### 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--query` / `-q` | 必填 | 搜索关键词。支持多关键词：`"词A,词B"` 或 `"词A OR 词B"` |
| `--type` / `-t` | `article` | `article`=文章 / `account`=公众号 |
| `--source` / `-s` | `auto` | `auto` / `tavily` / `sogou` |
| `--max-results` / `-n` | `10` | 最大返回条数（≤20） |
| `--pages` / `-p` | `1` | 搜狗抓取页数（每页约10条） |
| `--days` / `-d` | `0` | 只返回最近 N 天的文章（0=不限制）。Tavily API 层过滤+搜狗结果层过滤 |

## 输出格式（JSON）

```json
{
  "success": true,
  "query": "量化投资",
  "mode": "article",
  "sources_used": ["tavily", "sogou"],
  "tavily_available": true,
  "total_found": 10,
  "results": [
    {
      "title": "文章标题",
      "account": "公众号名称",
      "date": "2026-03-01",
      "abstract": "文章摘要...",
      "link": "https://mp.weixin.qq.com/s/xxx",
      "cover": "封面图URL",
      "source": "tavily"
    }
  ]
}
```

## 在 Claude Code 中使用

将本 Skill 放入 `.claude/skills/` 目录后，直接对 Claude 说：

- "帮我搜一下微信公众号关于**量化投资**的最新文章"
- "找找有哪些公众号在写 **DeepSeek**"
- "搜狗微信搜索**招商证券**"

## 注意事项

- 搜狗有频率限制，建议每次请求间隔 ≥ 5 秒
- 频繁请求会触发滑块验证码，触发后建议等待 5-10 分钟
- Tavily 免费套餐每月 1000 次请求

## License

MIT
