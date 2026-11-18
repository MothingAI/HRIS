#!/usr/bin/env python3
"""低代码配置演示（N3.4）：管理员向 employees 集合新增选填字段"紧急联系人"
（emergencyContact），不改任何前端/插件源码，即出现在 HR 员工编辑表单与
员工"我的档案"表格，可填写、保存、重新查看，旧记录正常打开。

步骤（全部走已验证的平台路径）：
1. 归一化 employees 上 fields=null 的角色动作（NocoBase 2.2.37 fields:create
   ACL 钩子对 null 展开 [...null] 会 500 回滚，见 docs/demo/LOWCODE-DEMO.md §3）。
   归一化只在建字段期间临时生效，建完立即复位 null（=全部字段）：显式列表只含
   注册字段、不含 FK 列（departmentId/managerId/userId），会把 FK 列从
   list/get 响应中剥掉（N4 空库复现实测）。另含自愈：早期版本把 view 白名单
   永久写成显式列表造成的同款损伤，检测到即复位；
2. POST /api/fields:create 新增字段（幂等：已存在则跳过）。该钩子同时把新字段
   追加进 hr create/update 的显式字段白名单（本脚本断言这一点）；
3. 需要时经官方 POST /api/fields:apply 补 uiSchema（title/x-component）；
4. 经官方 POST /api/uiSchemas:insertAdjacent 把字段节点插入
   "员工管理"编辑抽屉表单（Grid.Row 包裹）与"我的档案"表格列（官方列壳），
   等价于管理员在设计器里把字段拖进表单/表格（幂等：树中已含则跳过）。
5. 校验 employees."userId" 唯一索引仍在（REV-003 修复后由平台模型持续维护，
   见插件 unique-binding.js；本步兜底：万一缺失则用与插件迁移相同的 SQL 重申，
   先查重复，重复则只报告不动数据）。SQL 执行前经 target_binding 核验
   API/app/DB 同一目标（REV-007）。

用法：python scripts/demo_lowcode_employee_field.py
浏览器实测（填写/保存/旧记录）另见 scripts/browser/probe-n3-lowcode.js
与 docs/evidence/n3-lowcode-*.png。
"""
import json
import os
import random
import string
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nb import NbClient  # noqa: E402
from target_binding import verify_target  # noqa: E402
from verify_common import check, load_env, summary  # noqa: E402

COLL = "employees"
FIELD = "emergencyContact"
TITLE = "紧急联系人"
UID_ALPHABET = string.ascii_lowercase + "0123456789"

RESULTS = []


def uid():
    return "".join(random.choices(UID_ALPHABET, k=11))


def log(msg):
    print(f"[lowcode] {msg}", flush=True)


def load_admin():
    env = load_env()
    admin = NbClient(email=env["INIT_ROOT_EMAIL"], password=env["INIT_ROOT_PASSWORD"])
    admin.signin()
    return admin


def collection_fields(admin):
    r = admin.call("GET", "/api/collections:get",
                   params={"filterByTk": COLL, "appends": ["fields"],
                           "autoGenerate": "false"})
    return {f["name"]: f for f in (r.get("data") or {}).get("fields") or []}


def normalize_null_fields(admin):
    """fields:create 的 ACL 钩子会展开集合上所有动作的字段白名单，fields=null
    时展开 [...null] 触发 500 回滚。临时把 null 改为显式注册字段列表，返回这些
    动作 id，建完字段后 restore_null_fields 复位为 null。不能保持显式列表：
    注册字段不含 FK 列（departmentId/managerId/userId 不在 collections:get /
    fields:list 里），显式白名单会把 FK 列从 list/get 响应中剥掉（实测
    2.2.37，hr/employee/manager 全部读不到 departmentId）。"""
    cur = list(collection_fields(admin).keys())
    res = admin.call("GET", "/api/dataSourcesRolesResources:list",
                     params={"filter": json.dumps({"name": COLL}, ensure_ascii=False)})
    null_ids = []
    for r_ in res.get("data", []):
        rid = r_["id"]
        acts = admin.call("GET", "/api/dataSourcesRolesResourcesActions:list",
                          params={"filter": json.dumps({"rolesResourceId": rid},
                                                       ensure_ascii=False)})
        for a in acts.get("data", []):
            if a.get("fields") is None:
                admin.call("POST", "/api/dataSourcesRolesResourcesActions:update",
                           params={"filterByTk": a["id"]},
                           json_body={"fields": cur})
                null_ids.append(a["id"])
        admin.call("POST", "/api/dataSourcesRolesResources:update",
                   params={"filterByTk": rid},
                   json_body={"usingActionsConfig": True})
    return null_ids


def restore_null_fields(admin, null_ids):
    """把 normalize 临时改显式的动作白名单复位为 null（=全部字段，含 FK 列）。"""
    for aid in null_ids:
        admin.call("POST", "/api/dataSourcesRolesResourcesActions:update",
                   params={"filterByTk": aid}, json_body={"fields": None})


