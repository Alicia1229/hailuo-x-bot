# Hailuo X Daily Bot

每天 17:00（Asia/Shanghai）抓 X 上昨天自然日 00:00-24:00 里提到过
`hailuo03, hailuo3, hailuo, hailuoai, MiniMax Video, MiniMax H3` 的推文，
按 views 降序排好，发到飞书群里。

## 文件结构

```
hailuo-x-bot/
├── src/
│   ├── scraper.py      # X 抓取（twscrape，零付费 API）
│   ├── feishu.py       # 飞书自定义机器人 → 富文本卡片
│   └── main.py         # 调度入口（APScheduler 常驻 / --once 单跑）
├── scripts/
│   ├── register_x_account.py  # 登记 X 账号
│   ├── install_launchd.sh     # macOS 一键安装定时任务
│   ├── com.minimax.hailuo-bot.plist
│   └── crontab.txt
├── .github/workflows/daily.yml   # GitHub Actions 备份方案
├── .env.example
├── requirements.txt
└── README.md
```

## 第一步：拿飞书 Webhook

1. 飞书 → 目标群 → 右上角设置 → 群机器人 → 添加机器人 → 自定义机器人
2. 给它起个名字、选个头像、设"加签校验"（可选，复制下面的 Secret）
3. 复制 "Webhook 地址"（形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxx`）

## 第二步：拿 X 凭证（用你日常刷的那个号就行）

1. 浏览器打开 https://x.com，已登录状态
2. 打开 DevTools → Application 标签 → Cookies → `https://x.com`
3. 找到下面两项，复制它们的 **Value**：
   - `auth_token`（一长串）
   - `ct0`（也一长串）

> 注意：不要把这两串发到聊天/邮件里，拿到就贴进 `.env`，贴完别上传。

## 第三步：本地测试

```bash
cd /Users/minimax/hailuo-x-bot

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 改 .env：填 FEISHU_WEBHOOK_URL 和 X 相关

# 1) 登记 X 账号
python scripts/register_x_account.py
# 按提示粘 auth_token 和 ct0

# 2) 飞书发送测试（不接 X，确认机器人能收到）
python -c "from src import feishu; print(feishu.send('$FEISHU_WEBHOOK_URL', feishu.build_card([], 24)))"

# 3) 跑一次完整链路
python -m src.main --once
```

跑完去飞书对应群看一眼，应该会收到一条昨天自然日窗口的日报卡片。

## 第四步：上调度（选一个）

### 方案 A：macOS launchd（推荐，只要电脑在 17:00 左右开着）

```bash
bash scripts/install_launchd.sh
```

这条命令会：
- 把 plist 拷到 `~/Library/LaunchAgents/`
- 把脚本里的项目路径替换成你的实际路径
- `launchctl load` 一下，每晚 17:00 自动跑

**卸载**：`launchctl unload ~/Library/LaunchAgents/com.minimax.hailuo-bot.plist`

### 方案 B：GitHub Actions（电脑关机也能跑）

1. 在 GitHub 上创建仓库并启用 GitHub Pages；当前项目使用公开仓库托管静态报告
2. Settings → Secrets and variables → Actions → New repository secret，依次加：
   - `FEISHU_WEBHOOK_URL` —— 你的飞书 webhook
   - `FEISHU_SECRET` —— 飞书加签 secret（如果加了签名校验）
   - `X_AUTH_TOKEN` —— 你从 X 浏览器拿的 auth_token
   - `X_CT0` —— ct0
   - `X_USERNAME` —— 你的 X @handle 不带 @
   - `OPENAI_API_KEY` —— 用于 AI 判定风险监控（可选；不配会退回词典兜底）
   - `OPENAI_BASE_URL` —— OpenAI 兼容接口地址（可选；Mafia 平台填 `https://api.appintheloop.com/v1`）
   - `RISK_MODEL` —— 风险判定模型（可选；Mafia 平台可填 `hy-4.1-mini` / `hy-4o-mini` 等）
