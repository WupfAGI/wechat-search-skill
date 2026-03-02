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
# 加载 .env（读取 TAVILY_API_KEY）
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
    # 环境变量优先于文件
    for k in list(env_vars.keys()):
        env_vars[k] = os.environ.get(k, env_vars[k])
    return env_vars

ENV = load_env()
TAVILY_API_KEY = ENV.get("TAVILY_API_KEY", "")

# ──────────────────────────────────────────────
# Layer 1：Tavily 搜索
# ──────────────────────────────────────────────
def tavily_search(query: str, max_results: int = 10) -> list[dict]:
    """
    通过 Tavily API 搜索微信公众号文章。
    限定域名 mp.weixin.qq.com，结果最新、质量高。
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
        resp = client.search(
            query=query,
            include_domains=["mp.weixin.qq.com"],
            max_results=min(max_results, 20),
            search_depth="advanced",   # 更深度的搜索
        )
    except Exception as e:
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
            # 优先从 Markdown heading 提取：# 标题
            md_heading = re.search(r"^#+\s+(.+)$", content, re.MULTILINE)
            if md_heading:
                title = md_heading.group(1).strip()[:100]
            else:
                # 降级：取 content 中第一个非空、非 URL、非 cover 的行
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


def fetch_sogou_page(session: requests.Session, query: str, page: int = 1) -> str | None:
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


def parse_sogou_articles(html: str) -> list[dict]:
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
                item.select_one("span.all-time-y2")  # 搜狗微信文章列表中账号名所在位置
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


def sogou_search(query: str, pages: int = 1) -> tuple[list[dict], bool]:
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
def aggregate_accounts(articles: list[dict]) -> list[dict]:
    """从文章列表中按公众号名聚合（搜狗有账号名，Tavily 部分有）"""
    accounts: dict[str, dict] = {}
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
) -> dict:
    """
    双引擎搜索：
      source="auto"   → 优先 Tavily，不足时补充搜狗
      source="tavily" → 仅 Tavily
      source="sogou"  → 仅搜狗
    """
    has_tavily = bool(TAVILY_API_KEY)
    articles: list[dict] = []
    captcha_hit = False
    used_sources: list[str] = []

    # ── Tavily ──
    if source in ("auto", "tavily") and has_tavily:
        tv_results = tavily_search(query, max_results=max_results)
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

        # 去重：以 title 为 key，Tavily 结果保留（已在前）
        existing_titles = {a["title"] for a in articles}
        for art in sogou_arts:
            if art["title"] not in existing_titles:
                articles.append(art)
                existing_titles.add(art["title"])

        if sogou_arts:
            used_sources.append("sogou")

    # 截断到 max_results
    articles = articles[:max_results]

    if mode == "article":
        return {
            "success": True,
            "query": query,
            "mode": "article",
            "sources_used": used_sources,
            "tavily_available": has_tavily,
            "total_found": len(articles),
            "results": articles,
        }
    else:
        # account 模式：聚合公众号
        # account 模式需要更多文章，补充搜狗数据
        if mode == "account" and source == "auto":
            extra_arts, _ = sogou_search(query, pages=max(pages, 2))
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
    parser.add_argument("--query", "-q", required=True, help="搜索关键词")
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
    args = parser.parse_args()

    result = search(
        query=args.query,
        mode=args.mode,
        source=args.source,
        max_results=min(args.max_results, 20),
        pages=args.pages,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