def heal_view_whitelist(admin):
    """自愈：早期版本的归一化把 view 白名单永久写成显式列表（缺 FK 列），
    导致读不到 departmentId 等。view 的设计语义是 null（全部字段，
    build_s2 canonical：view 不设 fields）。检测到缺 FK 列的 view 白名单
    即复位为 null。"""
    res = admin.call("GET", "/api/dataSourcesRolesResources:list",
                     params={"filter": json.dumps({"name": COLL}, ensure_ascii=False)})
    healed = 0
    for r_ in res.get("data", []):
        acts = admin.call("GET", "/api/dataSourcesRolesResourcesActions:list",
                          params={"filter": json.dumps({"rolesResourceId": r_["id"]},
                                                       ensure_ascii=False)})
        for a in acts.get("data", []):
            if a.get("name") != "view":
                continue
            f = a.get("fields")
            if isinstance(f, list) and "departmentId" not in f:
                admin.call("POST", "/api/dataSourcesRolesResourcesActions:update",
                           params={"filterByTk": a["id"]},
                           json_body={"fields": None})
                healed += 1
                log(f"view 白名单缺 FK 列，复位为 null: {r_.get('roleName')}")
    return healed


def hr_update_fields(admin):
    res = admin.call("GET", "/api/dataSourcesRolesResources:list",
                     params={"filter": json.dumps({"name": COLL}, ensure_ascii=False)})
    for r_ in res.get("data", []):
        if r_.get("roleName") != "hr":
            continue
        acts = admin.call("GET", "/api/dataSourcesRolesResourcesActions:list",
                          params={"filter": json.dumps({"rolesResourceId": r_["id"]},
                                                       ensure_ascii=False)})
        for a in acts.get("data", []):
            if a.get("name") == "update":
                return a.get("fields")
    return None


def ensure_hr_whitelist(admin):
    """自愈：hr create/update 白名单若缺新字段（如 build_s2 幂等重跑覆写、或字段
    建立于归一化之前），经官方 actions:update 显式补上——与 fields:create 钩子的
    追加效果一致，路径相同。"""
    res = admin.call("GET", "/api/dataSourcesRolesResources:list",
                     params={"filter": json.dumps({"name": COLL}, ensure_ascii=False)})
    for r_ in res.get("data", []):
        if r_.get("roleName") != "hr":
            continue
        acts = admin.call("GET", "/api/dataSourcesRolesResourcesActions:list",
                          params={"filter": json.dumps({"rolesResourceId": r_["id"]},
                                                       ensure_ascii=False)})
        for a in acts.get("data", []):
            if a.get("name") not in ("create", "update"):
                continue
            cur = a.get("fields")
            if isinstance(cur, list) and FIELD not in cur:
                admin.call("POST", "/api/dataSourcesRolesResourcesActions:update",
                           params={"filterByTk": a["id"]},
                           json_body={"fields": cur + [FIELD]})
                log(f"hr {a.get('name')} 白名单补上 {FIELD}")


def get_page_schema(admin, title):
    routes = admin.call("GET", "/api/desktopRoutes:list",
                        params={"pageSize": 200}).get("data", [])
    page = next((r for r in routes if r.get("title") == title
                 and r.get("type") == "page"), None)
    if not page:
        return None, None
    s = admin.call("GET", "/api/uiSchemas:getJsonSchema",
                   params={"resourceIndex": page["schemaUid"],
                           "includeAsyncNode": "true"}).get("data")
    return s, page


def walk(node, fn, hits=None):
    """深度遍历 schema 树，返回 fn(node) 为真的节点列表。"""
    hits = [] if hits is None else hits
    if isinstance(node, dict):
        if fn(node):
            hits.append(node)
        for child in (node.get("properties") or {}).values():
            walk(child, fn, hits)
    return hits


def tree_has_field(node):
    return bool(walk(node, lambda n: n.get("x-collection-field")
                     == f"{COLL}.{FIELD}"))


def form_field_row():
    """Grid.Row > Grid.Col > CollectionField（编辑表单字段节点，官方形态）。"""
    return {
        "type": "void",
        "x-uid": uid(),
        "x-component": "Grid.Row",
        "properties": {
            uid(): {
                "type": "void",
                "x-component": "Grid.Col",
                "properties": {
                    FIELD: {
                        "type": "string",
                        "title": TITLE,
                        "x-component": "CollectionField",
                        "x-decorator": "FormItem",
                        "x-collection-field": f"{COLL}.{FIELD}",
                    }
                },
            }
        },
    }


