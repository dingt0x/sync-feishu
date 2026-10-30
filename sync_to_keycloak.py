import json
import os

import requests

KC = os.environ["KC_URL"].rstrip("/")
REALM = os.environ["KC_REALM"]
CID = os.environ["KC_CLIENT_ID"]
CSECRET = os.environ["KC_CLIENT_SECRET"]
IDP_ALIAS = os.getenv("KC_IDP_ALIAS", "feishu")  # 注意大小写，要和 Keycloak 里的 alias 一致
DRY_RUN = os.getenv("DRY_RUN", "1") == "1"      # 默认只打印，不写入
LIMIT = int(os.getenv("LIMIT", "0"))            # 只处理前 N 个人，0 表示全部

ADMIN = f"{KC}/admin/realms/{REALM}"


def get_token() -> str:
    r = requests.post(
        f"{KC}/realms/{REALM}/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CID,
            "client_secret": CSECRET,
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def find_user(h: dict, username: str):
    """按 username 精确查找，找不到返回 None"""
    r = requests.get(
        f"{ADMIN}/users",
        headers=h,
        params={"username": username, "exact": "true"},
        timeout=10,
    )
    r.raise_for_status()
    users = r.json()
    return users[0] if users else None


def create_user(h: dict, f: dict) -> str:
    body = {
        "username": f["union_id"],
        "firstName": f.get("name") or "",
        "lastName": "",
        "enabled": True,
    }
    email = f.get("email") or f.get("enterprise_email")
    if email:
        body["email"] = email
    r = requests.post(f"{ADMIN}/users", headers=h, json=body, timeout=10)
    r.raise_for_status()  # 成功是 201，新用户 ID 在 Location 头的最后一段
    return r.headers["Location"].rstrip("/").split("/")[-1]


def has_link(h: dict, user_id: str) -> bool:
    r = requests.get(f"{ADMIN}/users/{user_id}/federated-identity", headers=h, timeout=10)
    r.raise_for_status()
    return any(x["identityProvider"] == IDP_ALIAS for x in r.json())


def add_link(h: dict, user_id: str, f: dict):
    r = requests.post(
        f"{ADMIN}/users/{user_id}/federated-identity/{IDP_ALIAS}",
        headers=h,
        json={
            "identityProvider": IDP_ALIAS,
            "userId": f["union_id"],     # 必须和登录插件写入的一致，先按上面第 2 点确认
            "userName": f["union_id"],
        },
        timeout=10,
    )
    r.raise_for_status()


def update_user(h: dict, user_id: str, f: dict) -> dict:
    """读出完整用户对象，只改有变化的字段再写回；返回变化内容，没变化返回空 dict"""
    r = requests.get(f"{ADMIN}/users/{user_id}", headers=h, timeout=10)
    r.raise_for_status()
    user = r.json()

    changes = {}
    new_name = f.get("name") or ""
    if new_name and user.get("firstName") != new_name:
        changes["firstName"] = (user.get("firstName"), new_name)
        user["firstName"] = new_name

    # 飞书邮箱为空时不清空 Keycloak 里已有的邮箱
    new_email = f.get("email") or f.get("enterprise_email")
    if new_email and user.get("email") != new_email:
        changes["email"] = (user.get("email"), new_email)
        user["email"] = new_email

    if changes and not DRY_RUN:
        r = requests.put(f"{ADMIN}/users/{user_id}", headers=h, json=user, timeout=10)
        r.raise_for_status()
    return changes


def main():
    feishu_users = json.load(open("feishu_users.json", encoding="utf-8"))
    if LIMIT:
        feishu_users = feishu_users[:LIMIT]

    h = {"Authorization": f"Bearer {get_token()}"}
    stats = {"created": 0, "updated": 0, "linked": 0, "skipped": 0}

    for i, f in enumerate(feishu_users):
        # 批量很大时 token 可能过期，这里每 100 个人换一次
        if i and i % 100 == 0:
            h = {"Authorization": f"Bearer {get_token()}"}

        if not f.get("union_id"):
            print(f"跳过（没有 union_id）: {f.get('name')}")
            stats["skipped"] += 1
            continue

        existing = find_user(h, f["union_id"])
        if existing:
            user_id = existing["id"]
            changes = update_user(h, user_id, f)
            if changes:
                print(f"[更新] {f.get('name')}: {changes}")
                stats["updated"] += 1
        else:
            print(f"[新建] {f.get('name')} ({f['union_id']})")
            stats["created"] += 1
            if DRY_RUN:
                stats["linked"] += 1
                continue
            user_id = create_user(h, f)

        if not has_link(h, user_id):
            print(f"[关联] {f.get('name')} -> {IDP_ALIAS}")
            if not DRY_RUN:
                add_link(h, user_id, f)
            stats["linked"] += 1

    print(("DRY RUN " if DRY_RUN else "") + str(stats))


if __name__ == "__main__":
    main()