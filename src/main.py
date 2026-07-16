"""Hailuo X 日报入口。"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

from . import analyzer, feishu
from .scraper import fetch_for_query_sync

load_dotenv()

log = logging.getLogger("main")


def _configure_logging() -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[
            RotatingFileHandler(
                log_dir / "bot.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )
    # httpx 的 INFO 日志包含完整请求 URL；飞书 webhook 本身就是凭证。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _config() -> dict:
    return {
        "feishu_webhook": os.environ.get("FEISHU_WEBHOOK_URL"),
        "feishu_secret": os.environ.get("FEISHU_SECRET") or None,
        "keywords": [
            keyword.strip()
            for keyword in os.environ.get("KEYWORDS", "").split(",")
            if keyword.strip()
        ],
        "competitors": [k.strip() for k in os.environ.get(
            "COMPETITOR_KEYWORDS",
            "Seedance,Dreamina,Kling,Vidu,Pika,Runway,Happy Horse,Higgsfield",
        ).split(",") if k.strip()],
        "tz": os.environ.get("TZ", "Asia/Shanghai"),
        "push_hour": int(os.environ.get("PUSH_HOUR", 17)),
        "push_minute": int(os.environ.get("PUSH_MINUTE", 0)),
        "accounts_db": os.environ.get("X_ACCOUNTS_DB", "accounts.db"),
        "lookback_hours": int(os.environ.get("LOOKBACK_HOURS", 24)),
        "max_hailuo_tweets": int(os.environ.get("MAX_HAILUO_TWEETS", -1)),
        "max_per_query": int(os.environ.get("MAX_PER_QUERY", -1)),
        "pages_base_url": os.environ.get(
            "PAGES_BASE_URL",
            "https://Alicia1229.github.io/hailuo-x-bot",
        ),
        "openai_api_key": os.environ.get("OPENAI_API_KEY") or None,
        "openai_base_url": os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        "risk_model": os.environ.get("RISK_MODEL", "gpt-5-mini"),
    }


def _q(name: str) -> str:
    return f'"{name}"' if " " in name else name


def _full_report_url(pages_base: str, report_time: datetime) -> str:
    return f"{pages_base.rstrip('/')}/reports/{report_time:%Y%m%d}.html"


def _previous_calendar_day_window(
    cfg: dict,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """返回昨天自然日窗口：[昨天 00:00, 今天 00:00)。"""
    tz = ZoneInfo(cfg["tz"])
    current = now.astimezone(tz) if now else datetime.now(tz)
    window_end = current.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    window_start = window_end - timedelta(days=1)
    return window_start, window_end


def _add_quality_warning(data_quality: dict, message: str) -> None:
    data_quality["complete"] = False
    data_quality["warnings"].append(message)
    log.warning("数据质量: %s", message)


def _write_report(report: dict) -> Path:
    cache_dir = Path("cache")
    cache_dir.mkdir(exist_ok=True)
    report_file = cache_dir / f"{report['meta']['report_id']}.json"
    report_file.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (cache_dir / "latest_report_path.txt").write_text(
        str(report_file),
        encoding="utf-8",
    )
    return report_file


def _render_report(report_file: Path) -> None:
    subprocess.run(
        [sys.executable, "scripts/render_full_report.py", str(report_file)],
        check=True,
        cwd=Path.cwd(),
    )


def _build_card(report: dict, include_full_report_link: bool = True) -> dict:
    report_copy = copy.deepcopy(report)
    meta = report_copy.get("meta", {})
    card = feishu.build_card(
        report_copy,
        lookback_hours=meta.get("lookback_hours", 24),
        full_report_url=(meta.get("full_report_url") if include_full_report_link else None),
    )
    size_kb = len(json.dumps(card, ensure_ascii=False)) // 1024
    if size_kb > 18:
        log.warning("卡片过大 (%dKB)，压缩示例", size_kb)
        for tweet in report_copy.get("top_tweets", []):
            tweet["text"] = tweet.get("text", "")[:120]
        for item in report_copy.get("public_opinion", []):
            tweet = item.get("tweet", {})
            tweet["text"] = tweet.get("text", "")[:120]
        card = feishu.build_card(
            report_copy,
            lookback_hours=meta.get("lookback_hours", 24),
            full_report_url=(meta.get("full_report_url") if include_full_report_link else None),
        )
    return card


def _build_competitor_card(report: dict) -> dict:
    meta = report.get("meta", {})
    return feishu.build_competitor_card(
        report,
        lookback_hours=meta.get("lookback_hours", 24),
    )


def _send_report(
    cfg: dict,
    report: dict,
    include_full_report_link: bool = True,
) -> None:
    if not cfg["feishu_webhook"]:
        raise ValueError("FEISHU_WEBHOOK_URL 未配置")
    feishu.send(
        cfg["feishu_webhook"],
        _build_card(report, include_full_report_link=include_full_report_link),
        secret=cfg["feishu_secret"],
    )


def _send_competitor_report(cfg: dict, report: dict) -> None:
    if not cfg["feishu_webhook"]:
        raise ValueError("FEISHU_WEBHOOK_URL 未配置")
    feishu.send(
        cfg["feishu_webhook"],
        _build_competitor_card(report),
        secret=cfg["feishu_secret"],
    )


def _notify_failure(cfg: dict, error: BaseException) -> None:
    if not cfg["feishu_webhook"]:
        log.warning("未配置 FEISHU_WEBHOOK_URL，跳过失败通知")
        return
    try:
        message = f"{type(error).__name__}: {error}"
        feishu.send(
            cfg["feishu_webhook"],
            feishu.build_error_card(message),
            secret=cfg["feishu_secret"],
        )
    except Exception:
        log.error("失败通知也没发出去: %s", traceback.format_exc())


def _fetch_competitor_results(
    cfg: dict,
    window_hours: int,
    window_end: datetime,
    data_quality: dict,
) -> dict[str, list]:
    log.info("抓竞品（自然日窗口 %dh）— %s", window_hours, cfg["competitors"])
    competitor_results: dict = {}
    for name in cfg["competitors"]:
        try:
            tweets = fetch_for_query_sync(
                query=_q(name),
                since_hours=window_hours,
                max=cfg["max_per_query"],
                db_path=cfg["accounts_db"],
                window_end=window_end,
            )
            competitor_results[name] = tweets
            if cfg["max_per_query"] > 0 and len(tweets) >= cfg["max_per_query"]:
                _add_quality_warning(
                    data_quality,
                    f"竞品 {name} 达到抓取上限 {cfg['max_per_query']}，统计可能被截断",
                )
        except Exception as exc:
            competitor_results[name] = []
            _add_quality_warning(
                data_quality,
                f"竞品 {name} 抓取失败（{type(exc).__name__}）",
            )
    return competitor_results


def run_once(
    cfg: dict,
    send_report: bool = True,
    notify_failure: bool = True,
) -> Path:
    log.info("==== 单次任务开始 ====")
    started_at = time.time()
    try:
        window_start, window_end = _previous_calendar_day_window(cfg)
        report_day = window_start
        window_hours = int((window_end - window_start).total_seconds() // 3600)
        data_quality = {"complete": True, "warnings": []}
        log.info(
            "固定自然日数据窗口: %s ~ %s",
            window_start.isoformat(),
            window_end.isoformat(),
        )

        hailuo_query = " OR ".join(_q(keyword) for keyword in cfg["keywords"])
        log.info("step1: 抓 hailuo 关键词")
        hailuo_tweets = fetch_for_query_sync(
            query=hailuo_query,
            since_hours=window_hours,
            max=cfg["max_hailuo_tweets"],
            db_path=cfg["accounts_db"],
            window_end=window_end,
        )
        keywords_lower = [keyword.lower() for keyword in cfg["keywords"]]
        hailuo_tweets = [
            tweet for tweet in hailuo_tweets
            if any(keyword in tweet.text.lower() for keyword in keywords_lower)
        ]
        if not hailuo_tweets:
            _add_quality_warning(data_quality, "Hailuo 主查询返回 0 条，请复核 X 登录态和搜索可用性")

        log.info("step2: 分析 Hailuo 主报告")
        full_report_url = _full_report_url(cfg["pages_base_url"], report_day)
        generated_at = datetime.now(ZoneInfo(cfg["tz"]))
        report_id = f"report_{report_day:%Y%m%d}_{generated_at:%Y%m%dT%H%M%S}"
        report = {
            "meta": {
                "report_id": report_id,
                "report_date": report_day.strftime("%Y%m%d"),
                "generated_at": generated_at.isoformat(),
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "lookback_hours": window_hours,
                "full_report_url": full_report_url,
            },
            "data_quality": data_quality,
            "summary": analyzer.compute_summary(hailuo_tweets, window_hours),
            "top_tweets": [tweet.to_dict() for tweet in hailuo_tweets[:5]],
            "all_tweets": [tweet.to_dict() for tweet in hailuo_tweets],
            "competitor_table": [],
            "related_terms": analyzer.compute_related_terms(
                hailuo_tweets,
                excluded_terms=cfg["keywords"],
            ),
            "public_opinion": analyzer.compute_public_opinion(hailuo_tweets),
            "risky_tweets": analyzer.compute_risks(
                hailuo_tweets,
                openai_api_key=cfg["openai_api_key"],
                openai_base_url=cfg["openai_base_url"],
                model=cfg["risk_model"],
            ),
        }

        report_file = _write_report(report)
        log.info("step3: 渲染完整报告 HTML")
        _render_report(report_file)

        if send_report:
            log.info("step4: 发 Hailuo 主卡片")
            # 本地/launchd 运行只生成 HTML，并不会自动发布到 GitHub Pages。
            _send_report(cfg, report, include_full_report_link=False)
            log.info("step5: 抓竞品并单独发卡片")
            competitor_quality = {"complete": True, "warnings": []}
            competitor_results = _fetch_competitor_results(
                cfg,
                window_hours=window_hours,
                window_end=window_end,
                data_quality=competitor_quality,
            )
            competitor_report = {
                "meta": report["meta"],
                "data_quality": competitor_quality,
                "competitor_table": analyzer.build_competitor_table(competitor_results),
            }
            _send_competitor_report(cfg, competitor_report)

        log.info(
            "==== 完成，耗时 %.1fs；共 %d 条 hailuo 推文；完整报告 %s ====",
            time.time() - started_at,
            report["summary"]["total_tweets"],
            full_report_url,
        )
        return report_file
    except Exception as exc:
        log.error("任务失败: %s", traceback.format_exc())
        if notify_failure:
            _notify_failure(cfg, exc)
        raise


def send_saved_report(cfg: dict, report_path: Path) -> None:
    if report_path.suffix == ".txt":
        report_path = Path(report_path.read_text(encoding="utf-8").strip())
    report = json.loads(report_path.read_text(encoding="utf-8"))
    _send_report(cfg, report)
    log.info("已发送已发布报告: %s", report_path)


def send_competitor_report(cfg: dict, report_path: Path) -> None:
    if report_path.suffix == ".txt":
        report_path = Path(report_path.read_text(encoding="utf-8").strip())
    base_report = json.loads(report_path.read_text(encoding="utf-8"))
    meta = base_report.get("meta", {})
    window_end = datetime.fromisoformat(meta["window_end"])
    window_hours = meta.get("lookback_hours", 24)
    data_quality = {"complete": True, "warnings": []}
    competitor_results = _fetch_competitor_results(
        cfg,
        window_hours=window_hours,
        window_end=window_end,
        data_quality=data_quality,
    )
    report = {
        "meta": meta,
        "data_quality": data_quality,
        "competitor_table": analyzer.build_competitor_table(competitor_results),
    }
    _send_competitor_report(cfg, report)
    log.info("已发送竞品横向对比卡片: %s", report_path)


def run_scheduler(cfg: dict) -> None:
    timezone = ZoneInfo(cfg["tz"])
    scheduler = BlockingScheduler(timezone=timezone)
    scheduler.add_job(
        run_once,
        "cron",
        hour=cfg["push_hour"],
        minute=cfg["push_minute"],
        args=[cfg],
        id="daily_push",
        max_instances=1,
        coalesce=True,
    )
    log.info(
        "调度启动: 每天 %02d:%02d %s 触发",
        cfg["push_hour"], cfg["push_minute"], cfg["tz"],
    )
    scheduler.start()


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--send-report", type=Path)
    parser.add_argument("--send-competitor-report", type=Path)
    args = parser.parse_args()

    if args.prepare_only and not args.once:
        parser.error("--prepare-only 必须与 --once 一起使用")
    if args.send_report and (args.once or args.prepare_only or args.send_competitor_report):
        parser.error("--send-report 不能与 --once/--prepare-only/--send-competitor-report 同时使用")
    if args.send_competitor_report and (args.once or args.prepare_only):
        parser.error("--send-competitor-report 不能与 --once/--prepare-only 同时使用")

    required_keys = []
    if args.send_report or args.send_competitor_report:
        required_keys.append("FEISHU_WEBHOOK_URL")
    else:
        required_keys.append("KEYWORDS")
        if not args.prepare_only:
            required_keys.append("FEISHU_WEBHOOK_URL")
    for key in required_keys:
        if not os.environ.get(key):
            sys.exit(f"❌ 环境变量 {key} 缺失，先按 .env.example 填好。")

    cfg = _config()
    if args.send_report:
        send_saved_report(cfg, args.send_report)
    elif args.send_competitor_report:
        send_competitor_report(cfg, args.send_competitor_report)
    elif args.once:
        run_once(
            cfg,
            send_report=not args.prepare_only,
            notify_failure=not args.prepare_only,
        )
    else:
        run_scheduler(cfg)


if __name__ == "__main__":
    main()