def table_column():
    """官方列壳：TableV2.Column.Decorator + TableV2.Column + 只读字段。"""
    return {
        "type": "void",
        "x-uid": uid(),
        "x-decorator": "TableV2.Column.Decorator",
        "x-toolbar": "TableColumnSchemaToolbar",
        "x-settings": "fieldSettings:TableColumn",
        "x-component": "TableV2.Column",
        "properties": {
            FIELD: {
                "type": "string",
                "x-component": "CollectionField",
                "x-read-pretty": True,
                "x-collection-field": f"{COLL}.{FIELD}",
            }
        },
    }


def insert_adjacent(admin, target_uid, schema):
    return admin.call("POST", "/api/uiSchemas:insertAdjacent",
                      params={"resourceIndex": target_uid,
                              "position": "beforeEnd"},
                      json_body={"schema": schema})


def unique_user_id_index(env):
    """employees."userId" 唯一索引是否在库（任意 user 命名的唯一索引）。"""
    container = env.get("NB_DB_CONTAINER", "hris-postgres-1")
    e = dict(os.environ)
    e["PGPASSWORD"] = env["POSTGRES_PASSWORD"]
    r = subprocess.run(
        ["docker", "exec", container, "psql", "-U", env["POSTGRES_USER"],
         "-d", env["POSTGRES_DB"], "-t", "-A", "-c",
         "SELECT count(*) FROM pg_index x "
         "JOIN pg_class c ON c.oid = x.indexrelid "
         "JOIN pg_class t ON t.oid = x.indrelid "
         "WHERE t.relname='employees' AND c.relname LIKE '%user%' "
         "AND x.indisunique"],
        capture_output=True, text=True, env=e)
    return (r.stdout or "").strip()


def reassert_unique_index(env):
    """字段同步会删除非平台模型的索引（含本插件迁移建的唯一索引，实测 2.2.37）。
    fields:create 之后、下次启动迁移之前，用与插件迁移相同的 SQL 立即重申
    （先查重复，重复则只报告不动数据）。容器由 NB_DB_CONTAINER 绑定，与
    NB_BASE 指向的实例保持一致（同 verify_regression 的目标绑定规则）。"""
    container = env.get("NB_DB_CONTAINER", "hris-postgres-1")
    e = dict(os.environ)
    e["PGPASSWORD"] = env["POSTGRES_PASSWORD"]

    def q(sql):
        return subprocess.run(
            ["docker", "exec", container, "psql", "-U", env["POSTGRES_USER"],
             "-d", env["POSTGRES_DB"], "-t", "-A", "-c", sql],
            capture_output=True, text=True, env=e)

    dups = q('SELECT count(*) FROM (SELECT "userId" FROM employees '
             'WHERE "userId" IS NOT NULL GROUP BY "userId" HAVING count(*)>1) t')
    if (dups.stdout or "").strip() not in ("0",):
        log(f"检测到重复 userId 绑定（{dups.stdout.strip()}），不建索引、不动数据")
        return False
    if unique_user_id_index(env) != "0":
        return True  # 已存在（含表级约束被同步删除前的残留）
    r = subprocess.run(
        ["docker", "exec", container, "psql", "-U", env["POSTGRES_USER"],
         "-d", env["POSTGRES_DB"], "-t", "-A", "-c",
         'CREATE UNIQUE INDEX IF NOT EXISTS employees_user_id_key '
         'ON employees ("userId")'],
        capture_output=True, text=True, env=e)
    return r.returncode == 0


