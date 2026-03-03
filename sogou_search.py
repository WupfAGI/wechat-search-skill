# -*- coding: utf-8 -*-
"""
微信公众号搜索（双引擎）
Layer 1: Tavily API（site:mp.weixin.qq.com，精准，有 Key 优先走这里）
Layer 2: 搜狗微信爬虫（兜底，无需 API Key）

用法：
  python sogou_search.py --query "量化投资" --type article --max-results 10
  python sogou_search.py --query "招商证券" --type account
  python sogou_search.py --query "AI大模型" --source sogou --pages 2
  python sogou_search.py --query "量化" --source tavily --max-results 20

  # 时间过滤（过去 N 天）
  python sogou_search.py --query "昇腾950" --days 1
  python sogou_search.py --query "DeepSeek" --days 7 --source tavily

  # 多关键词（逗号或 OR）
  python sogou_search.py --query "昇腾950,昇腾950PR"
  python sogou_search.py --query "DeepSeek OR 大模型"

  # AI 摘要
  python sogou_search.py --query "DeepSeek" --days 3 --summary

  # 关闭去噪过滤（默认开启）
  python sogou_search.py --query "限时福利" --no-filter
"""

import argparse
import json
import os
import sys
import time
import random
import re
import datetime
from pathlib import Path

# ── Windows 终端 UTF-8 编码修复 ──
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print(json.dumps({"error": "缺少依赖，请运行: pip install requests beautifulsoup4"}, ensure_ascii=False))
    sys.exit(1)

# ──────────────────────────────────────────────
# 加载 .env（读取 TAVILY_API_KEY / ANTHROPIC_API_KEY）
# ──────────────────────────────────────────────
def load_env() -> dict:
    """从 ~/.claude/scripts/.env 加载环境变量"""
    env_path = Path.home() / ".claude" / "scripts" / ".env"
    env_vars = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env_vars[k.strip()] = v.strip()
    # 系统环境变量优先于文件
    for k in list(env_vars.keys()):
        env_vars[k] = os.environ.get(k, env_vars[k])
    # 额外读取系统中存在但 .env 未声明的关键变量
    for extra in ("ANTHROPIC_API_KEY", "TAVILY_API_KEY", "FEISHU_WEBHOOK_URL", "FEISHU_SECRET"):
        if extra not in env_vars and os.environ.get(extra):
            env_vars[extra] = os.environ[extra]
    return env_vars

ENV = load_env()
TAVILY_API_KEY = ENV.get("TAVILY_API_KEY", "")


# ──────────────────────────────────────────────
# Feature 8：垃圾词去噪
# ──────────────────────────────────────────────
SPAM_KEYWORDS = [
    # 诱导领取类
    "免费领取", "点击领取", "扫码领取", "立即领取", "戳我领取",
    "限时免费", "限时福利", "0元领", "白嫖", "薅羊毛",
    # 商业推广类
    "商务合作", "广告投放", "商业合作", "寻求合作",
    # 情绪标题党
    "不转不是", "震惊了", "万万没想到", "太真实了",
]

def filter_noise(articles: list) -> list:
    """
    过滤标题含垃圾词的广告/软文/标题党文章。
    对技术/财经类内容误杀率极低（关键词均为营销专用词）。
    """
    return [
        art for art in articles
        if not any(kw in art.get("title", "") for kw in SPAM_KEYWORDS)
    ]


