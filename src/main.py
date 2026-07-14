"""主入口：两种模式

1) python -m src.main --once    抓一次就退出（建议配合 macOS launchd / cron）
2) python -m src.main           APScheduler 常驻，每天 PUSH_HOUR:PUSH_MINUTE 触发

完整流程：
  1. hailuo 关键词主搜索
  2. 每个竞品关键词并行搜索（同期 24h）
  3. 对 hailuo Top-N 推文拉评论（共现分析用）
  4. analyzer 聚合：摘要 + 竞品表 + 共现 + 话题 + 风险
  5. 发到飞书
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

from . import analyzer, feishu
from .scraper import fetch_for_query_sync, fetch_replies_sync

load_dotenv()

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("main")


def _config() -> dict:
    return {
        "feishu_webhook": os.environ["FEISHU_WEBHOOK_URL"],
        "feishu_secret": os.environ.get("FEISHU_SECRET") or None,
        "keywords": [k.strip() for k in os.environ["KEYWORDS"].split(",") if k.strip()],
        "competitors": [k.strip() for k in os.environ.get(
            "COMPETITOR_KEYWORDS",
            "Seedance,Dreamina,Kling,Vidu,Pika,Runway,Happy Horse,Higgsfield"
        ).split(",") if k.strip()],
        "tz": os.environ.get("TZ", "Asia/Shanghai"),
        "push_hour": int(os.environ.get("PUSH_HOUR", 19)),
        "push_minute": int(os.environ.get("PUSH_MINUTE", 0)),
        "accounts_db": os.environ.get("X_ACCOUNTS_DB", "accounts.db"),
        "lookback_hours": int(os.environ.get("LOOKBACK_HOURS", 24)),
        "max_per_query": int(os.environ.get("MAX_PER_QUERY", 150)),
        "reply_limit_per_top": int(os.environ.get("REPLY_LIMIT_PER_TOP", 10)),
        "top_n_for_cooccur": int(os.environ.get("TOP_N_FOR_COOCUR", 5)),
    }


def _q(name: str) -> str:
    return f'"{name}"' if " " in name else name


def run_once(cfg: dict) -> None:
    log.info("==== 单次任务开始 ====")
    t0 = time.time()
    try:
        hailuo_query = " OR ".join(_q(k) for k in cfg["keywords"])
        log.info("step1: 抓 hailuo 关键词")
        hailuo_tweets = fetch_for_query_sync(
            query=hailuo_query,
            since_hours=cfg["lookback_hours"],
            max=cfg["max_per_query"],
            db_path=cfg["accounts_db"],
        )
        kw_lower = [k.lower() for k in cfg["keywords"]]
        hailuo_tweets = [t for t in hailuo_tweets
                         if any(k in t.text.lower() for k in kw_lower)]

        log.info("step2: 抓竞品（同期 %dh）— %s", cfg["lookback_hours"], cfg["competitors"])
        competitor_results: dict = {}
        for name in cfg["competitors"]:
            try:
                competitor_results[name] = fetch_for_query_sync(
                    query=_q(name),
                    since_hours=cfg["lookback_hours"],
                    max=cfg["max_per_query"],
                    db_path=cfg["accounts_db"],
                )
            except Exception as exc:
                log.warning("竞品 %s 抓取失败: %s", name, exc)
                competitor_results[name] = []

        replies_by_id: dict = {}
        top_for_replies = sorted(hailuo_tweets, key=lambda t: t.views, reverse=True)[:cfg["top_n_for_cooccur"]]
        log.info("step3: 拉 top %d 推文的前 %d 评论", cfg["top_n_for_cooccur"], cfg["reply_limit_per_top"])
        for t in top_for_replies:
            try:
                rpls = fetch_replies_sync(
                    tweet_id=int(t.tweet_id),
                    limit=cfg["reply_limit_per_top"],
                    db_path=cfg["accounts_db"],
                )
                if rpls:
                    replies_by_id[int(t.tweet_id)] = rpls
            except Exception as exc:
                log.warning("评论拉取失败 %s: %s", t.url, exc)

        log.info("step4: 分析聚合")
        top5 = hailuo_tweets[:5]
        report = {
            "summary": analyzer.compute_summary(hailuo_tweets),
            "top_tweets": [t.to_dict() for t in top5],
            "all_tweets": [t.to_dict() for t in hailuo_tweets],  # 全量用于完整报告
            "competitor_table": analyzer.build_competitor_table(competitor_results),
            "cooccurrence": analyzer.compute_cooccurrence(hailuo_tweets, replies_by_id),
            "topic_clusters": analyzer.compute_topic_clusters(hailuo_tweets),
            "risky_tweets": analyzer.compute_risks(hailuo_tweets),
        }

        Path("cache").mkdir(exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d_%H%M')
        report_file = Path("cache") / f"report_{stamp}.json"
        report_file.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 生成完整报告 HTML(给 GitHub Pages 用)
        log.info("step5a: 渲染完整报告 HTML")
        try:
            import subprocess
            subprocess.run(
                [sys.executable, "scripts/render_full_report.py", str(report_file)],
                check=True, cwd=Path.cwd(),
            )
        except Exception as exc:
            log.warning("HTML 渲染失败(不影响飞书推送): %s", exc)

        # GitHub Pages 链接:日期格式 YYYY-MM-DD
        today = datetime.now().strftime('%Y-%m-%d')
        pages_base = os.environ.get(
            "PAGES_BASE_URL",
            "https://Alicia1229.github.io/hailuo-x-bot",
        )
        full_report_url = f"{pages_base}/reports/{today}.html"

        log.info("step5b: 发飞书")
        card = feishu.build_card(report, lookback_hours=cfg["lookback_hours"],
                                full_report_url=full_report_url)
        size_kb = len(json.dumps(card)) // 1024
        if size_kb > 18:
            log.warning("卡片过大 (%dKB)，压缩示例", size_kb)
            for tw in report["top_tweets"]:
                tw["text"] = tw["text"][:120]
            for cl in report["topic_clusters"]:
                cl["examples"] = cl["examples"][:1]
            card = feishu.build_card(report, lookback_hours=cfg["lookback_hours"],
                                    full_report_url=full_report_url)

        feishu.send(cfg["feishu_webhook"], card, secret=cfg["feishu_secret"])
        log.info("==== 完成，耗时 %.1fs；共 %d 条 hailuo 推文；完整报告 %s ====",
                 time.time() - t0, report["summary"]["total_tweets"], full_report_url)

    except Exception:
        log.error("任务失败: %s", traceback.format_exc())
        try:
            err_card = feishu.build_error_card(traceback.format_exc()[-500:])
            feishu.send(cfg["feishu_webhook"], err_card, secret=cfg["feishu_secret"])
        except Exception:
            log.error("失败通知也没发出去: %s", traceback.format_exc())


def run_scheduler(cfg: dict) -> None:
    tz = ZoneInfo(cfg["tz"])
    sched = BlockingScheduler(timezone=tz)
    sched.add_job(
        run_once,
        "cron",
        hour=cfg["push_hour"],
        minute=cfg["push_minute"],
        args=[cfg],
        id="daily_push",
        max_instances=1,
        coalesce=True,
    )
    log.info("调度启动: 每天 %02d:%02d %s 触发",
             cfg["push_hour"], cfg["push_minute"], cfg["tz"])
    sched.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    cfg = _config()

    for k in ["FEISHU_WEBHOOK_URL", "KEYWORDS"]:
        if not os.environ.get(k):
            sys.exit(f"❌ 环境变量 {k} 缺失，先按 .env.example 填好。")

    if args.once:
        run_once(cfg)
    else:
        run_scheduler(cfg)