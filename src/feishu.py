"""飞书自定义机器人：发富文本卡片（schema 2.0）。

按日报结构分块渲染：
  1) 摘要（条数 / 总 views / 总 engagement）
  2) views 前 5 推文
  3) 竞品横向对比表
  4) hailuo vs 竞品共现分析
  5) 话题聚类
  6) 风险监控

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


def render_cooccurrence(rows: list[dict]) -> list[dict]:
    if not rows:
        return [{"tag": "div", "text": {"tag": "lark_md", "content": "_本期没有提到竞品的 hailuo 推文_"}}]
    lines = ["**🤝 用户最常将 hailuo 与这些产品对比**"]
    for r in rows[:6]:
        lines.append(
            f"- **{r['competitor']}**：{r['cooccur_count']} 条 "
            f"({r['cooccur_pct']}% 的 hailuo 推文里出现)，涉及 engagement {_fmt(r['engagement_with_cooccur'])}"
        )
    if len(rows) > 6:
        lines.append(f"\n_共 {len(rows)} 个竞品被对比。_")
    return [{
        "tag": "div",
        "text": {"tag": "lark_md", "content": "\n".join(lines)},
    }, {"tag": "hr"}]


def render_topic_clusters(clusters: list[dict]) -> list[dict]:
    if not clusters:
        return [{"tag": "div", "text": {"tag": "lark_md", "content": "_话题未聚类_"}}]
    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": "**🎯 话题聚类**"}}]
    for c in clusters:
        header = f"**{c['name']}** — {c['count']} 条（{c['pct']}%）"
        exs = []
        for t in c.get("examples", [])[:2]:
            exs.append(
                f"   • [{t['author']}]({t['author_url']}) 👁{_fmt(t['views'])} — "
                f"{_truncate(_safe_md_text(t['text']), 70)} ([原推]({t['url']}))"
            )
        body = header + ("\n" + "\n".join(exs) if exs else "")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": body}})
    elements.append({"tag": "hr"})
    return elements


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
                 "content": "<font color='grey'>判定规则：基于中英双语情感词典 + 否定处理，仅供参考，建议人工复核。</font>"},
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
    body_elements.extend(render_competitor_table(
        report.get("competitor_table", []),
        lookback_hours=lookback_hours,
    ))
    body_elements.extend(render_cooccurrence(report.get("cooccurrence", [])))
    body_elements.extend(render_topic_clusters(report.get("topic_clusters", [])))
    body_elements.extend(render_risks(report.get("risky_tweets", [])))

    # 完整报告链接(部署在 GitHub Pages)
    if full_report_url and total > 0:
        body_elements.append({"tag": "hr"})
        body_elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"📊 **查看完整 {total} 条推文(可搜索/排序/过滤)**\n"
                    f"[👉 打开完整报告]({full_report_url})"
                ),
            },
        })

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
        "cooccurrence": [{"competitor": "Kling", "cooccur_count": 5, "cooccur_pct": 12, "engagement_with_cooccur": 800}],
        "topic_clusters": [{"name": "对比评测", "count": 10, "pct": 23,
                            "examples": [{"tweet_id":"e1","url":"https://x.com/x/status/9","author":"@x","author_url":"https://x.com/x","text":"对比 hailuo 和 kling","created_at":"2026-07-07T00:00:00+00:00","views":5000,"likes":100,"retweets":10,"replies":3,"quotes":0}]}],
        "risky_tweets": [],
    }
    print(json.dumps(build_card(fake_report), ensure_ascii=False, indent=2)[:1200])
