"""临时工具:拉过去 24h 全部 hailuo 推文,输出 markdown 表格到 stdout。
不会推飞书,只读 + 写 stdout。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from src import analyzer
from src.scraper import fetch_for_query_sync

KEYWORDS = [k.strip() for k in os.environ["KEYWORDS"].split(",") if k.strip()]
query = " OR ".join(f'"{k}"' if " " in k else k for k in KEYWORDS)
DB = os.environ.get("X_ACCOUNTS_DB", "accounts.db")

sys.stderr.write(f"关键词: {KEYWORDS}\nquery: {query}\n拉过去 24h,不设条数上限…\n")
sys.stderr.flush()

tweets = fetch_for_query_sync(
    query=query,
    since_hours=24,
    max=-1,
    db_path=DB,
)

# 二次兜底过滤
kw_lower = [k.lower() for k in KEYWORDS]
tweets = [t for t in tweets if any(k in t.text.lower() for k in kw_lower)]
tweets.sort(key=lambda t: t.views, reverse=True)

sys.stderr.write(f"命中 {len(tweets)} 条\n\n")
sys.stderr.flush()

# 顺手把全量写到 cache/,便于 render 脚本读
import json as _json
full_dump = {
    "summary": {
        "total_tweets": len(tweets),
        "total_views": sum(t.views for t in tweets),
        "total_engagement": sum(t.engagement() for t in tweets),
        "authors": len({t.author for t in tweets}),
        "window_hours": 24,
    },
    "top_tweets": [t.to_dict() for t in tweets[:5]],
    "all_tweets": [t.to_dict() for t in tweets],
    "competitor_table": [],
    "related_terms": analyzer.compute_related_terms(tweets, excluded_terms=KEYWORDS),
    "public_opinion": analyzer.compute_public_opinion(tweets),
    "risky_tweets": [],
}
from datetime import datetime as _dt
stamp = _dt.now().strftime("%Y%m%d_%H%M")
dump_path = ROOT / "cache" / f"full_{stamp}.json"
dump_path.write_text(_json.dumps(full_dump, ensure_ascii=False, indent=2), encoding="utf-8")
sys.stderr.write(f"已写 {dump_path}\n")

# 输出 markdown 表格到 stdout
print(f"# Hailuo 推文 · 过去 24h · 共 {len(tweets)} 条")
print()
print("| # | 时间(UTC) | 作者 | Views | ❤️ | 🔁 | 💬 | 🔁引 | 推文摘要 | 链接 |")
print("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
for i, t in enumerate(tweets, 1):
    text = t.text.replace("|", "\\|").replace("\n", " ")[:80]
    ts = t.created_at[:16].replace("T", " ")
    print(
        f"| {i} | {ts} | {t.author} | {t.views:,} | {t.likes} | {t.retweets} | {t.replies} | {t.quotes} "
        f"| {text}… | [开]({t.url}) |"
    )
