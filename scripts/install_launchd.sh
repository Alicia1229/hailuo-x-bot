#!/usr/bin/env bash
# 一键安装 launchd 定时任务
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_NAME="com.minimax.hailuo-bot.plist"
SRC="$PROJECT_DIR/scripts/$PLIST_NAME"
DST="$HOME/Library/LaunchAgents/$PLIST_NAME"

echo "▶ 准备安装 launchd 任务"
echo "  PROJECT_DIR = $PROJECT_DIR"
echo "  DST         = $DST"

# 替换 WorkingDirectory
sed "s|/Users/minimax/hailuo-x-bot|$PROJECT_DIR|g" "$SRC" > "$DST"

# 找到 python3
PY3="$(command -v python3 || true)"
if [ -z "$PY3" ]; then
    PY3="$(brew --prefix python3)/bin/python3"
fi
echo "  PY3         = $PY3"

# 再次替换 ProgramArguments 的 python3 路径
sed -i '' "s|<string>/usr/local/bin/python3</string>|<string>$PY3</string>|g" "$DST"

launchctl unload "$DST" 2>/dev/null || true
launchctl load "$DST"
launchctl start com.minimax.hailuo-bot

echo "✅ 已安装。要立刻跑一次测试："
echo "    cd $PROJECT_DIR && source .venv/bin/activate && python -m src.main --once"