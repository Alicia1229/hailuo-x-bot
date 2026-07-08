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

    if args.batch:
        with open(args.batch) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) != 3:
                    print(f"跳过格式错的行: {line!r}")
                    continue
                u, tok, ct = parts
                cookie_str = f"auth_token={tok}; ct0={ct}"
                await api.pool.add_account(u, "x", "x", "x", cookies=cookie_str)
                print(f"✅ 已添加 {u}")
        return

    username = args.username or input("X 用户名（@handle，不要带 @）: ").strip()
    auth_token = args.auth_token or getpass.getpass("auth_token: ").strip()
    ct0 = args.ct0 or getpass.getpass("ct0: ").strip()

    cookie_str = f"auth_token={auth_token}; ct0={ct0}"
    await api.pool.add_account(
        username, password="x", email="x", email_password="x",
        cookies=cookie_str,
    )
    print(f"✅ 已添加 {username}")


if __name__ == "__main__":
    asyncio.run(main())