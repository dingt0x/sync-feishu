import json
import os
import time

import requests

# 国内飞书用 open.feishu.cn；如果是国际版 Lark，改成 https://open.larksuite.com
BASE = os.getenv("FEISHU_BASE", "https://open.feishu.cn")
APP_ID = os.environ["FEISHU_APP_ID"]
APP_SECRET = os.environ["FEISHU_APP_SECRET"]


def get_tenant_token() -> str:
    """用 app_id / app_secret 换 tenant_access_token（有效期约 2 小时）"""
    r = requests.post(
        f"{BASE}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": APP_ID, "app_secret": APP_SECRET},
        timeout=10,
    )
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"获取 token 失败: {data}")
    return data["tenant_access_token"]


def call(token: str, path: str, params: dict) -> dict:
    """GET 请求封装：带简单重试（遇到 429 限流时退避）"""
    for attempt in range(5):
        r = requests.get(
            f"{BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=15,
        )
        if r.status_code == 429:
            time.sleep(2 ** attempt)
            continue
        data = r.json()  # 飞书出错时 HTTP 状态码也可能是 400，body 里才有原因
        if data.get("code") != 0:
            raise RuntimeError(f"调用 {path} 失败: {data}")
        return data["data"]
    raise RuntimeError(f"调用 {path} 多次被限流")


def paginate(token: str, path: str, params: dict):
    """通用翻页：不断用 page_token 取下一页，直到 has_more 为 False"""
    page_token = None
    while True:
        p = {**params, "page_size": 50}
        if page_token:
            p["page_token"] = page_token
        data = call(token, path, p)
        yield from data.get("items", [])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")


def list_all_departments(token: str) -> list[dict]:
    """递归获取根部门（0）下的所有子部门"""
    return list(
        paginate(
            token,
            "/open-apis/contact/v3/departments/0/children",
            {"fetch_child": "true", "department_id_type": "open_department_id"},
        )
    )


def list_users_in_department(token: str, dept_id: str) -> list[dict]:
    """获取某个部门的直属成员"""
    return list(
        paginate(
            token,
            "/open-apis/contact/v3/users/find_by_department",
            {
                "department_id": dept_id,
                "department_id_type": "open_department_id",
                "user_id_type": "open_id",
            },
        )
    )


def main():
    token = get_tenant_token()

    departments = list_all_departments(token)
    dept_ids = ["0"] + [d["open_department_id"] for d in departments]  # 别忘了根部门本身
    print(f"共 {len(dept_ids)} 个部门（含根部门）")

    users = {}  # 用 open_id 去重
    for dept_id in dept_ids:
        for u in list_users_in_department(token, dept_id):
            users[u["open_id"]] = u

    print(f"共 {len(users)} 个用户（已去重）")

    with open("feishu_users.json", "w", encoding="utf-8") as f:
        json.dump(list(users.values()), f, ensure_ascii=False, indent=2)

    # 打印前几个，看看返回了哪些字段
    for u in list(users.values())[:5]:
        print(
            u.get("name"),
            "| email:", u.get("email") or u.get("enterprise_email"),
            "| open_id:", u.get("open_id"),
            "| union_id:", u.get("union_id"),
            "| user_id:", u.get("user_id"),
                        )


if __name__ == "__main__":
    main()