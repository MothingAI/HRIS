#!/usr/bin/env python3
"""S3 build: 注册自研插件 + 收紧转正集合的默认 CRUD 权限。

幂等：插件已注册则跳过；员工在 confirmation_requests 上的 create 动作若存在则删除。
只使用 NocoBase 官方 API（applicationPlugins / dataSourcesRolesResourcesActions），
不直接写系统表。

用法：
  python scripts/build_s3.py
从 .env 读取 NB_BASE / INIT_ROOT_EMAIL / INIT_ROOT_PASSWORD。

注意：本脚本只负责“注册”与“权限收紧”；插件动作的加载需在应用重启后生效
（插件管理器启动时自动符号链接 storage/plugins 下的插件并加载）。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nb import NbClient  # noqa: E402


def load_env():
    env = {}
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
              encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v
    return env


ENV = load_env()
ADMIN = None

PLUGIN_NAME = "hris-confirmation"
PLUGIN_PACKAGE = "@nocobase/plugin-hris-confirmation"
PLUGIN_VERSION = "0.1.0"


def log(msg):
    print(f"[S3] {msg}", flush=True)


def q(params):
    out = dict(params)
    if "filter" in out and not isinstance(out["filter"], str):
        out["filter"] = json.dumps(out["filter"], ensure_ascii=False)
    return out


def register_plugin():
    log("== 注册插件 ==")
    r = ADMIN.call("GET", "/api/applicationPlugins:list",
                   params=q({"filter": {"name": PLUGIN_NAME}}))
    if r.get("data"):
        existing = r["data"][0]
        rid = existing["id"]
        need = {}
        if not existing.get("enabled"):
            need["enabled"] = True
        if not existing.get("installed"):
            need["installed"] = True
        if existing.get("version") != PLUGIN_VERSION:
            need["version"] = PLUGIN_VERSION
        if existing.get("packageName") != PLUGIN_PACKAGE:
            need["packageName"] = PLUGIN_PACKAGE
        if need:
            ADMIN.call("POST", "/api/applicationPlugins:update",
                       params={"filterByTk": rid}, json_body=need)
            log(f"更新插件记录: {PLUGIN_PACKAGE} -> {need}")
        else:
            log(f"插件已注册: {PLUGIN_PACKAGE} (跳过)")
        return
    r = ADMIN.call("POST", "/api/applicationPlugins:create", json_body={
        "name": PLUGIN_NAME,
        "packageName": PLUGIN_PACKAGE,
        "version": PLUGIN_VERSION,
        "enabled": True,
        "installed": True,
        "builtIn": False,
    })
    if not r.get("data"):
        log(f"插件注册失败: {json.dumps(r, ensure_ascii=False)[:400]}")
        raise SystemExit(1)
    log(f"插件已注册: {PLUGIN_PACKAGE}")


def remove_employee_create_on_requests():
    log("== 收紧转正集合默认 CRUD ==")
    # 找到 employee 角色在 confirmation_requests 上的资源条目
    r = ADMIN.call("GET", "/api/dataSourcesRolesResources:list",
                   params=q({"filter": {"roleName": "employee", "name": "confirmation_requests"}}))
    res = r.get("data", [])
    if not res:
        log("未找到 employee/confirmation_requests 资源条目 (跳过)")
        return
    rid = res[0]["id"]

    r = ADMIN.call("GET", "/api/dataSourcesRolesResourcesActions:list",
                   params=q({"filter": {"rolesResourceId": rid, "name": "create"}}))
    actions = r.get("data", [])
    if not actions:
        log("员工 create 动作已不存在 (跳过)")
        return
    for a in actions:
        ADMIN.call("POST", "/api/dataSourcesRolesResourcesActions:destroy",
                   params={"filterByTk": a["id"]})
        log(f"删除动作: employee/confirmation_requests:create (id={a['id']})")

    # 触发 ACL 重载（动作 create 不触发重载监听，需更新 resource）
    ADMIN.call("POST", "/api/dataSourcesRolesResources:update",
               params={"filterByTk": rid}, json_body={"usingActionsConfig": True})
    log("已触发 ACL 重载")


def main():
    global ADMIN
    os.environ.setdefault("NB_BASE", ENV.get("NB_BASE", "http://127.0.0.1:13000"))
    ADMIN = NbClient(email=ENV.get("INIT_ROOT_EMAIL"),
                     password=ENV.get("INIT_ROOT_PASSWORD"))
    ADMIN.signin()
    log(f"已登录管理员: {ADMIN.user.get('nickname')}")

    register_plugin()
    remove_employee_create_on_requests()

    log("== S3 构建完成（插件加载需重启应用） ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
