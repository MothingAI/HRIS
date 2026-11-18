#!/usr/bin/env python3
"""S2 build: 集合、角色、账号、种子数据、服务端数据权限。

幂等：已存在的集合/角色/账号会跳过；可重复执行不产生重复数据。
只使用 NocoBase 官方 API（collections / fields / roles / users /
dataSourcesRolesResources*），不直接写系统表。

用法：
  python scripts/build_s2.py
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
    return env


ENV = load_env()
ADMIN = None


def log(msg):
    print(f"[S2] {msg}", flush=True)


def q(params):
    """Return params dict; serialize filter to JSON string if present."""
    out = dict(params)
    if "filter" in out and not isinstance(out["filter"], str):
        out["filter"] = json.dumps(out["filter"], ensure_ascii=False)
    return out


# ---- collections ----------------------------------------------------------

def ensure_collection(name, title, fields):
    r = ADMIN.call("GET", "/api/collections:get", params={"filterByTk": name})
    if r.get("data"):
        log(f"集合已存在: {name} (跳过)")
        return False
    body = {"name": name, "title": title, "fields": fields}
    r = ADMIN.call("POST", "/api/collections:create", json_body=body)
    if not r.get("data"):
        log(f"创建集合失败: {name} -> {json.dumps(r, ensure_ascii=False)[:400]}")
        raise SystemExit(1)
    log(f"创建集合: {name}")
    return True


def field_string(name, interface="input", **opts):
    return {"name": name, "type": "string", "interface": interface, **opts}


def field_text(name, interface="textarea", **opts):
    return {"name": name, "type": "text", "interface": interface, **opts}


def field_belongs(name, target, foreign_key=None, target_key="id", interface="m2o", **opts):
    fk = foreign_key or (name + "Id")
    return {"name": name, "type": "belongsTo", "interface": interface,
            "target": target, "foreignKey": fk, "targetKey": target_key, **opts}


def build_collections():
    log("== 建立集合 ==")

    # 部门
    ensure_collection("departments", "部门", [
        field_string("name", "input", allowNull=False),
        field_string("code", "input", unique=True, allowNull=False),
    ])

    # 员工
    ensure_collection("employees", "员工", [
        field_string("employeeNo", "input", unique=True, allowNull=False),
        field_string("name", "input", allowNull=False),
        field_belongs("department", "departments"),
        field_string("jobTitle", "input"),
        field_belongs("manager", "employees"),          # 自关联：直属主管
        field_belongs("user", "users", unique=True),    # 账号绑定
        {"name": "hireDate", "type": "dateOnly", "interface": "date"},
        {"name": "probationEndDate", "type": "dateOnly", "interface": "date"},
        field_string("status", "select", defaultValue="PROBATION"),
    ])

    # 转正申请
    ensure_collection("confirmation_requests", "转正申请", [
        field_belongs("employee", "employees"),
        field_belongs("approver", "users"),             # 待审批人 = 主管账号
        field_string("status", "select", defaultValue="DRAFT"),
        field_text("selfReview", "textarea"),
        {"name": "submittedAt", "type": "date", "interface": "datetime"},
        {"name": "decidedAt", "type": "date", "interface": "datetime"},
        field_text("decisionComment", "textarea"),
        {"name": "version", "type": "integer", "interface": "integer", "defaultValue": 0},
    ])

    # 审批轨迹（追加只读）
    ensure_collection("approval_actions", "审批轨迹", [
        field_belongs("request", "confirmation_requests"),
        field_belongs("actorUser", "users"),
        field_string("action", "select"),
        field_text("comment", "textarea"),
    ])

    log("== 集合建立完成 ==")


def normalize_null_role_action_fields(collection):
    """把集合上 fields=null 的角色动作归一化为显式字段列表（规避 fields:create 钩子缺陷）。

    NocoBase 2.2.37 的 fields:create 会触发 fields.afterCreate ACL 钩子，钩子里对每个
    角色动作执行 [...fields, 新字段]；当角色动作 fields 为 null（“全部字段”）时会抛
    “fields is not iterable” 并整体回滚。归一化后语义仍为“所有字段 + 新字段”。
    """
    cur = [f["name"] for f in ADMIN.call(
        "GET", "/api/fields:list",
        params=q({"filter": {"collectionName": collection}})).get("data", [])]
    res = ADMIN.call("GET", "/api/dataSourcesRolesResources:list",
                     params=q({"filter": {"name": collection}}))
    changed = 0
    for r_ in res.get("data", []):
        rid = r_["id"]
        acts = ADMIN.call("GET", "/api/dataSourcesRolesResourcesActions:list",
                          params=q({"filter": {"rolesResourceId": rid}}))
        for a in acts.get("data", []):
            if a.get("fields") is None:
                ADMIN.call("POST", "/api/dataSourcesRolesResourcesActions:update",
                           params={"filterByTk": a["id"]}, json_body={"fields": cur})
                changed += 1
        ADMIN.call("POST", "/api/dataSourcesRolesResources:update",
                   params={"filterByTk": rid}, json_body={"usingActionsConfig": True})
    if changed:
        log(f"归一化 {collection} 的 {changed} 个 null 字段动作 -> 显式列表")
    return changed


def ensure_field(collection, body):
    """幂等新增集合字段（先做 null 归一化规避平台缺陷）。已存在则跳过。"""
    r = ADMIN.call("GET", "/api/fields:list",
                   params=q({"filter": {"collectionName": collection,
                                        "name": body["name"]}}))
    if r.get("data"):
        log(f"字段已存在: {collection}.{body['name']} (跳过)")
        return False
    normalize_null_role_action_fields(collection)
    r = ADMIN.call("POST", "/api/fields:create", json_body={
        "collectionName": collection, **body})
    if not r.get("data"):
        log(f"创建字段失败: {collection}.{body['name']} -> "
            f"{json.dumps(r, ensure_ascii=False)[:300]}")
        raise SystemExit(1)
    log(f"创建字段: {collection}.{body['name']}")
    return True


def ensure_o2m_relation():
    """确认 confirmation_requests.approvalActions 反向 o2m 关系（轨迹抽屉的关联表格依赖）。

    幂等：字段已存在则跳过。新建前先把 confirmation_requests 上 fields=null 的角色动作
    归一化为显式字段列表（NocoBase 2.2.37 fields:create 的 afterCreate ACL 钩子对
    null 执行 [...null, 新字段] 会抛错回滚，同 docs/demo/LOWCODE-DEMO.md 记录的 quirks）。
    """
    ensure_field("confirmation_requests", {
        "name": "approvalActions",
        "type": "hasMany",
        "interface": "o2m",
        "target": "approval_actions",
        "foreignKey": "requestId",
        "targetKey": "id",
        "title": "审批轨迹",
    })


def ensure_acted_at():
    """审批轨迹时间列：approval_actions.actedAt（系统 createdAt 不在集合字段中暴露，
    无法配置列元数据；显式字段由插件在写入轨迹时填充）。"""
    ensure_field("approval_actions", {
        "name": "actedAt",
        "type": "date",
        "interface": "datetime",
        "title": "时间",
    })


# ---- roles ----------------------------------------------------------------

def ensure_role(name, title):
    r = ADMIN.call("GET", "/api/roles:get", params={"filterByTk": name})
    if r.get("data"):
        log(f"角色已存在: {name} (跳过)")
        return
    r = ADMIN.call("POST", "/api/roles:create", json_body={"name": name, "title": title})
    if not r.get("data"):
        log(f"创建角色失败: {name} -> {json.dumps(r, ensure_ascii=False)[:400]}")
        raise SystemExit(1)
    log(f"创建角色: {name}")


def build_roles():
    log("== 建立角色 ==")
    ensure_role("hr", "HR")
    ensure_role("manager", "主管")
    ensure_role("employee", "员工")
    log("== 角色建立完成 ==")


# ---- seed data ------------------------------------------------------------

def ensure_department(name, code):
    r = ADMIN.call("GET", "/api/departments:list", params=q({"filter": {"code": code}}))
    if r.get("data"):
        return r["data"][0]
    r = ADMIN.call("POST", "/api/departments:create", json_body={"name": name, "code": code})
    d = r.get("data")
    log(f"创建部门: {name} ({code})")
    return d


def ensure_user(email, password, nickname, username=None):
    r = ADMIN.call("GET", "/api/users:list", params=q({"filter": {"email": email}}))
    if r.get("data"):
        return r["data"][0]
    r = ADMIN.create_user(email, password, nickname, username or email.split("@")[0])
    u = r.get("data")
    log(f"创建用户: {nickname} <{email}> id={u.get('id') if u else None}")
    return u


def set_user_roles(user_id, role_names):
    # 先清空现有角色再设置，避免叠加默认 member
    roles = [{"name": n} for n in role_names]
    r = ADMIN.call("POST", "/api/users:update", params={"filterByTk": user_id},
                   json_body={"roles": roles})
    return r


# 员工种子（20 人：试用 8 / 在职 10 / 离职 2）
EMPLOYEES = [
    # (工号, 姓名, 部门code, 岗位, 主管工号, 状态)
    ("E001", "王建国", "TECH", "技术部经理", None, "REGULAR"),
    ("E002", "李思远", "TECH", "软件工程师", "E001", "PROBATION"),
    ("E003", "张敏", "TECH", "前端工程师", "E001", "PROBATION"),
    ("E004", "刘洋", "TECH", "后端工程师", "E001", "REGULAR"),
    ("E005", "陈静", "TECH", "测试工程师", "E001", "REGULAR"),
    ("E006", "赵磊", "TECH", "软件工程师", "E001", "PROBATION"),
    ("E007", "孙悦", "TECH", "运维工程师", "E001", "LEFT"),
    ("E008", "周涛", "TECH", "架构师", "E001", "REGULAR"),
    ("E009", "林芳", "PROD", "产品部经理", None, "REGULAR"),
    ("E010", "吴昊", "PROD", "产品经理", "E009", "PROBATION"),
    ("E011", "郑雪", "PROD", "产品经理", "E009", "PROBATION"),
    ("E012", "黄强", "PROD", "交互设计师", "E009", "REGULAR"),
    ("E013", "徐丽", "PROD", "UI设计师", "E009", "REGULAR"),
    ("E014", "何军", "PROD", "产品经理", "E009", "PROBATION"),
    ("E015", "高媛", "PROD", "产品运营", "E009", "REGULAR"),
    ("E016", "罗成", "MKT", "市场经理", None, "REGULAR"),
    ("E017", "邓超", "MKT", "市场专员", "E016", "REGULAR"),
    ("E018", "蒋雯", "MKT", "市场专员", "E016", "LEFT"),
    ("E019", "马兰", "MKT", "市场专员", "E016", "PROBATION"),
    ("E020", "沈涛", "MKT", "市场专员", "E016", "PROBATION"),
]

# 演示账号（虚构保留域名 example.com）
DEMO_USERS = {
    "hr": ("hr@example.com", "沈玉兰", "hr"),
    "manager_a": ("manager.a@example.com", "王建国", "manager"),
    "manager_b": ("manager.b@example.com", "林芳", "manager"),
    "employee_a": ("employee.a@example.com", "李思远", "employee"),
    "employee_b": ("employee.b@example.com", "郑雪", "employee"),
}

# 演示账号密码必须从本地配置提供，缺失时停止，避免硬编码凭据入库。
DEMO_PASSWORD = ENV["DEMO_PASSWORD"]


def build_seed():
    log("== 建立种子数据 ==")

    deps = {
        "TECH": ensure_department("技术部", "TECH"),
        "PROD": ensure_department("产品部", "PROD"),
        "MKT": ensure_department("市场部", "MKT"),
    }

    emp_by_no = {}
    for (no, name, dept_code, job, mgr_no, status) in EMPLOYEES:
        r = ADMIN.call("GET", "/api/employees:list", params=q({"filter": {"employeeNo": no}}))
        if r.get("data"):
            emp = r["data"][0]
            log(f"员工已存在: {no} {name} (跳过)")
        else:
            body = {
                "employeeNo": no,
                "name": name,
                "departmentId": deps[dept_code]["id"],
                "jobTitle": job,
                "status": status,
            }
            if mgr_no:
                body["managerId"] = emp_by_no[mgr_no]["id"]
            r = ADMIN.call("POST", "/api/employees:create", json_body=body)
            emp = r.get("data")
            if not emp:
                log(f"创建员工失败: {no} {name} -> {json.dumps(r, ensure_ascii=False)[:400]}")
                raise SystemExit(1)
            log(f"创建员工: {no} {name} [{status}]")
        emp_by_no[no] = emp

    # 演示账号 + 角色 + 员工绑定
    bindings = {
        "hr": None,                      # HR 不绑定员工
        "manager_a": "E001",
        "manager_b": "E009",
        "employee_a": "E002",
        "employee_b": "E011",
    }
    users = {}
    for key, (email, nickname, role) in DEMO_USERS.items():
        u = ensure_user(email, DEMO_PASSWORD, nickname)
        set_user_roles(u["id"], [role])
        users[key] = u
        # 绑定员工 <-> 账号
        no = bindings[key]
        if no:
            ADMIN.call("POST", "/api/employees:update",
                       params={"filterByTk": emp_by_no[no]["id"]},
                       json_body={"userId": u["id"]})
            log(f"绑定: {nickname}({no}) <-> {email}")

    log("== 种子数据完成 ==")
    return emp_by_no, users


# ---- ACL ------------------------------------------------------------------

def scope_create(key, name, scope):
    r = ADMIN.call("GET", "/api/dataSourcesRolesResourcesScopes:list",
                   params=q({"filter": {"key": key}}))
    if r.get("data"):
        s = r["data"][0]
        if s.get("scope") != scope:
            ADMIN.call("POST", "/api/dataSourcesRolesResourcesScopes:update",
                       params={"filterByTk": s["id"]}, json_body={"scope": scope, "name": name})
            log(f"更新范围: {key}")
        return s["id"]
    r = ADMIN.call("POST", "/api/dataSourcesRolesResourcesScopes:create",
                   json_body={"key": key, "name": name, "dataSourceKey": "main", "scope": scope})
    log(f"创建范围: {key}")
    return r["data"]["id"]


def resource_grant(role, collection, actions):
    """为角色在某集合上授予动作（含 scope 与 fields 白名单）。

    actions: [{"name": "view", "scopeKey": "emp_self" | None, "fields": [...] | None}, ...]
    scopeKey=None 表示不设数据范围（scope={} 全量）。
    """
    # 找到或创建 resource 条目
    r = ADMIN.call("GET", "/api/dataSourcesRolesResources:list",
                   params=q({"filter": {"roleName": role, "name": collection}}))
    if r.get("data"):
        res = r["data"][0]
    else:
        r = ADMIN.call("POST", "/api/dataSourcesRolesResources:create",
                       json_body={"dataSourceKey": "main", "roleName": role,
                                  "name": collection, "usingActionsConfig": True})
        res = r.get("data")
        if not res:
            log(f"授权失败: {role}/{collection} -> {json.dumps(r, ensure_ascii=False)[:400]}")
            raise SystemExit(1)
        log(f"授权资源: {role} -> {collection}")

    rid = res["id"]

    # 现有动作
    r = ADMIN.call("GET", "/api/dataSourcesRolesResourcesActions:list",
                   params=q({"filter": {"rolesResourceId": rid}}))
    existing = {a["name"]: a for a in r.get("data", [])}

    for act in actions:
        act_name = act["name"]
        body = {}
        if act.get("scopeKey"):
            body["scopeId"] = scope_create(act["scopeKey"], act["scopeKey"],
                                           SCOPES[act["scopeKey"]])
        else:
            body["scopeId"] = None
        # fields: None -> null (全部字段)；list -> 字段白名单。务必显式写入，
        # 否则集合默认值 [] 会把动作解释成“无任何字段”。
        fields = act.get("fields")
        if act_name in existing:
            stored = existing[act_name].get("fields")
            if fields is not None and isinstance(stored, list):
                # 幂等重跑不冲掉后来加进白名单的字段（如 fields:create ACL 钩子
                # 自动追加的管理员新字段）：规范列表 + 集合里仍存在的额外字段
                extra = [f for f in stored if f not in fields]
                if extra:
                    cur = [f["name"] for f in ADMIN.call(
                        "GET", "/api/fields:list",
                        params=q({"filter": {"collectionName": collection}}
                                 )).get("data", [])]
                    kept = [f for f in extra if f in cur]
                    if kept:
                        fields = fields + kept
                        log(f"保留白名单额外字段: {role}/{collection}.{act_name} +{kept}")
        body["fields"] = fields
        if act_name in existing:
            ADMIN.call("POST", "/api/dataSourcesRolesResourcesActions:update",
                       params={"filterByTk": existing[act_name]["id"]}, json_body=body)
            log(f"更新动作: {role}/{collection}:{act_name} fields={act.get('fields')}")
        else:
            ADMIN.call("POST", "/api/dataSourcesRolesResourcesActions:create",
                       json_body={"rolesResourceId": rid, "name": act_name, **body})
            log(f"创建动作: {role}/{collection}:{act_name} fields={act.get('fields')}")

    # 关键：动作 create 不会触发 ACL 重载（只监听 afterUpdateWithAssociations），
    # 需要更新一次 resource 以触发 afterSaveWithAssociations -> writeToACL。
    ADMIN.call("POST", "/api/dataSourcesRolesResources:update",
               params={"filterByTk": rid}, json_body={"usingActionsConfig": True})


SCOPES = {
    "emp_self": {"userId": "{{ ctx.state.currentUser.id }}"},
    "emp_manager": {"$or": [
        {"userId": "{{ ctx.state.currentUser.id }}"},
        {"manager.userId": "{{ ctx.state.currentUser.id }}"},
    ]},
    "req_self": {"employee.userId": "{{ ctx.state.currentUser.id }}"},
    "req_manager": {"$or": [
        {"employee.userId": "{{ ctx.state.currentUser.id }}"},
        {"approverId": "{{ ctx.state.currentUser.id }}"},
    ]},
    "act_self": {"request.employee.userId": "{{ ctx.state.currentUser.id }}"},
    "act_manager": {"$or": [
        {"request.employee.userId": "{{ ctx.state.currentUser.id }}"},
        {"request.approverId": "{{ ctx.state.currentUser.id }}"},
    ]},
}


def build_acl():
    log("== 配置数据权限 ==")

    # 员工角色
    resource_grant("employee", "departments", [
        {"name": "view"},
    ])
    resource_grant("employee", "employees", [
        {"name": "view", "scopeKey": "emp_self"},
    ])
    # 员工对申请只有“读本人”权限；写操作全部走 S3 受控动作（saveDraft/submit），
    # 不授予默认 create（否则可绕过受控动作伪造 status/approver/employee）。
    # build_s3.py 会防御性地删除历史遗留的 employee/confirmation_requests:create。
    resource_grant("employee", "confirmation_requests", [
        {"name": "view", "scopeKey": "req_self"},
    ])
    resource_grant("employee", "approval_actions", [
        {"name": "view", "scopeKey": "act_self"},
    ])

    # 主管角色
    resource_grant("manager", "departments", [
        {"name": "view"},
    ])
    resource_grant("manager", "employees", [
        {"name": "view", "scopeKey": "emp_manager"},
    ])
    resource_grant("manager", "confirmation_requests", [
        {"name": "view", "scopeKey": "req_manager"},
    ])
    resource_grant("manager", "approval_actions", [
        {"name": "view", "scopeKey": "act_manager"},
    ])

    # HR 角色（员工档案全量可读，但更新不含 status）
    HR_EDITABLE_FIELDS = ["employeeNo", "name", "department", "jobTitle",
                          "manager", "user", "hireDate", "probationEndDate"]
    resource_grant("hr", "departments", [
        {"name": "view"},
        {"name": "create"},
        {"name": "update"},
    ])
    resource_grant("hr", "employees", [
        {"name": "view"},
        {"name": "create", "fields": HR_EDITABLE_FIELDS},
        {"name": "update", "fields": HR_EDITABLE_FIELDS},
    ])
    resource_grant("hr", "confirmation_requests", [
        {"name": "view"},
    ])
    resource_grant("hr", "approval_actions", [
        {"name": "view"},
    ])

    log("== 数据权限配置完成 ==")


# ---- main -----------------------------------------------------------------

def main():
    global ADMIN
    os.environ.setdefault("NB_BASE", ENV.get("NB_BASE", "http://127.0.0.1:13000"))
    ADMIN = NbClient(email=ENV.get("INIT_ROOT_EMAIL"),
                     password=ENV.get("INIT_ROOT_PASSWORD"))
    ADMIN.signin()
    log(f"已登录管理员: {ADMIN.user.get('nickname')}")

    build_collections()
    ensure_o2m_relation()
    ensure_acted_at()
    build_roles()
    emp_by_no, users = build_seed()
    build_acl()

    log("== S2 构建完成 ==")
    log(f"员工总数: {len(emp_by_no)}")
    log("演示账号: " + ", ".join(f"{k}={v[0]}" for k, v in DEMO_USERS.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
