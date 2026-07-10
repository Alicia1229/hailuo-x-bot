"""X (Twitter) 抓取模块：用 twscrape，无需付费 API。

需要先在 accounts.db 里登记至少一个 X 账号（auth_token + ct0 cookie）。
登记方式见 README 中"如何拿到 X 凭证"。
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import twscrape

log = logging.getLogger(__name__)

# twscrape 的 search() 支持 X 原生搜索语法；
# 我们把每个关键词用 OR 包起来，保证"命中任一即收"。
def build_query(keywords: Iterable[str]) -> str:
    parts = [k.strip() for k in keywords if k.strip()]
    if not parts:
        raise ValueError("keywords 不能为空")
    # 用 OR 串起来，加 -filter:replies 可选（这里保留 replies 以便看互动）
    return " OR ".join(f'"{k}"' if " " in k else k for k in parts)


@dataclass
class Tweet:
    tweet_id: str
    url: str
    author: str
    author_url: str
    text: str
    created_at: str  # ISO8601
    views: int       # impression / view_count
    likes: int
    retweets: int
    replies: int
    quotes: int

    def engagement(self) -> int:
        return self.likes + self.retweets + self.replies + self.quotes

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_tweet(t) -> Tweet:
    # twscrape 的 Tweet 模型字段命名稳定
    return Tweet(
        tweet_id=str(t.id),
        url=f"https://x.com/{t.user.username}/status/{t.id}",
        author=f"@{t.user.username}",
        author_url=f"https://x.com/{t.user.username}",
        # 过滤掉过长换行，让卡片更紧凑
        text=re.sub(r"\s+", " ", t.rawContent).strip(),
        created_at=t.date.isoformat(),
        views=getattr(t, "viewCount", 0) or 0,
        likes=getattr(t, "likeCount", 0) or 0,
        retweets=getattr(t, "retweetCount", 0) or 0,
        replies=getattr(t, "replyCount", 0) or 0,
        quotes=getattr(t, "quoteCount", 0) or 0,
    )


async def fetch_for_query(
    query: str,
    since_hours: int = 24,
    max: int = 200,
    db_path: str | Path = "accounts.db",
) -> list[Tweet]:
    """给定任意 X 搜索 query，过去 since_hours 小时内的所有推文。

    时间窗口同时通过 X 服务端的 since_time / until_time 算子过滤，
    保证返回的 max 条一定都在窗口内（Python 端再过一道兜底）。
    """
    api = twscrape.API(db_path)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=since_hours)
    since_ts = int(cutoff.timestamp())
    until_ts = int(now.timestamp())
    # 用括号把调用方的 query 包起来，避免 since_time/until_time 只 OR 进最后一个关键词
    bounded = f"({query}) since_time:{since_ts} until_time:{until_ts}"
    log.info(
        "搜索 query=%r, 时间窗口=%s ~ %s UTC (server-side since_time=%s until_time=%s)",
        query, cutoff.isoformat(), now.isoformat(), since_ts, until_ts,
    )
    # X 限流/网络抖动很常见：3 次重试，指数退避（15s/30s/45s）
    # twscrape 内部已经有账号池轮换，外层这个 retry 是兜底
    attempts = 3
    last_err: Exception | None = None
    for try_n in range(attempts):
        out: list[Tweet] = []
        try:
            async for t in api.search(bounded, limit=max):
                # X 偶尔会回退一两分钟窗口外的，python 再兜底
                if t.date < cutoff:
                    continue
                out.append(_parse_tweet(t))
            out.sort(key=lambda x: x.views, reverse=True)
            log.info("query=%r 命中 %d 条", query[:60], len(out))
            return out
        except Exception as exc:
            last_err = exc
            wait = 15 * (try_n + 1)
            log.warning(
                "第 %d/%d 次抓取 %r 失败 (%s: %s)，%ds 后重试",
                try_n + 1, attempts, query[:60], type(exc).__name__, exc, wait,
            )
            if try_n < attempts - 1:
                await asyncio.sleep(wait)
    log.error("query=%r %d 次重试后仍失败: %s", query[:60], attempts, last_err)
    raise last_err


async def fetch_replies_for(
    tweet_id: int,
    limit: int = 10,
    db_path: str | Path = "accounts.db",
) -> list[Tweet]:
    """拉一条推文下的回复。用于共现分析（评论区里也会提到竞品）。"""
    api = twscrape.API(db_path)
    out: list[Tweet] = []
    try:
        async for t in api.tweet_replies(tweet_id, limit=limit):
            out.append(_parse_tweet(t))
    except Exception as exc:
        log.warning("拉评论失败 tweet_id=%s: %s", tweet_id, exc)
    return out


async def fetch_recent_tweets(
    keywords: list[str],
    since_hours: int = 24,
    max_per_query: int = 200,
    db_path: str | Path = "accounts.db",
) -> list[Tweet]:
    """（保留原接口）抓过去 since_hours 小时内、命中任一关键词的推文，按 views 降序。"""
    query = build_query(keywords)
    results = await fetch_for_query(query, since_hours, max_per_query, db_path)
    # 二次保险：可能 twscrape 内部 parser 漏判，再过一道关键词
    results = [t for t in results if any(kw.lower() in t.text.lower() for kw in keywords)]
    return results


# 给同步入口用的薄包装
def fetch_tweets_sync(*args, **kwargs) -> list[Tweet]:
    return asyncio.run(fetch_recent_tweets(*args, **kwargs))


def fetch_for_query_sync(*args, **kwargs) -> list[Tweet]:
    return asyncio.run(fetch_for_query(*args, **kwargs))


def fetch_replies_sync(*args, **kwargs) -> list[Tweet]:
    return asyncio.run(fetch_replies_for(*args, **kwargs))


if __name__ == "__main__":
    # 手动调试：python -m src.scraper
    import os, sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    kws = os.environ.get("KEYWORDS", "hailuo,hailuo03").split(",")
    db = os.environ.get("X_ACCOUNTS_DB", "accounts.db")
    if not Path(db).exists():
        sys.exit(f"❌ 找不到 {db}。先按 README 步骤注册一个 X 账号。")
    out = fetch_tweets_sync(kws, db_path=db)
    print(f"\n抓到了 {len(out)} 条：")
    for t in out[:5]:
        print(f"- {t.url}  views={t.views}  ❤️{t.likes}  🔁{t.retweets}  💬{t.replies}")
        print(f"  {t.text[:120]}")