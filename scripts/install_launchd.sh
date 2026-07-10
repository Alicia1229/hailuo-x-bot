#!/usr/bin/env bash
# 一键安装 launchd 定时任务
#
# 优先级：
#   1) <project>/.venv/bin/python  （项目内 venv，依赖最稳）
#   2) 系统 python3  （必须已装 requirements.txt）
#   3) brew python3  （如果存在）
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

# 选 python：先用 venv 的
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    PY3="$PROJECT_DIR/.venv/bin/python"
elif [ -x "$PROJECT_DIR/.venv/bin/python3" ]; then
    PY3="$PROJECT_DIR/.venv/bin/python3"
else
    PY3="$(command -v python3 || true)"
    if [ -z "$PY3" ]; then
        PY3="$(brew --prefix python3 2>/dev/null)/bin/python3"
    fi
    if [ -z "$PY3" ] || [ ! -x "$PY3" ]; then
        echo "❌ 找不到 python。先建 venv："
        echo "    cd $PROJECT_DIR && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
        exit 1
    fi
    # 兜底：试一下 deps，没装就提示
    if ! "$PY3" -c "import twscrape, httpx, apscheduler, dotenv" 2>/dev/null; then
        echo "⚠️  $PY3 里没有依赖，建议改用 .venv/bin/python（已自动重选）"
        if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
            PY3="$PROJECT_DIR/.venv/bin/python"
        fi
    fi
fi
echo "  PY3         = $PY3"

# 替换 ProgramArguments 的 python3 路径
sed -i '' "s|<string>/usr/local/bin/python3</string>|<string>$PY3</string>|g" "$DST"

launchctl unload "$DST" 2>/dev/null || true
launchctl load "$DST"
launchctl start com.minimax.hailuo-bot

# 如果 plist 的 PUSH_HOUR/PUSH_MINUTE（plist 写死 19:00）跟 .env 不一致
# 提醒一下用户
ENV_PUSH_HOUR="$(grep -E '^PUSH_HOUR=' "$PROJECT_DIR/.env" 2>/dev/null | cut -d= -f2 || true)"
if [ -n "$ENV_PUSH_HOUR" ] && [ "$ENV_PUSH_HOUR" != "19" ]; then
    echo "⚠️  .env 里 PUSH_HOUR=$ENV_PUSH_HOUR，但 plist 写死了 19:00。修改跑点请改 plist。"
fi

echo "✅ 已安装。要立刻跑一次测试："
echo "    cd $PROJECT_DIR && source .venv/bin/activate && python -m src.main --once"