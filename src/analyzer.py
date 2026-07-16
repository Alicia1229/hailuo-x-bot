"""分析层：摘要、竞品表、共现分析、话题聚类、风险监控。

输入：src.scraper.Tweet 列表 + （可选）每条推文的评论
输出：纯 dict，方便卡片直接渲染。
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

import httpx

from .scraper import Tweet

log = logging.getLogger(__name__)

# === 竞品词典 ============================================================
# 同一厂商可能有很多别名（大小写、空格、连字符），统一映射到一个 key
COMPETITORS: dict[str, list[str]] = {
    "Seedance":  ["seedance", "seedance 2.0", "see-dance"],
    "Dreamina":  ["dreamina"],
    "Kling":     ["kling", "kling ai"],
    "Vidu":      ["vidu", "vidu ai"],
    "Pika":      ["pika", "pika labs"],
    "Runway":    ["runway", "runwayml"],
    "Happy Horse": ["happy horse", "happyhorse"],
    "Higgsfield": ["higgsfield", "higgs field"],
}

# === 话题聚类 ============================================================
# 主题 -> 触发关键词列表（多语言、不区分大小写、按子串匹配）
TOPIC_KEYWORDS: list[tuple[str, list[str]]] = [
    ("产品体验 / 画面质量",          [
        "画质", "画面", "清晰", "quality", "resolution", "画面质感",
        "色彩", "color", "blur", "清晰度", "render", "渲染", "细节",
    ]),
    ("与竞品对比评测",              [
        "vs", "对比", "比较", "胜出", "better", "worse",
        "领先", "不如", "不如说", "lost to", "beats",
        "对比一下", "比较一下", "instead of",
    ]),
    ("教程 / 提示词技巧",            [
        "教程", "tutorial", "how to", "workflow", "prompt",
        "提示词", "工作流", "tip", "tips", "trick", "设定",
        "配方", "preset", "参数",
    ]),
    ("作品 / 创意分享",              [
        "作品", "作品集", "分享", "made with", "created with",
        "generate", "生成", "我用", "动画", "mv", "music video",
        "我做了", "我生成",
    ]),
    ("商业应用 / 客户案例",          [
        "客户", "客户案例", "case study", "广告", "ad",
        "campaign", "营销", "商业化", "商业应用", "品牌",
        "production", "production ready",
    ]),
    ("价格 / 订阅 / 政策",          [
        "价格", "订阅", "subscribe", "subscription", "plan",
        "credits", "credits", "积分", "收费", "免费", "free",
        "pricing", "tier", "付费", "低价", "便宜", "cheap",
        "affordable", "low price", "cost", "性价比",
    ]),
    ("BUG / 问题反馈 / 抱怨",        [
        "bug", "crash", "卡死", "卡顿", "崩溃", "退款",
        "refund", "broken", "glitch", "fail", "失败", "拒绝",
        "down", "宕机", "service", "服务器",
    ]),
    ("新功能 / 版本更新",            [
        "新功能", "new feature", "release", "更新", "上线",
        "v2", "v3", "发布", "release notes", "changelog",
        "推出了", "刚刚发布",
    ]),
]

# === 简单中英情感词典 ====================================================
POSITIVE = {
    # English
    "amazing": 2, "awesome": 2, "love": 1.5, "great": 1, "beautiful": 1.5,
    "best": 1.5, "fantastic": 2, "impressive": 2, "perfect": 2,
    "good": 0.5, "cool": 0.5, "wow": 1, "❤": 1, "🔥": 1,
    # Chinese
    "棒": 1.5, "赞": 1, "强": 1, "爽": 1, "好": 0.5, "不错": 1,
    "惊艳": 2, "丝滑": 1.5, "喜欢": 1, "真香": 1.5, "强": 1,
    "厉害": 1.5, "惊喜": 1.5, "完美": 2,
}
NEGATIVE = {
    # English
    "bad": -1.5, "worst": -2, "terrible": -2, "awful": -2, "hate": -1.5,
    "broken": -1.5, "bug": -0.5, "crash": -1.5, "fail": -1, "failed": -1.5,
    "refund": -2, "scam": -2.5, "trash": -2, "garbage": -2,
    "disappointed": -1.5, "sucks": -2, "broken": -1.5, "useless": -1.5,
    "expensive": -1, "expensive": -1, "limited": -0.5,
    # Chinese
    "垃圾": -2, "糟糕": -2, "差": -1.5, "烂": -2, "坑": -1.5,
    "退款": -2, "退钱": -2, "卡": -1, "卡顿": -1.5, "崩": -1.5,
    "崩溃": -1.5, "失望": -1.5, "不行": -1, "差劲": -1.5,
    "骗": -2, "辣鸡": -2, "无语": -1, "难用": -1.5,
}
NEGATION = {"not", "no", "never", "没有", "不是", "不会", "别", "没", "无"}
CREATIVE_CONTEXT = {
    "cinematic", "prompt", "scene", "style", "vfx", "shot", "camera",
    "city", "water", "wave", "waves", "car", "chase", "around",
}

RELATED_STOPWORDS = {
    "about", "after", "again", "also", "and", "are", "been", "before",
    "being", "but", "can", "could", "for", "from", "get", "got", "had",
    "has", "have", "her", "here", "him", "his", "how", "into", "its",
    "just", "more", "most", "new", "not", "now", "our", "out", "over",
    "she", "some", "than", "that", "the", "their", "them", "then", "there",
    "these", "they", "this", "through", "too", "use", "used", "using",
    "very", "was", "way", "were", "what", "when", "where", "which", "who",
    "will", "with", "would", "you", "your", "ai", "video", "image", "prompt",
    "generated", "generate", "created", "create", "made", "official", "https",
    "com", "amp", "一个", "这个", "可以", "使用", "进行", "通过", "以及",
    "就是", "还是", "没有", "什么", "非常", "真的", "今天", "现在",
}


def sentiment_score(text: str) -> float:
    """简单的情感打分。返回 [-3, +3] 的浮点数，越正越正面。"""
    text_lower = text.lower()
    tokens = re.findall(r"[\w一-鿿]+|[❤🔥⭐]", text_lower)
    score = 0.0
    for i, tok in enumerate(tokens):
        if tok in NEGATIVE:
            if _ignore_negative_in_context(tok, tokens, i):
                continue
            weight = NEGATIVE[tok]   # 词表里已经是负值，直接取
        elif tok in POSITIVE:
            weight = POSITIVE[tok]
        else:
            continue
        # 否定：如果前 2 个 token 内有 negation，则反号
        window = tokens[max(0, i - 2):i]
        if any(w in NEGATION for w in window):
            weight = -weight * 0.5
        score += weight
    # 归一化：用出现次数做软裁剪
    return max(min(score, 3.0), -3.0)


def _ignore_negative_in_context(tok: str, tokens: list[str], index: int) -> bool:
    """过滤创意 prompt 里的剧情词，避免误当成产品风险。"""
    if tok != "crash":
        return False
    local = set(tokens[max(0, index - 4):index + 5])
    if not local.intersection({"wave", "waves", "water", "car", "chase", "around"}):
        return False
    return bool(set(tokens).intersection(CREATIVE_CONTEXT))


# === 主分析函数 ==========================================================

@dataclass
class Report:
    summary: dict
    top_tweets: list[Tweet]
    competitor_table: list[dict]
    related_terms: list[dict]
    public_opinion: list[dict]
    risky_tweets: list[dict]
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["top_tweets"] = [t.to_dict() if hasattr(t, "to_dict") else t for t in self.top_tweets]
        return d


def compute_summary(tweets: list[Tweet], window_hours: int = 24) -> dict:
    total_views = sum(t.views for t in tweets)
    total_eng = sum(t.engagement() for t in tweets)
    return {
        "total_tweets": len(tweets),
        "total_views": total_views,
        "total_engagement": total_eng,
        "authors": len({t.author for t in tweets}),
        "window_hours": window_hours,
    }


def compute_related_terms(
    tweets: list[Tweet],
    excluded_terms: Iterable[str] = (),
    limit: int = 20,
) -> list[dict]:
    """提取 Related 高频词；count 表示出现该词的推文数，而非原始重复次数。"""
    excluded = set(RELATED_STOPWORDS)
    for term in excluded_terms:
        normalized = term.lower().strip().lstrip("#")
        if normalized:
            excluded.add(normalized)
            excluded.update(normalized.split())

    counter: Counter = Counter()
    display: dict[str, str] = {}
    token_pattern = re.compile(
        r"#[a-zA-Z0-9_\u4e00-\u9fff]+|[a-zA-Z][a-zA-Z0-9_-]{2,}|[\u4e00-\u9fff]{2,6}"
    )
    for tweet in tweets:
        text = re.sub(r"https?://\S+", " ", tweet.text or "")
        text = re.sub(r"@[A-Za-z0-9_]+", " ", text)
        seen: set[str] = set()
        for raw_token in token_pattern.findall(text):
            normalized = raw_token.lower().lstrip("#")
            if normalized in excluded or normalized.isdigit():
                continue
            if len(normalized) < 2 or normalized in seen:
                continue
            seen.add(normalized)
            display.setdefault(normalized, raw_token)
        counter.update(seen)

    return [
        {"term": display[term], "count": count}
        for term, count in counter.most_common(limit)
    ]


def compute_public_opinion(tweets: list[Tweet], limit: int = 3) -> list[dict]:
    """按讨论话题聚合舆情，而不是给代表帖打正/中/负标签。"""
    topic_tweets: dict[str, dict[str, object]] = {}
    for tweet in tweets:
        text_lower = tweet.text.lower()
        for topic_name, keywords in TOPIC_KEYWORDS:
            matched = [
                keyword
                for keyword in keywords
                if keyword.lower() in text_lower
            ]
            if not matched:
                continue
            bucket = topic_tweets.setdefault(
                topic_name,
                {
                    "tweets": [],
                    "terms": Counter(),
                    "tweet_ids": set(),
                },
            )
            tweet_ids = bucket["tweet_ids"]
            if tweet.tweet_id in tweet_ids:
                continue
            bucket["tweets"].append(tweet)
            tweet_ids.add(tweet.tweet_id)
            bucket["terms"].update(matched)

    if not topic_tweets:
        return _fallback_public_opinion(tweets, limit)

    items = []
    for topic_name, bucket in topic_tweets.items():
        topic_items = bucket["tweets"]
        topic_items.sort(key=lambda tweet: (tweet.views, tweet.engagement()), reverse=True)
        terms = [
            term
            for term, _count in bucket["terms"].most_common(5)
        ]
        count = len(topic_items)
        views = sum(tweet.views for tweet in topic_items)
        engagement = sum(tweet.engagement() for tweet in topic_items)
        items.append({
            "topic": topic_name,
            "summary": _summarize_topic(topic_name, terms),
            "count": count,
            "pct": round(count / max(len(tweets), 1) * 100, 1),
            "views": views,
            "engagement": engagement,
            "keywords": terms,
            "tweet": topic_items[0].to_dict(),
        })

    items.sort(
        key=lambda item: (item["count"], item["engagement"], item["views"]),
        reverse=True,
    )
    return items[:limit]


def _summarize_topic(topic_name: str, terms: list[str]) -> str:
    """把内部话题名压成飞书卡片上更像“人在聊什么”的短句。"""
    term_text = " ".join(term.lower() for term in terms)
    if topic_name == "价格 / 订阅 / 政策":
        if any(
            marker in term_text
            for marker in ("免费", "free", "低价", "便宜", "cheap", "affordable", "性价比")
        ):
            return "价格低 / 免费额度"
        return "价格、订阅与额度"
    if topic_name == "产品体验 / 画面质量":
        return "画质与生成效果"
    if topic_name == "与竞品对比评测":
        return "和竞品对比"
    if topic_name == "教程 / 提示词技巧":
        return "教程与提示词玩法"
    if topic_name == "作品 / 创意分享":
        return "作品与创意案例"
    if topic_name == "商业应用 / 客户案例":
        return "商业应用场景"
    if topic_name == "BUG / 问题反馈 / 抱怨":
        return "问题反馈与抱怨"
    if topic_name == "新功能 / 版本更新":
        return "新功能与版本更新"
    return topic_name


def _fallback_public_opinion(tweets: list[Tweet], limit: int) -> list[dict]:
    """话题词典没命中时，退回到高互动代表帖，但不打情感标签。"""
    ranked = sorted(
        tweets,
        key=lambda tweet: (tweet.engagement(), tweet.views),
        reverse=True,
    )
    return [
        {
            "topic": "高互动讨论",
            "summary": "高互动讨论",
            "count": 1,
            "pct": round(1 / max(len(tweets), 1) * 100, 1),
            "views": tweet.views,
            "engagement": tweet.engagement(),
            "keywords": [],
            "tweet": tweet.to_dict(),
        }
        for tweet in ranked[:limit]
    ]


def build_competitor_table(
    competitors_results: dict[str, list[Tweet]],
) -> list[dict]:
    """输入 {"Seedance": [Tweet, ...], ...}，返回行 [{name, count, views, engagement}] 按 count 降序。"""
    rows = []
    for name, tweets in competitors_results.items():
        rows.append({
            "name": name,
            "count": len(tweets),
            "views": sum(t.views for t in tweets),
            "engagement": sum(t.engagement() for t in tweets),
        })
    rows.sort(key=lambda r: r["count"], reverse=True)
    return rows


def compute_cooccurrence(
    tweets: list[Tweet],
    replies_by_id: dict[int, list[Tweet]] | None = None,
) -> list[dict]:
    """统计 hailuo 相关推文里有多少条同时提到某竞品（推文正文 + 评论）。"""
    counter: Counter = Counter()
    engagements: dict[str, int] = defaultdict(int)

    def doc_text(t: Tweet) -> str:
        return (t.text or "").lower()

    for t in tweets:
        full_text = doc_text(t)
        if replies_by_id and int(t.tweet_id) in replies_by_id:
            full_text += " " + " ".join(doc_text(r) for r in replies_by_id[int(t.tweet_id)])

        matched = set()
        for name, aliases in COMPETITORS.items():
            if any(a in full_text for a in aliases):
                matched.add(name)
        for name in matched:
            counter[name] += 1
            engagements[name] += t.engagement()

    rows = []
    for name, cnt in counter.most_common():
        rows.append({
            "competitor": name,
            "cooccur_count": cnt,
            "cooccur_pct": round(cnt / max(len(tweets), 1) * 100, 1),
            "engagement_with_cooccur": engagements[name],
        })
    return rows


def compute_topic_clusters(
    tweets: list[Tweet],
    top_per_topic: int = 2,
) -> list[dict]:
    """基于关键词简单匹配的硬聚类。一条推文可以同时归多个主题。"""
    counts: Counter = Counter()
    examples: dict[str, list[Tweet]] = defaultdict(list)
    for t in tweets:
        text_lower = t.text.lower()
        for topic_name, kws in TOPIC_KEYWORDS:
            if any(kw.lower() in text_lower for kw in kws):
                counts[topic_name] += 1
                examples[topic_name].append(t)

    clusters = []
    # 取出 top-5（按 count 降序）
    for name, cnt in counts.most_common(5):
        # 选 views 最高的两条作为示例
        exemplar = sorted(examples[name], key=lambda t: t.views, reverse=True)[:top_per_topic]
        clusters.append({
            "name": name,
            "count": cnt,
            "pct": round(cnt / max(len(tweets), 1) * 100, 1),
            "examples": [t.to_dict() for t in exemplar],
        })
    return clusters


def compute_risks(
    tweets: list[Tweet],
    threshold: float = -0.5,
    openai_api_key: str | None = None,
    model: str = "gpt-5-mini",
) -> list[dict]:
    """风险监控：优先用 AI 判定，未配置或失败时退回词典规则。"""
    if openai_api_key:
        try:
            return compute_risks_ai(tweets, openai_api_key=openai_api_key, model=model)
        except Exception as exc:
            log.warning("AI 风险判定失败，退回词典规则: %s", exc)
    return compute_risks_by_dictionary(tweets, threshold=threshold)


def compute_risks_by_dictionary(tweets: list[Tweet], threshold: float = -0.5) -> list[dict]:
    """词典兜底：情感分 < threshold 的推文列为风险。"""
    flagged = []
    for t in tweets:
        s = sentiment_score(t.text)
        if s <= threshold:
            flagged.append({
                "tweet": t.to_dict(),
                "score": round(s, 2),
                "reason": _extract_reason(t.text),
            })
    # 按情感分升序（最负面优先）
    flagged.sort(key=lambda x: x["score"])
    return flagged[:10]


def compute_risks_ai(
    tweets: list[Tweet],
    openai_api_key: str,
    model: str = "gpt-5-mini",
    batch_size: int = 15,
) -> list[dict]:
    """用 OpenAI 模型判断真正的产品/品牌风险，避免剧情词误报。"""
    if not tweets:
        return []
    by_id = {tweet.tweet_id: tweet for tweet in tweets}
    flagged: list[dict] = []
    for start in range(0, len(tweets), batch_size):
        batch = tweets[start:start + batch_size]
        payload = _build_risk_payload(batch)
        response = _call_openai_risk_judge(
            openai_api_key=openai_api_key,
            model=model,
            payload=payload,
        )
        for item in response.get("items", []):
            tweet_id = str(item.get("tweet_id", ""))
            tweet = by_id.get(tweet_id)
            if tweet is None or not item.get("is_risk"):
                continue
            severity = _clamp_int(item.get("severity", 1), 1, 3)
            reason = str(item.get("reason") or item.get("evidence") or tweet.text[:80])
            flagged.append({
                "tweet": tweet.to_dict(),
                "score": -severity,
                "reason": reason[:160],
                "risk_type": str(item.get("risk_type") or "其他风险"),
                "judge": "ai",
            })
    flagged.sort(key=lambda item: (item["score"], -item["tweet"].get("views", 0)))
    return flagged[:10]


def _build_risk_payload(tweets: list[Tweet]) -> list[dict]:
    return [{
        "tweet_id": tweet.tweet_id,
        "author": tweet.author,
        "views": tweet.views,
        "text": tweet.text[:1200],
    } for tweet in tweets]


def _call_openai_risk_judge(
    openai_api_key: str,
    model: str,
    payload: list[dict],
) -> dict:
    system_prompt = (
        "你是 Hailuo / MiniMax Video 的品牌舆情风险分析员。"
        "只判断推文本身是否对 Hailuo、MiniMax、Hailuo AI、MiniMax Video 构成真实舆情风险。"
        "风险包括：产品 bug、崩溃、服务不可用、生成质量差、退款/诈骗、价格/额度抱怨、"
        "强烈负面竞品比较、公司/融资/股价/监管/商业化负面新闻。"
        "不要把影视/游戏/创意 prompt 里的剧情词当风险，例如 crash、chaos、dead、infected、"
        "battle、disaster、dark、broken wall、waves crash 等只描述画面的词。"
        "正面作品分享、教程、提示词、普通竞品并列、剧情描述都不是风险。"
        "只输出 JSON，不要输出解释。"
    )
    user_prompt = (
        "请逐条判断，返回格式："
        '{"items":[{"tweet_id":"...","is_risk":true/false,'
        '"severity":1-3,"risk_type":"...","reason":"中文短理由，<=60字","evidence":"原文依据，<=80字"}]}'
        "\n推文：\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    with httpx.Client(timeout=60) as client:
        response = client.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "input": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_output_tokens": 2000,
            },
        )
    response.raise_for_status()
    return _parse_openai_json(response.json())


def _parse_openai_json(body: dict) -> dict:
    text = body.get("output_text")
    if not text:
        chunks = []
        for output in body.get("output", []):
            for content in output.get("content", []):
                if content.get("type") in ("output_text", "text"):
                    chunks.append(content.get("text", ""))
        text = "\n".join(chunks)
    if not text:
        raise ValueError("OpenAI response missing output text")
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("OpenAI response is not JSON")
    return json.loads(match.group(0))


def _clamp_int(value: object, low: int, high: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = low
    return max(low, min(high, n))


def _extract_reason(text: str) -> str:
    """从推文中捞一段含情感词的短句作为风险点。"""
    # 简单：找情感词前后 20 字符
    for word in (NEGATIVE.keys() | POSITIVE.keys()):
        idx = text.lower().find(word.lower())
        if idx >= 0:
            start = max(0, idx - 20)
            end = min(len(text), idx + len(word) + 30)
            return text[start:end].strip()
    return text[:60]


# === 单测入口 ============================================================
if __name__ == "__main__":
    samples = [
        Tweet("1", "u", "@a", "u", "hailuo is amazing and the quality is great",
              "2026-07-07T00:00:00Z", 1000, 50, 10, 5, 1),
        Tweet("2", "u", "@b", "u", "compared to kling, hailuo is worse, lots of bug",
              "2026-07-07T00:00:00Z", 800, 30, 5, 3, 0),
        Tweet("3", "u", "@c", "u", "教程贴：如何用 hailuo 制作 mv？",
              "2026-07-07T00:00:00Z", 600, 25, 4, 2, 0),
        Tweet("4", "u", "@d", "u", "hailuo 垃圾，根本不能用，退款！",
              "2026-07-07T00:00:00Z", 500, 20, 3, 10, 0),
    ]
    print("summary:", compute_summary(samples))
    print()
    print("cooccurrence:", compute_cooccurrence(samples))
    print()
    print("topics:", json.dumps([{k: v for k, v in c.items() if k != 'examples'} | {'n_examples': len(c['examples'])} for c in compute_topic_clusters(samples)], ensure_ascii=False, indent=2))
    print()
    print("risks:")
    for r in compute_risks(samples):
        print(f"  score={r['score']}  reason={r['reason']}")
        print(f"    {r['tweet']['url']}")
