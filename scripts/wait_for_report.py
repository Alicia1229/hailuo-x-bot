"""等待 GitHub Pages 上出现本次报告 ID。"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx


def _resolve_report_path(path: Path) -> Path:
    if path.suffix == ".txt":
        return Path(path.read_text(encoding="utf-8").strip())
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report_path", type=Path)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--interval", type=int, default=15)
    args = parser.parse_args()

    report_path = _resolve_report_path(args.report_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    meta = report["meta"]
    report_url = meta["full_report_url"]
    report_id = meta["report_id"]
    deadline = time.monotonic() + args.timeout

    with httpx.Client(timeout=20, follow_redirects=True) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(
                    report_url,
                    headers={"Cache-Control": "no-cache"},
                    params={"report": report_id, "ts": int(time.time())},
                )
                if response.status_code == 200 and report_id in response.text:
                    print(f"✅ GitHub Pages 已发布报告 {report_id}")
                    return
                print(f"等待 Pages: status={response.status_code}")
            except httpx.HTTPError as exc:
                print(f"等待 Pages: {type(exc).__name__}")
            time.sleep(args.interval)

    raise SystemExit(f"❌ GitHub Pages 在 {args.timeout}s 内未发布报告 {report_id}")


if __name__ == "__main__":
    main()
