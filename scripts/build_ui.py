#!/usr/bin/env python3
"""S2.3 / S3.1 / S4.1 业务页面与角色菜单配置（通过受支持配置 API，幂等）。

⚠️ 2026-11-10 起本脚本只负责【路由骨架与角色菜单】（desktopRoutes page/tabs
+ rolesDesktopRoutes）。**页面内容 schema 已由 scripts/build_pages_v2.py 接管**
（官方形态：tabs.schemaUid=Grid 内容节点 + 官方区块模板 + 受控动作按钮接线；
本脚本旧版生成的页面形态是 REV-001 渲染崩溃根因，勿再用于建页面内容）。

完整页面构建顺序：
  1. python scripts/build_ui.py          # 路由 + 角色菜单（幂等）
  2. python scripts/fix_field_meta.py    # 集合字段中文标题 + uiSchema（列头/单元格渲染依赖）
  3. python scripts/build_pages_v2.py    # 页面 schema（幂等，--rebuild <标题> 可强制重建）

对应 REV-001：为员工管理、我的档案、我的转正、待我审批、人事概览、部门管理
配置可登录的业务页面，并把菜单权限赋给对应角色。

实现方式（均为 NocoBase 官方 API，不直接写系统表）：
- 页面内容：`POST /api/uiSchemas:insertNewSchema`，传入一棵嵌套 schema 树，
  服务端自动生成 x-uid 并落库（ui-schema-storage 插件标准动作）。
- 菜单项：`POST /api/desktopRoutes:create`，`menuSchemaUid` 指向页面根 schema。
- 菜单权限：`desktopRoutes.roles` 多对多关联（through rolesDesktopRoutes）。

幂等：按 route 的 title 去重；已存在的页面 / 菜单跳过。

用法：
  python scripts/build_ui.py
从 .env 读取 NB_BASE / INIT_ROOT_EMAIL / INIT_ROOT_PASSWORD。
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
    for k, v in os.environ.items():
        if v:
            env[k] = v
    return env


ENV = load_env()
ADMIN = None


def log(msg):
    print(f"[UI] {msg}", flush=True)


# ---- schema 构造（NocoBase 2.x 标准节点树：properties 下嵌套子节点） ----

def _block(title, component, props=None, decorator=None, decorator_props=None,
           use_decorator_props=None, use_component_props=None, initializer=None,
           properties=None):
    node = {"type": "void", "title": title, "x-component": component}
    if props:
        node["x-component-props"] = props
    if decorator:
        node["x-decorator"] = decorator
    if decorator_props:
        node["x-decorator-props"] = decorator_props
    if use_decorator_props:
        node["x-use-decorator-props"] = use_decorator_props
    if use_component_props:
        node["x-use-component-props"] = use_component_props
    if initializer:
        node["x-initializer"] = initializer
    if properties:
        node["properties"] = properties
    return node


def table_column(data_index, title, width=None):
    props = {"dataIndex": [data_index], "title": title}
    if width:
        props["width"] = width
    return _block(title, "TableV2.Column", props=props)


def table_block(collection, columns, add_action=False):
    """构造一个数据表格区块（TableBlockProvider + TableV2 + 列）。"""
    table = _block(
        "表格", "TableV2",
        use_component_props="useTableBlockProps",
        initializer="table:configureColumns",
        properties={("col_" + c[0]): table_column(c[0], c[1], c[2]) for c in columns},
    )
    props = {
        "collection": collection,
        "action": "list",
        "params": {"pageSize": 20},
        "rowKey": "id",
        "showIndex": True,
    }
    properties = {"table": table}
    if add_action:
        add_btn = _block(
            "新增", "Action",
            props={"type": "primary"},
            properties={},
        )
        add_btn.update({"x-action": "create", "x-align": "right", "x-component": "Action.Link"})
        properties["actions"] = _block(
            "操作栏", "ActionBar",
            initializer="table:configureActions",
            properties={"add": add_btn},
        )
    return _block(
        collection, "TableV2",
        decorator="TableBlockProvider",
        decorator_props=props,
        use_decorator_props="useTableBlockDecoratorProps",
        properties=properties,
    )


def form_item(field, title, required=False):
    node = _block(title, "CollectionField",
                  decorator="FormItem",
                  properties={"field": {"type": "string", "title": title, "name": field}})
    node["x-component-props"] = {"name": field}
    if required:
        node["x-decorator-props"] = {"rules": [{"required": True}]}
    return node


def form_block(collection, fields, action="get"):
    form = _block(
        "表单", "FormV2",
        use_component_props="useCreateFormBlockProps" if action == "create" else "useFormBlockProps",
        properties={("f_" + f): form_item(f, t) for f, t in fields},
    )
    props = {"collection": collection, "action": action}
    return _block(
        collection, "FormV2",
        decorator="FormBlockProvider",
        decorator_props=props,
        use_decorator_props="useCreateFormBlockDecoratorProps" if action == "create" else "useFormBlockDecoratorProps",
        properties={"form": form},
    )


def page(title, blocks):
    """构造一个页面：Page -> Grid -> Grid.Row -> Grid.Col -> 区块。"""
    cols = {}
    for i, b in enumerate(blocks):
        cols["col_%d" % i] = _block("列", "Grid.Col", properties={"block_%d" % i: b})
    row = _block("行", "Grid.Row", properties=cols)
    grid = _block("栅格", "Grid", initializer="page:addBlock", properties={"row": row})
    return _block(title, "Page", properties={"grid": grid})


# ---- 页面 / 菜单定义（角色 -> 可见菜单） ----

PAGES = [
    # (title, type, icon, 角色列表, schema 构造器参数)
    ("员工管理", "page", "TeamOutlined", ["hr"],
     lambda: page("员工管理", [table_block("employees", [
         ("employeeNo", "工号", 120), ("name", "姓名", 120),
         ("department", "部门", 140), ("jobTitle", "岗位", 140),
         ("manager", "直属主管", 140), ("hireDate", "入职日期", 140),
         ("probationEndDate", "试用期结束", 140), ("status", "任职状态", 120),
     ])])),
    ("部门管理", "page", "ApartmentOutlined", ["hr"],
     lambda: page("部门管理", [table_block("departments", [
         ("name", "部门名称", 160), ("code", "部门编码", 160),
     ])])),
    ("我的档案", "page", "UserOutlined", ["employee", "manager"],
     lambda: page("我的档案", [table_block("employees", [
         ("employeeNo", "工号", 120), ("name", "姓名", 120),
         ("department", "部门", 140), ("jobTitle", "岗位", 140),
         ("manager", "直属主管", 140), ("status", "任职状态", 120),
     ])])),
    ("我的转正", "page", "FileDoneOutlined", ["employee"],
     lambda: page("我的转正", [form_block("confirmation_requests", [
         ("selfReview", "自我评价"), ("trialHarvest", "试用期收获"),
     ], action="create")])),
    ("待我审批", "page", "AuditOutlined", ["manager"],
     lambda: page("待我审批", [table_block("confirmation_requests", [
         ("employee", "员工", 160), ("status", "状态", 120),
         ("selfReview", "自我评价", 200), ("submittedAt", "提交时间", 160),
     ])])),
    ("人事概览", "page", "DashboardOutlined", ["hr"],
     lambda: page("人事概览", [table_block("employees", [
         ("employeeNo", "工号", 120), ("name", "姓名", 120),
         ("department", "部门", 140), ("status", "任职状态", 120),
     ])])),
]


def route_exists(title):
    r = ADMIN.call("GET", "/api/desktopRoutes:list",
                   params={"filter": json.dumps({"title": title})})
    return len(r.get("data", [])) > 0


def role_names():
    r = ADMIN.call("GET", "/api/roles:list", params={"pageSize": 50})
    return {d["name"]: d for d in r.get("data", [])}


def main():
    global ADMIN
    os.environ.setdefault("NB_BASE", ENV.get("NB_BASE", "http://127.0.0.1:13000"))
    ADMIN = NbClient(email=ENV.get("INIT_ROOT_EMAIL"), password=ENV.get("INIT_ROOT_PASSWORD"))
    ADMIN.signin()
    log(f"已登录管理员: {ADMIN.user.get('nickname')} @ {ADMIN.base}")

    roles = role_names()

    created = 0
    for title, rtype, icon, allowed_roles, schema_builder in PAGES:
        if route_exists(title):
            log(f"菜单已存在，跳过: {title}")
            continue

        # 1) 创建页面 schema，取回根 x-uid
        schema = schema_builder()
        r = ADMIN.call("POST", "/api/uiSchemas:insertNewSchema", json_body=schema)
        root_uid = (r.get("data") or {}).get("x-uid")
        if not root_uid:
            log(f"创建页面 schema 失败: {title} -> {json.dumps(r, ensure_ascii=False)[:300]}")
            raise SystemExit(1)

        # 2) 创建菜单并赋角色（菜单权限 = desktopRoutes.roles）
        role_refs = []
        for rn in allowed_roles:
            if rn in roles:
                role_refs.append({"name": rn})
            else:
                log(f"警告: 角色不存在，忽略 {rn}（页面 {title}）")
        body = {
            "type": rtype,
            "title": title,
            "icon": icon,
            "menuSchemaUid": root_uid,
            "sort": 100 + created,
        }
        if role_refs:
            body["roles"] = role_refs
        r = ADMIN.call("POST", "/api/desktopRoutes:create", json_body=body)
        if not r.get("data"):
            log(f"创建菜单失败: {title} -> {json.dumps(r, ensure_ascii=False)[:300]}")
            raise SystemExit(1)
        created += 1
        log(f"已创建: {title} (schema={root_uid}, roles={allowed_roles})")

    # 3) 结构级自检：菜单数量、menuSchemaUid 可解析、角色菜单可见性
    routes = ADMIN.call("GET", "/api/desktopRoutes:list",
                        params={"pageSize": 100, "appends": ["roles"]}).get("data", [])
    log(f"desktopRoutes 总数: {len(routes)}")
    for rt in routes:
        uid = rt.get("menuSchemaUid")
        resolved = False
        if uid:
            js = ADMIN.call("GET", "/api/uiSchemas:getJsonSchema",
                            params={"resourceIndex": uid})
            resolved = bool(js.get("data"))
        log(f"  菜单: {rt.get('title')} type={rt.get('type')} "
            f"roles={[x.get('name') for x in rt.get('roles', [])]} schema_resolved={resolved}")

    # 4) 角色菜单可见性（客户端渲染菜单用的权威动作 desktopRoutes:listAccessible）
    pw = ENV.get("DEMO_PASSWORD")
    if pw:
        for email in ("employee.a@example.com", "manager.a@example.com", "hr@example.com"):
            c = NbClient(email=email, password=pw)
            c.signin()
            r = c.req("GET", "/api/desktopRoutes:listAccessible", params={"pageSize": 100})
            titles = [x.get("title") for x in (r.json().get("data", []) if r.status_code == 200 else [])]
            log(f"  可见菜单 {email}: {titles}")

    log(f"== 业务页面与菜单配置完成（结构级，共创建 {created} 个）==")
    log("提示：可视化渲染与表单/动作接线需在浏览器中验收（见 BLACKBOARD.md REV-001）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
