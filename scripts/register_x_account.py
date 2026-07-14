"""在 accounts.db 里登记一个 X 账号（用 cookie auth_token + ct0）。

拿值的方法：
1. 打开 https://x.com 并登录
2. 浏览器 DevTools → Application → Cookies → https://x.com
3. 找到 auth_token（最长那串）和 ct0 两项，复制它们的 Value
4. 运行：
   python scripts/register_x_account.py
   按提示粘贴即可

想批量登记：python scripts/register_x_account.py --batch accounts_seed.txt
文件格式每行：username auth_token ct0  （空格分隔）
"""
import argparse
import asyncio
import getpass
import os
from pathlib import Path

import twscrape


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="accounts.db")
    parser.add_argument("--batch", help="批量文件路径")
    parser.add_argument("auth_token", nargs="?", default=None)
    parser.add_argument("ct0", nargs="?", default=None)
    parser.add_argument("--username", default=None)
    args = parser.parse_args()

    api = twscrape.API(args.db)

    async def save_account(username: str, auth_token: str, ct0: str) -> None:
        if not username or not auth_token or not ct0:
            raise ValueError("username、auth_token、ct0 均不能为空")
        try:
            existing = await api.pool.get(username)
        except ValueError:
            existing = None
        if existing:
            await api.pool.delete_accounts(username)
        cookie_str = f"auth_token={auth_token}; ct0={ct0}"
        await api.pool.add_account(
            username, password="x", email="x", email_password="x",
            cookies=cookie_str,
        )
        print(f"✅ 已{'更新' if existing else '添加'} {username}")

    if args.batch:
        with open(args.batch) as f:
            for line_number, line in enumerate(f, 1):
                parts = line.strip().split()
                if len(parts) != 3:
                    print(f"⚠️ 跳过格式错误的第 {line_number} 行")
                    continue
                u, tok, ct = parts
                await save_account(u, tok, ct)
        Path(args.db).chmod(0o600)
        return

    username = args.username or os.environ.get("X_USERNAME") or input(
        "X 用户名（@handle，不要带 @）: "
    ).strip()
    auth_token = args.auth_token or os.environ.get("X_AUTH_TOKEN") or getpass.getpass(
        "auth_token: "
    ).strip()
    ct0 = args.ct0 or os.environ.get("X_CT0") or getpass.getpass("ct0: ").strip()

    await save_account(username, auth_token, ct0)
    Path(args.db).chmod(0o600)


if __name__ == "__main__":
    asyncio.run(main())