# ──────────────────────────────────────────────
# 工具函数：日期解析 & 过滤
# ──────────────────────────────────────────────
def parse_date(date_str: str):
    """尝试解析日期字符串，支持 'YYYY-MM-DD HH:MM' 和 'YYYY-MM-DD' 两种格式。"""
    if not date_str:
        return None
    try:
        return datetime.datetime.strptime(date_str[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(date_str[:10], "%Y-%m-%d")
    except ValueError:
        pass
    return None


def filter_by_days(articles: list, days: int) -> list:
    """
    过滤超出时间窗口的文章。
    - days <= 0：不过滤，原样返回
    - 日期缺失或解析失败的文章：保留（不因日期缺失而丢弃）
    """
    if days <= 0:
        return articles
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    result = []
    for art in articles:
        dt = parse_date(art.get("date", ""))
        if dt is None or dt >= cutoff:
            result.append(art)
    return result


# ──────────────────────────────────────────────
# 工具函数：多关键词解析
# ──────────────────────────────────────────────
def parse_keywords(query: str) -> list:
    """
    解析多关键词，支持两种分隔语法：
      - 逗号分隔："昇腾950,昇腾950PR"
      - OR 分隔："DeepSeek OR 大模型"
    单个关键词直接返回含一个元素的列表。
    """
    if " OR " in query:
        keywords = [k.strip() for k in query.split(" OR ") if k.strip()]
    elif "," in query:
        keywords = [k.strip() for k in query.split(",") if k.strip()]
    else:
        keywords = [query.strip()]
    return keywords


# ──────────────────────────────────────────────
# Feature 2：AI 摘要（Kimi Coding API，Anthropic 格式）
# ──────────────────────────────────────────────
def summarize_results(articles: list, query: str, days: int = 0) -> str:
    """
    调用 Kimi Coding API 对搜索结果生成中文简报。
    - 接口格式：Anthropic Messages API（x-api-key + /v1/messages）
    - Base URL：KIMI_BASE_URL（默认 https://api.kimi.com/coding）
    - 需要 KIMI_API_KEY（从 .env 或系统环境变量读取）
    - 任何异常均静默处理，返回空字符串（不影响主搜索流程）
    """
    api_key = ENV.get("KIMI_API_KEY", "") or os.environ.get("KIMI_API_KEY", "")
    if not api_key:
        return ""

    # 构建文章列表文本（最多取前 10 篇，每篇标题 + 摘要 150 字）
    articles_text = ""
    for i, art in enumerate(articles[:10], 1):
        account = art.get("account", "") or "未知公众号"
        title = art.get("title", "")
        abstract = art.get("abstract", "")[:150]
        date = art.get("date", "")[:10]
        articles_text += f"{i}. 【{account}】{title}"
        if date:
            articles_text += f"（{date}）"
        articles_text += "\n"
        if abstract:
            articles_text += f"   {abstract}\n"
        articles_text += "\n"

    time_desc = f"过去 {days} 天内" if days > 0 else "最新"
    prompt = (
        f"以下是关于「{query}」的{time_desc}微信公众号文章（共 {len(articles)} 篇），"
        f"请用 3-5 句话生成一份中文简报，概括主要话题和热点趋势，语言简洁专业：\n\n"
        f"{articles_text}\n简报："
    )

    base_url = ENV.get("KIMI_BASE_URL", "https://api.kimi.com/coding")
    model = ENV.get("KIMI_MODEL", "claude-3-5-haiku-20241022")

    try:
        resp = requests.post(
            f"{base_url.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 600,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        data = resp.json()
        # Anthropic 格式响应：{"content": [{"type": "text", "text": "..."}]}
        return data["content"][0]["text"].strip()
    except Exception:
        return ""


# ──────────────────────────────────────────────
# Layer 1：Tavily 搜索
# ──────────────────────────────────────────────
def tavily_search(query: str, max_results: int = 10, days: int = 0) -> list:
    """
    通过 Tavily API 搜索微信公众号文章。
    限定域名 mp.weixin.qq.com，结果最新、质量高。
    days > 0 时在 API 层面过滤（最近 N 天），days=0 不限制。
    返回标准化 article 列表。
    """
    if not TAVILY_API_KEY:
        return []

    try:
        from tavily import TavilyClient
    except ImportError:
        return []

    try:
        client = TavilyClient(api_key=TAVILY_API_KEY)
        search_kwargs = dict(
            query=query,
            include_domains=["mp.weixin.qq.com"],
            max_results=min(max_results, 20),
            search_depth="advanced",
        )
        if days > 0:
            search_kwargs["days"] = days  # Tavily 原生支持 days 参数
        resp = client.search(**search_kwargs)
    except Exception:
        return []

    results = []
    for item in resp.get("results", []):
        url = item.get("url", "")
        title = item.get("title", "").strip()
        content = item.get("content", "").strip()
        pub_date = item.get("published_date", "") or ""

        # Tavily 有时返回 URL 或无效内容作为 title，尝试从 content 的 Markdown 标题提取
        BAD_TITLES = {"cover_image", "cover image", "image"}
        if not title or title.startswith("http") or title == url or title.lower() in BAD_TITLES:
            md_heading = re.search(r"^#+\s+(.+)$", content, re.MULTILINE)
            if md_heading:
                title = md_heading.group(1).strip()[:100]
            else:
                for line in content.split("\n"):
                    line = line.strip(" #\r\n")
                    if line and not line.startswith("http") and line.lower() not in BAD_TITLES:
                        title = line[:100]
                        break
        if not title or title.lower() in BAD_TITLES:
            continue

        # Tavily 有时会把站点名放在 title 后面，如 "标题 - 公众号名"
        account = ""
        if " - " in title:
            parts = title.rsplit(" - ", 1)
            title = parts[0].strip()
            account = parts[1].strip()

        # 过滤微信内容被屏蔽的无效结果
        BAD_CONTENT = ("no information is available", "learn why", "此内容无法显示")
        if any(b in content.lower() for b in BAD_CONTENT):
            continue

        if url:
            results.append({
                "title": title,
                "account": account,
                "date": pub_date[:10] if pub_date else "",
                "abstract": content[:200],
                "link": url,
                "cover": "",
                "source": "tavily",
            })

    return results


# ──────────────────────────────────────────────
# Layer 2：搜狗微信爬虫
# ──────────────────────────────────────────────
SOGOU_URL = "https://weixin.sogou.com/weixin"

SOGOU_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://weixin.sogou.com/",
}


def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(SOGOU_HEADERS)
    return s


def fetch_sogou_page(session: requests.Session, query: str, page: int = 1):
    """拉取搜狗微信文章搜索结果页"""
    params = {"type": 2, "query": query, "page": page, "ie": "utf8"}
    try:
        resp = session.get(SOGOU_URL, params=params, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        if "验证码" in resp.text or "captcha" in resp.url:
            return None
        return resp.text
    except requests.RequestException:
        return None


def parse_sogou_articles(html: str) -> list:
    """解析搜狗微信文章列表 HTML"""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    for item in soup.select("ul.news-list > li"):
        try:
            title_tag = item.select_one("h3 > a") or item.select_one(".txt-box h3 a")
            title = title_tag.get_text(strip=True) if title_tag else ""
            link = title_tag["href"] if title_tag and title_tag.has_attr("href") else ""
            if link and not link.startswith("http"):
                link = "https://weixin.sogou.com" + link

            abstract_tag = item.select_one("p.txt-info") or item.select_one(".txt-box p")
            abstract = abstract_tag.get_text(strip=True) if abstract_tag else ""

            account_tag = (
                item.select_one("span.all-time-y2")
                or item.select_one("a.account")
                or item.select_one(".account")
            )
            account = account_tag.get_text(strip=True) if account_tag else ""

            date_str = ""
            script = item.find("script")
            if script:
                ts_match = re.search(r"timeConvert\('(\d+)'\)", script.string or "")
                if ts_match:
                    ts = int(ts_match.group(1))
                    date_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
            if not date_str:
                date_tag = item.select_one(".s2")
                date_str = date_tag.get_text(strip=True) if date_tag else ""

            img_tag = item.select_one("img")
            img_url = img_tag.get("src", "") if img_tag else ""

            if title:
                results.append({
                    "title": title,
                    "account": account,
                    "date": date_str,
                    "abstract": abstract[:200],
                    "link": link,
                    "cover": img_url,
                    "source": "sogou",
                })
        except Exception:
            continue

    return results


def sogou_search(query: str, pages: int = 1) -> tuple:
    """
    搜狗微信搜索，返回 (文章列表, 是否触发验证码)。
    """
    session = get_session()
    all_articles = []

    for page in range(1, pages + 1):
        html = fetch_sogou_page(session, query, page)
        if html is None:
            return all_articles, True   # 触发验证码
        all_articles.extend(parse_sogou_articles(html))
        if page < pages:
            time.sleep(random.uniform(1.5, 3.0))

    return all_articles, False


# ──────────────────────────────────────────────
# 公众号聚合
# ──────────────────────────────────────────────
def aggregate_accounts(articles: list) -> list:
    """从文章列表中按公众号名聚合（搜狗有账号名，Tavily 部分有）"""
    accounts: dict = {}
    for art in articles:
        name = art.get("account", "").strip()
        if not name:
            continue
        if name not in accounts:
            accounts[name] = {
                "name": name,
                "article_count": 0,
                "recent_articles": [],
                "sogou_search_link": f"https://weixin.sogou.com/weixin?type=2&query={name}",
            }
        acc = accounts[name]
        acc["article_count"] += 1
        if len(acc["recent_articles"]) < 3:
            acc["recent_articles"].append({
                "title": art.get("title", ""),
                "date": art.get("date", ""),
                "link": art.get("link", ""),
                "source": art.get("source", ""),
            })

    return sorted(accounts.values(), key=lambda x: x["article_count"], reverse=True)


# ──────────────────────────────────────────────
# 主逻辑：双引擎调度
# ──────────────────────────────────────────────
def search(
    query: str,
    mode: str = "article",
    source: str = "auto",
    max_results: int = 10,
    pages: int = 1,
    days: int = 0,
    noise_filter: bool = True,
) -> dict:
    """
    双引擎搜索：
      source="auto"   → 优先 Tavily，不足时补充搜狗
      source="tavily" → 仅 Tavily
      source="sogou"  → 仅搜狗
    days > 0 时过滤最近 N 天内的文章（Tavily 在 API 层过滤，搜狗在结果层过滤）
    noise_filter=True 时过滤广告/软文（默认开启）
    """
    has_tavily = bool(TAVILY_API_KEY)
    articles: list = []
    captcha_hit = False
    used_sources: list = []

    # ── Tavily ──
    if source in ("auto", "tavily") and has_tavily:
        tv_results = tavily_search(query, max_results=max_results, days=days)
        if tv_results:
            articles.extend(tv_results)
            used_sources.append("tavily")

    # ── 搜狗（auto 时：Tavily 结果不足 5 条则补充；sogou 时：直接抓）──
    need_sogou = (
        source == "sogou"
        or (source == "auto" and len(articles) < 5)
        or (source == "auto" and not has_tavily)
    )

    if need_sogou:
        sogou_pages = pages if source == "sogou" else max(pages, 1)
        sogou_arts, captcha_hit = sogou_search(query, pages=sogou_pages)

        if captcha_hit and not articles:
            return {
                "success": False,
                "error": "搜狗触发验证码，Tavily 也未返回结果。请稍后重试或检查 TAVILY_API_KEY。",
                "query": query,
                "results": [],
            }

        # 搜狗结果按 days 过滤
        if days > 0:
            sogou_arts = filter_by_days(sogou_arts, days)

        # 去重：以 title 为 key，Tavily 结果保留（已在前）
        existing_titles = {a["title"] for a in articles}
        for art in sogou_arts:
            if art["title"] not in existing_titles:
                articles.append(art)
                existing_titles.add(art["title"])

        if sogou_arts:
            used_sources.append("sogou")

    # ── Feature 8：去噪 ──
    if noise_filter:
        before = len(articles)
        articles = filter_noise(articles)
        filtered_count = before - len(articles)
    else:
        filtered_count = 0

    # 截断到 max_results
    articles = articles[:max_results]

    if mode == "article":
        result = {
            "success": True,
            "query": query,
            "mode": "article",
            "days_filter": days if days > 0 else None,
            "noise_filtered": filtered_count,
            "sources_used": used_sources,
            "tavily_available": has_tavily,
            "total_found": len(articles),
            "results": articles,
        }
        return result
    else:
        # account 模式需要更多文章，补充搜狗数据
        if mode == "account" and source == "auto":
            extra_arts, _ = sogou_search(query, pages=max(pages, 2))
            if days > 0:
                extra_arts = filter_by_days(extra_arts, days)
            if noise_filter:
                extra_arts = filter_noise(extra_arts)
            existing_titles = {a["title"] for a in articles}
            for art in extra_arts:
                if art["title"] not in existing_titles:
                    articles.append(art)
                    existing_titles.add(art["title"])
            if extra_arts and "sogou" not in used_sources:
                used_sources.append("sogou")

        accounts = aggregate_accounts(articles)
        return {
            "success": True,
            "query": query,
            "mode": "account",
            "days_filter": days if days > 0 else None,
            "noise_filtered": filtered_count,
            "sources_used": used_sources,
            "tavily_available": has_tavily,
            "note": "公众号由文章搜索结果聚合（搜狗有账号名；Tavily 部分结果含账号名）",
            "total_found": len(accounts),
            "results": accounts,
        }


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="微信公众号双引擎搜索（Tavily + 搜狗）")
    parser.add_argument(
        "--query", "-q", required=True,
        help="搜索关键词。支持多关键词：逗号分隔 '关键词1,关键词2' 或 OR 语法 '关键词1 OR 关键词2'",
    )
    parser.add_argument(
        "--type", "-t", dest="mode",
        choices=["article", "account"], default="article",
        help="搜索模式：article=文章(默认) / account=公众号",
    )
    parser.add_argument(
        "--source", "-s",
        choices=["auto", "tavily", "sogou"], default="auto",
        help="搜索引擎：auto=自动(默认) / tavily=仅Tavily / sogou=仅搜狗",
    )
    parser.add_argument("--max-results", "-n", type=int, default=10,
                        help="最大返回条数（默认10，最多20）")
    parser.add_argument("--pages", "-p", type=int, default=1,
                        help="搜狗抓取页数（默认1页≈10条，仅 source=sogou 时生效）")
    parser.add_argument(
        "--days", "-d", type=int, default=0,
        help="只返回最近 N 天内的文章（默认0=不限制）。Tavily 在 API 层过滤，搜狗在结果层过滤。",
    )
    parser.add_argument(
        "--no-filter", action="store_true", default=False,
        help="关闭广告/软文去噪过滤（默认开启）",
    )
    parser.add_argument(
        "--summary", action="store_true", default=False,
        help="用 Claude API 生成搜索结果中文简报（需要 ANTHROPIC_API_KEY）",
    )
    args = parser.parse_args()

    # ── 多关键词处理 ──
    keywords = parse_keywords(args.query)
    max_results = min(args.max_results, 20)
    noise_filter = not args.no_filter

    if len(keywords) == 1:
        result = search(
            query=keywords[0],
            mode=args.mode,
            source=args.source,
            max_results=max_results,
            pages=args.pages,
            days=args.days,
            noise_filter=noise_filter,
        )
    else:
        # 多关键词：各自搜索后合并去重
        all_articles = []
        seen_titles: set = set()
        all_sources: set = set()
        any_success = False
        total_filtered = 0

        for kw in keywords:
            sub = search(
                query=kw,
                mode=args.mode,
                source=args.source,
                max_results=max_results,
                pages=args.pages,
                days=args.days,
                noise_filter=noise_filter,
            )
            if sub.get("success"):
                any_success = True
                total_filtered += sub.get("noise_filtered", 0)
                for art in sub.get("results", []):
                    t = art.get("title", "")
                    if t and t not in seen_titles:
                        all_articles.append(art)
                        seen_titles.add(t)
                for s in sub.get("sources_used", []):
                    all_sources.add(s)
            if kw != keywords[-1]:
                time.sleep(random.uniform(1.0, 2.0))

        all_articles = all_articles[:max_results]

        if args.mode == "article":
            result = {
                "success": any_success,
                "query": args.query,
                "keywords": keywords,
                "mode": "article",
                "days_filter": args.days if args.days > 0 else None,
                "noise_filtered": total_filtered,
                "sources_used": list(all_sources),
                "tavily_available": bool(TAVILY_API_KEY),
                "total_found": len(all_articles),
                "results": all_articles,
            }
        else:
            accounts = aggregate_accounts(all_articles)
            result = {
                "success": any_success,
                "query": args.query,
                "keywords": keywords,
                "mode": "account",
                "days_filter": args.days if args.days > 0 else None,
                "noise_filtered": total_filtered,
                "sources_used": list(all_sources),
                "tavily_available": bool(TAVILY_API_KEY),
                "note": "公众号由文章搜索结果聚合",
                "total_found": len(accounts),
                "results": accounts,
            }

    # ── Feature 2：AI 摘要 ──
    if args.summary and result.get("success") and result.get("results"):
        summary = summarize_results(
            articles=result["results"],
            query=args.query,
            days=args.days,
        )
        if summary:
            result["summary"] = summary

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
