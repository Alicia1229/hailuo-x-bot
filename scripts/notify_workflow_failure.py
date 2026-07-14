"""GitHub Actions 发布阶段失败时发送通用飞书告警。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src import feishu


def main() -> None:
    webhook = os.environ["FEISHU_WEBHOOK_URL"]
    secret = os.environ.get("FEISHU_SECRET") or None
    card = feishu.build_error_card(
        "GitHub Actions 日报发布流程失败，请到仓库 Actions 页面查看失败步骤。"
    )
    feishu.send(webhook, card, secret=secret)


if __name__ == "__main__":
    main()
