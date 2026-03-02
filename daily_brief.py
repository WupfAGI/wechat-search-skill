# -*- coding: utf-8 -*-
"""
微信公众号每日简报推送（飞书群机器人）

功能：
  搜索 → 去噪 → AI摘要 → 格式化 → 飞书推送

用法：
  python daily_brief.py --query "AI大模型" --days 1
  python daily_brief.py --query "DeepSeek,Qwen" --days 1 --max-results 10
  python daily_brief.py --query "量化投资" --days 3 --dry-run   # 只打印不推送

Windows 定时任务配置（每天 08:00 自动推送）：
  # 在"任务计划程序"中新建任务：
  # 触发器：每天 08:00
  # 操作 > 程序：python
  # 参数："{脚本路径}\\daily_brief.py" --query "AI大模型,DeepSeek" --days 1
  #
  # 或用 PowerShell 注册（以管理员身份运行）：
  # $action = New-ScheduledTaskAction -Execute "python" `
  #   -Argument '"C:\\...\\daily_brief.py" --query "AI大模型" --days 1'
  # $trigger = New-ScheduledTaskTrigger -Daily -At 08:00
  # Register-ScheduledTask -Action $action -Trigger $trigger -TaskName "WechatDailyBrief"
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import datetime
from pathlib import Path

# ── Windows UTF-8 编码修复 ──
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import requests
except ImportError:
    print("缺少依赖，请运行: pip install requests", file=sys.stderr)
    sys.exit(1)

# ── 从 sogou_search.py 导入核心函数 ──
_skill_dir = Path(__file__).parent
sys.path.insert(0, str(_skill_dir))
try:
    from sogou_search import search, summarize_results, ENV, parse_keywords
except ImportError as e:
    print(f"无法导入 sogou_search：{e}", file=sys.stderr)
    sys.exit(1)


# ──────────────────────────────────────────────
# 飞书机器人推送
# ──────────────────────────────────────────────
def feishu_sign(secret: str, timestamp: str) -> str:
    """生成飞书 Webhook 签名（HMAC-SHA256）"""
    string_to_sign = f"{timestamp}\n{secret}"
    code = hmac.new(
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(code).decode("utf-8")


def build_feishu_payload(
    articles: list,
    query: str,
    summary: str,
    days: int,
    source_tags: list,
) -> dict:
    """
    构建飞书 post 富文本消息体。
    格式：标题行 → AI简报 → 文章列表（每条可点击）
    """
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    time_desc = f"最近 {days} 天" if days > 0 else "最新"
    engines = "+".join(source_tags) if source_tags else "双引擎"

    title = f"📰 微信公众号日报｜{query}｜{today}"

    content_blocks = []

    # ── 元信息行 ──
    content_blocks.append([{
        "tag": "text",
        "text": f"🔍 {time_desc}  ·  {engines}  ·  共 {len(articles)} 篇\n",
    }])

    # ── AI 简报 ──
    if summary:
        content_blocks.append([{
            "tag": "text",
            "text": f"📊 简报：{summary}\n",
        }])
        content_blocks.append([{"tag": "text", "text": "─" * 24}])

    # ── 文章列表 ──
    for i, art in enumerate(articles, 1):
        art_title = art.get("title", "（无标题）")
        art_link = art.get("link", "")
        art_account = art.get("account", "")
        art_date = (art.get("date", "") or "")[:10]
        art_source = art.get("source", "")

        line = [{"tag": "text", "text": f"{i}. "}]
        if art_link:
            line.append({"tag": "a", "text": art_title, "href": art_link})
        else:
            line.append({"tag": "text", "text": art_title})

        meta_parts = []
        if art_account:
            meta_parts.append(art_account)
        if art_date:
            meta_parts.append(art_date)
        if art_source:
            meta_parts.append(f"via {art_source}")
        if meta_parts:
            line.append({"tag": "text", "text": f"  [{' · '.join(meta_parts)}]"})

        content_blocks.append(line)

    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": content_blocks,
                }
            }
        },
    }


def push_to_feishu(
    articles: list,
    query: str,
    summary: str,
    days: int,
    source_tags: list,
    dry_run: bool = False,
) -> bool:
    """
    推送消息到飞书群机器人。
    dry_run=True 时只打印消息体，不实际推送。
    返回是否推送成功。
    """
    webhook_url = ENV.get("FEISHU_WEBHOOK_URL", "")
    secret = ENV.get("FEISHU_SECRET", "")

    payload = build_feishu_payload(articles, query, summary, days, source_tags)

    if not dry_run and secret:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = feishu_sign(secret, timestamp)

    # 打印预览（dry_run 或调试用）
    if dry_run:
        print("\n" + "=" * 50)
        print("【飞书消息预览（dry-run，不推送）】")
        print("=" * 50)
        _print_preview(articles, query, summary, days, source_tags)
        print("=" * 50 + "\n")
        return True

    if not webhook_url:
        print("⚠️  未配置 FEISHU_WEBHOOK_URL，跳过推送。", file=sys.stderr)
        return False

    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        data = resp.json()
        if data.get("code") == 0:
            print("✅ 飞书推送成功")
            return True
        else:
            print(f"❌ 飞书推送失败：{data}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"❌ 飞书推送异常：{e}", file=sys.stderr)
        return False


def _print_preview(articles, query, summary, days, source_tags):
    """控制台预览（dry-run 模式）"""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    time_desc = f"最近 {days} 天" if days > 0 else "最新"
    engines = "+".join(source_tags) if source_tags else "双引擎"

    print(f"📰 微信公众号日报｜{query}｜{today}")
    print(f"🔍 {time_desc}  ·  {engines}  ·  共 {len(articles)} 篇")
    if summary:
        print(f"\n📊 简报：{summary}")
    print("\n" + "─" * 40)
    for i, art in enumerate(articles, 1):
        title = art.get("title", "（无标题）")
        link = art.get("link", "")
        account = art.get("account", "")
        date = (art.get("date", "") or "")[:10]
        source = art.get("source", "")
        meta = " · ".join(filter(None, [account, date, f"via {source}" if source else ""]))
        print(f"{i}. {title}")
        if meta:
            print(f"   [{meta}]")
        if link:
            print(f"   🔗 {link}")
        print()


# ──────────────────────────────────────────────
# 主逻辑
# ──────────────────────────────────────────────
def run_daily_brief(
    query: str,
    days: int = 1,
    max_results: int = 10,
    source: str = "auto",
    dry_run: bool = False,
    no_summary: bool = False,
) -> bool:
    """
    完整流程：搜索 → 去噪 → AI摘要 → 飞书推送。
    返回是否执行成功。
    """
    keywords = parse_keywords(query)
    print(f"🔍 搜索关键词：{keywords}  时间窗口：{days} 天  数量上限：{max_results}")

    # ── 搜索（噪声过滤默认开启）──
    if len(keywords) == 1:
        result = search(
            query=keywords[0],
            mode="article",
            source=source,
            max_results=max_results,
            days=days,
            noise_filter=True,
        )
    else:
        import random
        all_articles = []
        seen_titles: set = set()
        all_sources: set = set()
        any_success = False

        for kw in keywords:
            sub = search(
                query=kw,
                mode="article",
                source=source,
                max_results=max_results,
                days=days,
                noise_filter=True,
            )
            if sub.get("success"):
                any_success = True
                for art in sub.get("results", []):
                    t = art.get("title", "")
                    if t and t not in seen_titles:
                        all_articles.append(art)
                        seen_titles.add(t)
                for s in sub.get("sources_used", []):
                    all_sources.add(s)
            if kw != keywords[-1]:
                import time as _time
                _time.sleep(random.uniform(1.0, 2.0))

        result = {
            "success": any_success,
            "results": all_articles[:max_results],
            "sources_used": list(all_sources),
            "noise_filtered": 0,
        }

    if not result.get("success"):
        print(f"❌ 搜索失败：{result.get('error', '未知错误')}", file=sys.stderr)
        return False

    articles = result.get("results", [])
    sources = result.get("sources_used", [])
    noise_count = result.get("noise_filtered", 0)
    print(f"📄 获取文章 {len(articles)} 篇（已过滤垃圾 {noise_count} 篇）")

    if not articles:
        print("⚠️  未找到符合条件的文章，跳过推送。")
        return True

    # ── AI 摘要 ──
    summary = ""
    if not no_summary:
        print("🤖 生成 AI 简报...")
        summary = summarize_results(articles=articles, query=query, days=days)
        if summary:
            print(f"   {summary[:80]}...")
        else:
            print("   （AI 摘要不可用，继续推送）")

    # ── 飞书推送 ──
    success = push_to_feishu(
        articles=articles,
        query=query,
        summary=summary,
        days=days,
        source_tags=sources,
        dry_run=dry_run,
    )
    return success


# ──────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="微信公众号每日简报 → 飞书推送",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python daily_brief.py --query "AI大模型" --days 1
  python daily_brief.py --query "DeepSeek,Qwen" --days 1 --dry-run
  python daily_brief.py --query "量化投资" --days 7 --max-results 15

定时任务（Windows 任务计划程序）见脚本顶部注释。
        """,
    )
    parser.add_argument("--query", "-q", required=True,
                        help="搜索关键词（支持逗号或 OR 多关键词）")
    parser.add_argument("--days", "-d", type=int, default=1,
                        help="时间窗口（默认1天，即过去24小时）")
    parser.add_argument("--max-results", "-n", type=int, default=10,
                        help="最大文章数（默认10）")
    parser.add_argument("--source", "-s",
                        choices=["auto", "tavily", "sogou"], default="auto",
                        help="搜索引擎（默认auto）")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="只预览消息，不推送到飞书")
    parser.add_argument("--no-summary", action="store_true", default=False,
                        help="跳过 AI 摘要生成")
    args = parser.parse_args()

    success = run_daily_brief(
        query=args.query,
        days=args.days,
        max_results=min(args.max_results, 20),
        source=args.source,
        dry_run=args.dry_run,
        no_summary=args.no_summary,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
