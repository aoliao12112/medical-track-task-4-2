"""Create the four local role-demo users without changing existing accounts."""
from __future__ import annotations

import os

from medical_claims.auth import hash_password
from medical_claims.storage import CaseStorage


USERS = (
    ("demo_claim", "CLAIM", "演示理赔员"),
    ("demo_medical", "MEDICAL", "演示医疗复核员"),
    ("demo_service", "SERVICE", "演示客服"),
    ("demo_customer", "CUSTOMER", "演示客户"),
)


def main() -> int:
    password = os.environ.get("DEMO_USER_PASSWORD")
    if not password or len(password) < 8:
        raise SystemExit("请通过 DEMO_USER_PASSWORD 提供至少 8 位的本地演示密码")

    storage = CaseStorage()
    created: list[str] = []
    existing: list[str] = []
    for username, role, display_name in USERS:
        if storage.get_user_by_username(username):
            existing.append(username)
            continue
        storage.create_user(
            username=username,
            password_hash=hash_password(password),
            role_code=role,
            display_name=display_name,
            phone="13800001234" if role == "CUSTOMER" else None,
            email="demo.customer@example.com" if role == "CUSTOMER" else None,
        )
        created.append(username)

    print(f"created={','.join(created) or '-'}")
    print(f"existing={','.join(existing) or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