3. Actions → 启用 workflows
4. 默认 `cron: "0 9 * * *"` = 每天 **17:00 Asia/Shanghai**

> GitHub 的 cron 是 UTC 时区。每天 17:00 Asia/Shanghai = 09:00 UTC，可直接用现成的。
>
> 云端流程会先生成并 push GitHub Pages，确认本次报告 ID 已上线后再发送飞书；任一步骤失败都会让 Actions 标红。
> 本地/launchd 模式不会自动发布 GitHub Pages，因此飞书卡片不附完整报告链接，避免发送尚未上线的地址。

### 方案 C：cron（懒得解释，老手用）

```bash
crontab -e
# 加上这一行：
0 17 * * * cd /Users/minimax/hailuo-x-bot && .venv/bin/python -m src.main --once >> logs/cron.log 2>&1
```

## 工作原理（30 秒版）

- `scraper.py` 用 [twscrape](https://github.com/vladkens/twscrape) 调 X 前端 GraphQL，不需要付费 API key，但需要你提供至少一个登录态（auth_token + ct0）来证明身份
- `feishu.py` 把结果打包成飞书 schema 2.0 的卡片（每条推文一行含 views/❤️/🔁/💬，下面挂一个"打开推文"按钮跳原推）
- `main.py` 提供两种模式：常驻调度 (`python -m src.main`) 和跑一次就退出 (`--once`)，后者是为了方便挂到 launchd / cron / GitHub Actions
- 每天 17:00 发送昨天自然日 00:00-24:00 的数据；Hailuo 主查询默认不设条数上限，并按 tweet ID 去重
- Hailuo 和竞品查询默认均不设置条数上限，尽可能抓取固定时间窗口内的完整数据；高频词可能导致任务运行较久或触发 X 限流
- 飞书会先发 Hailuo 主卡片：Views Top 5、所有命中帖子表格链接、Related 高频词云、热议话题 Top 3 和风险监控；风险监控配置 `OPENAI_API_KEY` 后由 AI 最终判定，可用 `OPENAI_BASE_URL` 切到 Mafia / OpenAI 兼容接口，竞品横向对比会在抓取完成后单独发第二张卡片

## 常见问题

**Q: 飞书推送的 20KB 限制**  
A: Hailuo 主卡片只展示 Views Top 5 和三个热议话题代表帖，所有命中帖子通过 Top 5 后面的 GitHub Pages 表格链接查看；竞品数据单独一张卡片发送。

**Q: 我想换关键词**  
A: 改 `.env` 里 `KEYWORDS=hailuo,hailuoai,MiniMax`，逗号分隔。

**Q: 抓不到东西**  
A: 看 `logs/bot.log` 和 `logs/launchd.err.log`。最常见原因是 X 的 cookie 失效（几个月没登录）——重新跑 `scripts/register_x_account.py` 即可。

**Q: 我的 X 账号被风控了**  
A: 优先用一个小号、被封风险低的那种。twscrape 内部有账号池，多账号轮转限流。

**Q: 100 条/月免费 API 不行吗**  
A: 不行，X Free 套餐只支持 `/2/tweets/search/recent` 极有限配额，且拿不到 viewCount。twscrape 是目前最干净的零成本方案。

## 风险/提醒

- 用 cookie 走 X 前端 GraphQL 接口**违反 X 服务条款**——个人小规模使用风险很低，但理论上账号可能被限制。介意的话改用付费 X API ($100/月起) 或 Apify。
- `auth_token` 等于你的 X 账号权限，请妥善保存。`.gitignore` 已经把 `.env` 和 `accounts.db` 排除。
- 飞书 webhook 也别外泄，否则别人能往你群里发消息。
- 不要把带 PAT 的 GitHub URL 直接写进终端命令；凭证可能进入 shell 历史，优先使用 Git Credential Manager 或 `gh auth login`。
