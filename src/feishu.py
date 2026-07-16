"""飞书自定义机器人：发富文本卡片（schema 2.0）。

按日报结构分块渲染：
  1) 摘要（条数 / 总 views / 总 engagement）
  2) views 前 5 推文
  3) 所有命中帖子表格链接
  4) Related 高频词云
  5) 舆情正/中/负占比
  6) 热议话题 Top 3
  7) 风险监控

竞品横向对比单独发送一张卡片，避免竞品抓取拖慢主报告。

注意：飞书 schema 2.0 已不再支持 `tag: action` 按钮，这里所有跳转都用
markdown 内嵌链接 `[文本](url)`。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time

import httpx

log = logging.getLogger(__name__)

FEISHU_API = "https://open.feishu.cn/open-apis/bot/v2/hook/{hook}"


def _sign(secret: str, ts: int) -> str:
    string_to_sign = f"{ts}\n{secret}"
    h = hmac.new(string_to_sign.encode("utf-8"), string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(h).decode("utf-8")


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _safe_md_text(value: object) -> str:
    """把外部文本变成不可注入链接/标签的飞书 Markdown 纯文本。"""
    return str(value).translate(str.maketrans({
        "[": "［", "]": "］", "(": "（", ")": "）",
        "<": "＜", ">": "＞", "*": "＊", "_": "＿",
        "`": "｀", "~": "～", "|": "｜", "\\": "＼",
    }))


def _fmt(n: int) -> str:
    if n < 10_000:
        return str(n)
    if n < 1_000_000:
        return f"{n/10000:.1f}万"
    return f"{n/10000:.0f}万"


def send(webhook_url: str, payload: dict, secret: str | None = None, timeout: float = 10.0) -> dict:
    if secret:
        ts = int(time.time())
        payload = {**payload, "timestamp": ts, "sign": _sign(secret, ts)}

    if "/hook/" in webhook_url and not webhook_url.startswith("http"):
        url = FEISHU_API.format(hook=webhook_url.split("/hook/")[-1])
    else:
        url = webhook_url

    r = httpx.post(url, json=payload, timeout=timeout)
    try:
        body = r.json()
    except json.JSONDecodeError:
        body = {"raw": r.text}
    if r.status_code != 200 or body.get("code") not in (0, None):
        log.error("飞书返回异常: status=%s body=%s", r.status_code, body)
        raise RuntimeError(f"feishu error: {body}")
    log.info("飞书推送成功: payload ≈ %dKB", len(json.dumps(payload)) // 1024)
    return body


# === 卡片各段渲染 ========================================================

def render_summary(report: dict) -> list[dict]:
    s = report["summary"]
    window_hours = s.get("window_hours", 24)
    rows = [
        ["指标", "值"],
        ["推文数", f"{s['total_tweets']}"],
        ["作者数", f"{s['authors']}"],
        ["总 Views", _fmt(s["total_views"])],
        ["总 Engagement", _fmt(s["total_engagement"])],
    ]
    table_md = "\n".join([
        f"| {' | '.join(rows[0])} |",
        f"| {' | '.join(['---'] * len(rows[0]))} |",
        *[f"| {' | '.join(r)} |" for r in rows[1:]],
    ])
    return [{
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"**📊 {window_hours}h 摘要**\n" + table_md,
        },
    }, {"tag": "hr"}]


def render_top_tweets(tweets: list[dict], cap: int = 5) -> list[dict]:
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "**🔥 Views Top 5**"}}]
    cap = min(len(tweets), cap)
    if cap == 0:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "_无数据_"}})
        return elements
    for i, t in enumerate(tweets[:cap], 1):
        eng = t.get("likes", 0) + t.get("retweets", 0) + t.get("replies", 0) + t.get("quotes", 0)
        line = (
            f"**{i}. [{t['author']}]({t['author_url']})** · "
            f"👁 **{_fmt(t['views'])}** · engagement **{_fmt(eng)}** "
            f"(❤️{_fmt(t.get('likes', 0))} · 🔁{_fmt(t.get('retweets', 0))} · 💬{_fmt(t.get('replies', 0))} · 🔁引{_fmt(t.get('quotes', 0))})\n"
            f"<font color='grey'>{t['created_at'][:16].replace('T', ' ')} UTC</font>\n"
            f"{_truncate(_safe_md_text(t['text']), 160)}\n"
            f"🔗 [打开推文]({t['url']})"
        )
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": line}})
        elements.append({"tag": "hr"})
    return elements


def render_all_tweets_link(total: int, full_report_url: str | None) -> list[dict]:
    """渲染完整命中帖子表格入口。"""
    if not full_report_url or total <= 0:
        return []
    return [{
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": (
                f"📋 **所有命中帖子表格**\n"
                f"共 **{total}** 条，可搜索 / 排序 / 过滤："
                f"[打开完整表格]({full_report_url})"
            ),
        },
    }, {"tag": "hr"}]


def render_competitor_table(rows: list[dict], lookback_hours: int = 24) -> list[dict]:
    if not rows:
        return [{"tag": "div", "text": {"tag": "lark_md", "content": "_竞品对比数据暂缺_"}}]
    lines = [
        f"**🥊 竞品横向对比（同期 {lookback_hours}h）**",
        "| 竞品 | 推文数 | 总 Views | 总 Engagement |",
        "| --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(f"| **{r['name']}** | {r['count']} | {_fmt(r['views'])} | {_fmt(r['engagement'])} |")
    return [{
        "tag": "div",
        "text": {"tag": "lark_md", "content": "\n".join(lines)},
    }, {"tag": "hr"}]


def render_related_terms(terms: list[dict]) -> list[dict]:
    if not terms:
        return [{
            "tag": "div",
            "text": {"tag": "lark_md", "content": "**☁️ Related 词云**\n_暂无足够词频数据_"},
        }, {"tag": "hr"}]
    tokens = []
    for index, item in enumerate(terms[:20]):
        text = f"{_safe_md_text(item['term'])} ×{item['count']}"
        tokens.append(f"**{text}**" if index < 6 else text)
    return [{
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": "**☁️ Related 词云**\n" + "　·　".join(tokens),
        },
    }, {"tag": "hr"}]


def render_public_opinion(items: list[dict]) -> list[dict]:
    if not items:
        return [{
            "tag": "div",
            "text": {"tag": "lark_md", "content": "**📣 热议话题 Top 3**\n_暂无足够话题数据_"},
        }, {"tag": "hr"}]
    elements = [{
        "tag": "div",
        "text": {"tag": "lark_md", "content": "**📣 热议话题 Top 3**"},
    }]
    for index, item in enumerate(items[:3], 1):
        tweet = item["tweet"]
        keywords = item.get("keywords", [])
        keyword_text = "　·　".join(_safe_md_text(term) for term in keywords[:5])
        keyword_line = f"关键词：{keyword_text}\n" if keyword_text else ""
        engagement = sum(
            tweet.get(key, 0)
            for key in ("likes", "retweets", "replies", "quotes")
        )
        content = (
            f"**{index}. {_safe_md_text(item.get('summary') or item.get('topic', '其他讨论'))}**\n"
            f"讨论 **{item.get('count', 0)}** 条"
            f"（{item.get('pct', 0)}%） · "
            f"总 views **{_fmt(item.get('views', 0))}** · "
            f"总互动 **{_fmt(item.get('engagement', engagement))}**\n"
            f"{keyword_line}"
            f"代表帖：[{tweet['author']}]({tweet['author_url']}) · "
            f"👁 {_fmt(tweet.get('views', 0))} · 互动 {_fmt(engagement)}\n"
            f"{_truncate(_safe_md_text(tweet.get('text', '')), 180)}\n"
            f"🔗 [打开推文]({tweet['url']})"
        )
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": content},
        })
    elements.append({"tag": "hr"})
    return elements


def render_sentiment_overview(overview: dict | None) -> list[dict]:
    if not overview or not overview.get("items"):
        return [{
            "tag": "div",
            "text": {"tag": "lark_md", "content": "**🧭 舆情正负面占比**\n_暂无足够舆情数据_"},
        }, {"tag": "hr"}]

    lines = [
        "**🧭 舆情正负面占比**",
        "| 类型 | 数量 | 占比 | 代表帖 |",
        "| --- | ---: | ---: | --- |",
    ]
    for item in overview.get("items", []):
        examples = item.get("examples", [])
        if examples:
            example = examples[0]
            example_text = (
                f"[{example.get('author', '')}]({example.get('author_url', '')}) · "
                f"👁 {_fmt(example.get('views', 0))} · "
                f"[打开]({example.get('url', '')})"
            )
        else:
            example_text = "-"
        lines.append(
            f"| **{_safe_md_text(item.get('name', item.get('label', '')))}** "
            f"| {item.get('count', 0)} "
            f"| {item.get('pct', 0)}% "
            f"| {example_text} |"
        )

    source = overview.get("source")
    if source == "ai":
        lines.append("\n<font color='grey'>判定方式：AI 逐条判断对 Hailuo / MiniMax Video 的态度。</font>")
    else:
        lines.append("\n<font color='grey'>判定方式：AI 不可用时使用词典兜底，仅供参考。</font>")
    return [{
        "tag": "div",
        "text": {"tag": "lark_md", "content": "\n".join(lines)},
    }, {"tag": "hr"}]


def render_risks(risks: list[dict]) -> list[dict]:
    if not risks:
        return [{
            "tag": "div",
            "text": {"tag": "lark_md",
                     "content": "**🛡 风险监控**\n_本期未检测到明显负面倾向的推文 ✅_"},
        }]
    lines = [f"**🛡 风险监控（情感分析过滤到 {len(risks)} 条）**"]
    for r in risks:
        t = r["tweet"]
        lines.append(
            f"- 情感分 **{r['score']}** · [{t['author']}]({t['author_url']}) 👁{_fmt(t['views'])} "
            f"❤️{_fmt(t['likes'])} 💬{_fmt(t['replies'])}\n"
            f"  _\"{_truncate(_safe_md_text(r.get('reason', '')), 80)}\"_\n"
            f"  🔗 [打开]({t['url']})"
        )
    elements = [{
        "tag": "div",
        "text": {"tag": "lark_md", "content": "\n".join(lines)},
    }, {"tag": "hr"}, {
        "tag": "div",
        "text": {"tag": "lark_md",
                 "content": "<font color='grey'>判定规则：优先由 AI 判断真实产品/品牌风险；AI 不可用时使用词典兜底。</font>"},
    }]
    return elements


def build_card(
    report: dict,
    lookback_hours: int = 24,
    full_report_url: str | None = None,
) -> dict:
    s = report.get("summary", {})
    report_date = str(report.get("meta", {}).get("report_date", ""))
    if len(report_date) == 8 and report_date.isdigit():
        date_text = (
            f"{int(report_date[:4])}.{int(report_date[4:6])}.{int(report_date[6:8])}"
        )
    else:
        date_text = "日期未知"
    title_prefix = f"Hailuo X 日报 · {date_text} · {lookback_hours}h"
    total = s.get("total_tweets", 0)
    if total > 0:
        header_title = (
            f"{title_prefix} · "
            f"{total} 条 · {_fmt(s.get('total_views', 0))} views"
        )
        template = "blue"
    else:
        header_title = f"{title_prefix} · 无数据"
        template = "grey"

    body_elements: list[dict] = []
    warnings = report.get("data_quality", {}).get("warnings", [])
    if warnings:
        warning_lines = ["**⚠️ 数据可能不完整**"]
        warning_lines.extend(f"- {_safe_md_text(item)}" for item in warnings)
        body_elements.extend([{
            "tag": "div",
            "text": {"tag": "lark_md", "content": "\n".join(warning_lines)},
        }, {"tag": "hr"}])
    body_elements.extend(render_summary(report))
    body_elements.extend(render_top_tweets(report.get("top_tweets", [])))
    body_elements.extend(render_all_tweets_link(total, full_report_url))
    body_elements.extend(render_related_terms(report.get("related_terms", [])))
    body_elements.extend(render_sentiment_overview(report.get("sentiment_overview")))
    body_elements.extend(render_public_opinion(report.get("public_opinion", [])))
    body_elements.extend(render_risks(report.get("risky_tweets", [])))

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": header_title},
                "template": template,
            },
            "body": {
                "direction": "vertical",
                "elements": body_elements,
            },
        },
    }


def build_competitor_card(report: dict, lookback_hours: int = 24) -> dict:
    """单独的竞品横向对比卡片。"""
    report_date = str(report.get("meta", {}).get("report_date", ""))
    if len(report_date) == 8 and report_date.isdigit():
        date_text = (
            f"{int(report_date[:4])}.{int(report_date[4:6])}.{int(report_date[6:8])}"
        )
    else:
        date_text = "日期未知"

    rows = report.get("competitor_table", [])
    body_elements: list[dict] = []
    warnings = report.get("data_quality", {}).get("warnings", [])
    if warnings:
        warning_lines = ["**⚠️ 竞品数据可能不完整**"]
        warning_lines.extend(f"- {_safe_md_text(item)}" for item in warnings)
        body_elements.extend([{
            "tag": "div",
            "text": {"tag": "lark_md", "content": "\n".join(warning_lines)},
        }, {"tag": "hr"}])
    body_elements.extend(render_competitor_table(rows, lookback_hours=lookback_hours))

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"Hailuo X 竞品横向对比 · {date_text} · {lookback_hours}h",
                },
                "template": "blue" if rows else "grey",
            },
            "body": {
                "direction": "vertical",
                "elements": body_elements,
            },
        },
    }


def build_error_card(error: str) -> dict:
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": "⚠️ Hailuo X 抓取 / 推送失败"},
                "template": "red",
            },
            "body": {
                "direction": "vertical",
                "elements": [{
                    "tag": "div",
                    "text": {"tag": "lark_md",
                             "content": f"**错误**: {_safe_md_text(error[:500])}"},
                }],
            },
        },
    }


if __name__ == "__main__":
    fake_report = {
        "summary": {"total_tweets": 43, "authors": 38, "total_views": 123_456, "total_engagement": 4_321},
        "top_tweets": [
            {"tweet_id": "1", "url": "https://x.com/a/status/1", "author": "@a",
             "author_url": "https://x.com/a", "text": "demo",
             "created_at": "2026-07-07T01:00:00+00:00",
             "views": 50000, "likes": 1000, "retweets": 30, "replies": 50, "quotes": 5}
        ],
        "competitor_table": [{"name": "Kling", "count": 12, "views": 80000, "engagement": 1500}],
        "related_terms": [{"term": "cinematic", "count": 12}, {"term": "Kling", "count": 8}],
        "public_opinion": [{"topic": "产品体验 / 画面质量", "summary": "画质与生成效果", "count": 12, "pct": 27.9, "views": 50000, "engagement": 1200, "keywords": ["quality", "画质"], "tweet": {"tweet_id":"e1","url":"https://x.com/x/status/9","author":"@x","author_url":"https://x.com/x","text":"hailuo quality is amazing","created_at":"2026-07-07T00:00:00+00:00","views":5000,"likes":100,"retweets":10,"replies":3,"quotes":0}}],
        "risky_tweets": [],
    }
    print(json.dumps(build_card(fake_report), ensure_ascii=False, indent=2)[:1200])
