"""Create one local development user without storing a plaintext password."""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from medical_claims.auth import hash_password
from medical_claims.storage import CaseStorage


def main() -> int:
    parser = argparse.ArgumentParser(description="创建本地开发账号")
    parser.add_argument("--username", required=True, help="登录账号 / 工号")
    parser.add_argument("--display-name", required=True, help="页面显示名称")
    parser.add_argument("--role", default="CLAIM", choices=("CLAIM", "MEDICAL", "SERVICE", "CUSTOMER"))
    args = parser.parse_args()

    password = os.getenv("DEMO_USER_PASSWORD") or getpass.getpass("请输入密码（至少 8 位）：")
    confirmation = os.getenv("DEMO_USER_PASSWORD") or getpass.getpass("请再次输入密码：")
    if password != confirmation:
        print("两次密码不一致", file=sys.stderr)
        return 2

    storage = CaseStorage()
    if storage.get_user_by_username(args.username):
        print("该账号已存在；脚本不会覆盖现有密码", file=sys.stderr)
        return 3
    user_id = storage.create_user(
        username=args.username,
        password_hash=hash_password(password),
        role_code=args.role,
        display_name=args.display_name,
    )
    print(f"已创建本地 {args.role} 开发账号：{args.username}（user_id={user_id}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