def main():
    admin = load_admin()

    # 0) 自愈历史损伤：早期版本把 view 白名单写成显式列表（缺 FK 列），
    #    hr/employee/manager 读不到 departmentId 等，复位为 null
    healed = heal_view_whitelist(admin)
    if healed:
        log(f"自愈 {healed} 个 view 白名单（复位 null）")

    # 1) 归一化 + 建字段 + 复位
    fields = collection_fields(admin)
    if FIELD in fields:
        log(f"字段 {FIELD} 已存在，跳过创建")
    else:
        null_ids = normalize_null_fields(admin)
        if null_ids:
            log(f"临时归一化 {len(null_ids)} 个 null 字段动作 -> 显式列表")
        r = admin.call("POST", "/api/fields:create", json_body={
            "collectionName": COLL,
            "name": FIELD,
            "type": "string",
            "interface": "input",
            "title": TITLE,
        })
        check("新增字段成功", bool(r.get("data")),
              json.dumps(r, ensure_ascii=False)[:200])
        restore_null_fields(admin, null_ids)
        if null_ids:
            log(f"已复位 {len(null_ids)} 个动作白名单 -> null（=全部字段）")

    # 2) hr create/update 白名单须含新字段：fields:create 钩子会在创建时自动追加；
    #    字段已存在（重跑）或其他脚本覆写白名单时，走同一路径显式补上（自愈）
    ensure_hr_whitelist(admin)
    upd = hr_update_fields(admin)
    check("hr update 白名单含新字段（钩子追加/幂等自愈）",
          bool(upd) and FIELD in upd, f"fields={upd}")

    # 3) uiSchema 元数据（title/x-component）缺失则经官方 fields:apply 补
    f = collection_fields(admin).get(FIELD) or {}
    ui = f.get("uiSchema") or {}
    if not (ui.get("title") == TITLE and ui.get("x-component")):
        r = admin.call("POST", "/api/fields:apply", json_body={
            "collectionName": COLL, "name": FIELD,
            "interface": f.get("interface") or "input",
            "type": f.get("type") or "string", "title": TITLE,
        })
        check("fields:apply 补 uiSchema", bool(r.get("data")),
              json.dumps(r, ensure_ascii=False)[:200])
        ui = (collection_fields(admin).get(FIELD) or {}).get("uiSchema") or {}
    check("字段 uiSchema 齐备", ui.get("title") == TITLE and bool(ui.get("x-component")),
          f"uiSchema={json.dumps(ui, ensure_ascii=False)[:120]}")

    # 4a) 员工管理 编辑抽屉表单：x-decorator-props.action=get 的 FormV2 内 grid
    schema, page = get_page_schema(admin, "员工管理")
    check("员工管理 schema 可读", bool(schema), f"page={page and page.get('id')}")
    edit_deco = walk(schema, lambda n: (n.get("x-decorator-props") or {})
                     .get("action") == "get")
    target = None
    for deco in edit_deco:
        grids = walk(deco, lambda n: n.get("x-component") == "Grid"
                     and (n.get("x-initializer") == "form:configureFields"))
        if grids:
            target = grids[0]
            break
    if not target:
        check("定位编辑表单 grid", False, "未找到 form:configureFields Grid")
    elif tree_has_field(target):
        log("编辑表单已含字段，跳过插入")
    else:
        r = insert_adjacent(admin, target["x-uid"], form_field_row())
        check("字段插入编辑表单", not (isinstance(r, dict) and r.get("errors")),
              json.dumps(r, ensure_ascii=False)[:150])
    # 回读断言
    schema2, _ = get_page_schema(admin, "员工管理")
    check("员工管理 schema 已含 emergencyContact 字段节点",
          tree_has_field(schema2))

    # 4b) 我的档案 表格列
    schema, page = get_page_schema(admin, "我的档案")
    check("我的档案 schema 可读", bool(schema), f"page={page and page.get('id')}")
    tables = walk(schema, lambda n: n.get("x-component") == "TableV2")
    done = any(tree_has_field(t) for t in tables)
    if done:
        log("我的档案表格已含字段列，跳过插入")
    elif tables:
        # TableV2 节点的 properties 即列容器
        r = insert_adjacent(admin, tables[0]["x-uid"], table_column())
        check("字段列插入我的档案", not (isinstance(r, dict) and r.get("errors")),
              json.dumps(r, ensure_ascii=False)[:150])
    else:
        check("定位我的档案 TableV2", False, "未找到 TableV2")
    schema3, _ = get_page_schema(admin, "我的档案")
    check("我的档案 schema 已含 emergencyContact 列", tree_has_field(schema3))

    # 5) 字段同步会删掉插件迁移建的唯一索引：立即重申并断言（与插件迁移同 SQL）
    #    REV-003 修复后索引由平台模型维护（unique-binding.js），本步通常只读校验；
    #    REV-007：任何 SQL（含 CREATE INDEX 兜底）前核验 API/app/DB 同一目标。
    env = load_env()
    try:
        binding = verify_target(env, "低代码字段脚本（索引校验 SQL）")
        print(f"[lowcode] 目标绑定一致: {binding['base']} / {binding['app']} / "
              f"{binding['db']} (project={binding['project']})", flush=True)
    except Exception as e:  # noqa: BLE001
        check("目标绑定一致（API/app/DB 同一项目）", False, str(e)[:200])
        return summary()
    reassert_unique_index(env)
    n_uniq = unique_user_id_index(env)
    check("employees.userId 唯一索引在库（fields:create 后重申）",
          n_uniq not in ("0", "", None), f"uniq_idx={n_uniq}")

    # 6) FK 列可读回归断言：白名单复位后 hr 读员工列表须含 departmentId
    #    （早期归一化损伤的实测症状是全表 departmentId=None）
    hr = NbClient(email="hr@example.com", password=env["DEMO_PASSWORD"])
    hr.base = admin.base
    hr.signin()
    rows = hr.call("GET", "/api/employees:list").get("data", [])
    check("hr 读员工列表含 FK 列 departmentId（白名单未剥字段）",
          bool(rows) and all(r.get("departmentId") is not None for r in rows),
          f"rows={len(rows)} with_dept={sum(1 for r in rows if r.get('departmentId') is not None)}")

    return summary()


if __name__ == "__main__":
    sys.exit(main())
