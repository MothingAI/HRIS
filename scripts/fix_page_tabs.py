#!/usr/bin/env python3
"""修复 v1 页面路由与页面 schema 的官方形态偏差（REV-001 渲染阻塞根因）。

问题：build_ui.py 旧版创建的 tabs 子路由 schemaUid 指向页面根 schemaUid，
而 NocoBase 2.2.37 官方形态要求：
  - 页面 schema：Page(x-uid=s) -> {<tabSchemaName>: Grid(x-uid=d, x-async=true)}
  - tabs 子路由：schemaUid=d（Grid 内容节点）、tabSchemaName=Grid 的属性键

旧形态会让客户端把页面 schema 渲染两次；非 root 角色的 listAccessible 会给
tabs 子路由序列化 children=[]，第二次渲染读取 children[0].schemaUid 即崩溃
（root 下 children 键缺省，只渲染成空白，不崩溃）。

修复（幂等，逐页执行）：
  1. 读取页面 schema，定位 Page 下第一个 Grid 属性 (key, x-uid)；
  2. uiSchemas:patch 给 Grid 节点补 x-async=true（官方形态）；
  3. desktopRoutes:update 把 tabs 子路由改为 schemaUid=<Grid x-uid>、
     tabSchemaName=<Grid 属性键>。

用法：python scripts/fix_page_tabs.py   （.env 提供 INIT_ROOT_EMAIL/PASSWORD）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nb import NbClient  # noqa: E402

PAGE_TITLES = ["员工管理", "部门管理", "我的档案", "我的转正", "待我审批", "人事概览"]


def load_env():
    env = {}
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(base, ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v
    return env


def q(obj):
    return json.dumps(obj)


def main():
    env = load_env()
    admin = NbClient(email=env["INIT_ROOT_EMAIL"], password=env["INIT_ROOT_PASSWORD"])
    admin.signin()
    print(f"[fix] 已登录: {admin.user.get('nickname')} @ {admin.base}")

    routes = admin.call("GET", "/api/desktopRoutes:list", params={"pageSize": 200}).get("data", [])
    by_title = {r.get("title"): r for r in routes}

    fixed, skipped = [], []
    for title in PAGE_TITLES:
        page = by_title.get(title)
        if not page or page.get("type") != "page":
            print(f"[fix] 跳过（非 page 路由或缺失）: {title}")
            continue
        tabs = [
            r for r in routes
            if r.get("parentId") == page.get("id") and r.get("type") == "tabs"
        ]
        if len(tabs) != 1:
            print(f"[fix] 警告: {title} tabs 子路由数量异常 ({len(tabs)})")
            if not tabs:
                continue
        tab = tabs[0]

        schema = admin.call("GET", "/api/uiSchemas:getJsonSchema",
                            params={"resourceIndex": page["schemaUid"]}).get("data")
        if not schema:
            print(f"[fix] 跳过（页面 schema 不可读）: {title}")
            continue

        grid_key, grid_node = None, None
        for key, node in (schema.get("properties") or {}).items():
            if node.get("x-component") == "Grid":
                grid_key, grid_node = key, node
                break
        grid_uid = (grid_node or {}).get("x-uid")
        if not grid_uid:
            print(f"[fix] 跳过（Page 下未找到 Grid）: {title}")
            continue

        if tab.get("schemaUid") == grid_uid and tab.get("tabSchemaName") == grid_key \
                and grid_node.get("x-async") is True:
            skipped.append(title)
            print(f"[fix] 已是官方形态，跳过: {title}")
            continue

        # 1) Grid 节点补 x-async=true（官方形态；getJsonSchema 默认读缓存，patch 后失效）
        if grid_node.get("x-async") is not True:
            r = admin.call("POST", "/api/uiSchemas:patch",
                           json_body={"x-uid": grid_uid, "x-async": True})
            if not isinstance(r, dict):
                print(f"[fix] Grid patch 结果异常: {title} -> {json.dumps(r, ensure_ascii=False)[:200]}")

        # 2) tabs 子路由改指 Grid 节点
        r2 = admin.update("desktopRoutes", tab["id"],
                          {"schemaUid": grid_uid, "tabSchemaName": grid_key})
        ok2 = isinstance(r2, dict) and (
            r2.get("data") is not None or r2.get("result") or r2.get("_status") in (200, 201))
        print(f"[fix] {title}: tab#{tab['id']} schemaUid {tab.get('schemaUid')} -> {grid_uid}, "
              f"tabSchemaName {tab.get('tabSchemaName')} -> {grid_key} [{'OK' if ok2 else 'FAIL'}]")
        fixed.append(title)

    print(f"[fix] 完成: 修复 {len(fixed)} 页 {fixed}；已合规跳过 {len(skipped)} 页")
    return 0


if __name__ == "__main__":
    sys.exit(main())
