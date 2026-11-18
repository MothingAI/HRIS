#!/usr/bin/env python3
"""演示数据准备（REV-010 第二轮重写）：所有权=受保护工具清单，不再看可编辑字段。

旧版缺陷（2026-11-18 Codex 复核）：所有权判定只看 employeeNo 是否匹配
DEMO-\\d{2}——该字段 HR 可改。普通员工被改号成 DEMO-90 后：--blank 会删其手工
申请；默认模式会把其原账号新建成工具账号并【改绑】。可编辑业务字段不能当
所有权凭据。

本版规则：
1. 工具清单 prepDemoRegistry（独立集合，仅 root 可访问——未授予任何角色
   ACL，不在任何页面）：登记本工具实际创建的员工/账号/申请 ID。所有权
   = 清单登记，employeeNo/email 一律不作为凭据。
2. 碰撞处理（先拒绝后顺延，绝不接管）：
   - 分配 DEMO-NN 时工号或 demoNN@example.com 已被【非清单】记录占用 →
     跳到下一编号并记录原因，不改其角色、不改绑、不删除；
   - 清单员工的绑定被人工改动（userId ≠ 清单账号）→ 不回绑，换新身份；
   - 清单员工名下的手工申请（不在清单）→ 永不删除；默认模式遇之不顺延
     复用该身份（避免 409），改用下一身份。
3. 两条互斥演示路径（REV-012）：
   - 默认【预填待办】：确保恰有一条清单身份的 PENDING 待审批——已有则
     直接复用（演示跳过第 2 步，从主管审批开始）；上一轮批/驳完 → 顺延
     新身份再提交。历史一律保留。
   - --blank【空白开始】：只删【清单登记】的在途申请（DRAFT/PENDING），
     并确保一个可登录、试用中、无任何申请的专属身份，打印其账号——
     演示完整走第 2 步（新建草稿→编辑→提交）。
4. 收尾打印实时统计（员工总数/在职/试用/待审批/离职/已批/已驳）与当前
   演示账号——演示话术从本输出现场取数，不写死数字。

用法：
  python scripts/prep_demo.py            # 预填待办（幂等；直接审批路径）
  python scripts/prep_demo.py --blank    # 空白开始（全流程路径；输出演示账号）
退出码：0 = 就绪；1 = 存在失败。
"""
import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nb import NbClient  # noqa: E402
from verify_common import check, cleanup_requests, jf, load_env, signed, summary  # noqa: E402

TOOL_NO_RE = re.compile(r"^DEMO-(\d{2})$")
MGR_EMAIL = "manager.a@example.com"
DEPT_CODE = "TECH"
MGR_EMP_NO = "E001"  # 王建国 = manager.a，主管待办出在这

# 工具清单集合（REV-010）：仅 root 可访问（无任何角色 ACL 授权，不在页面）。
REGISTRY = "prepDemoRegistry"


def log(msg):
    print(f"[prep] {msg}", flush=True)


# ---- 工具清单 ------------------------------------------------------------


def ensure_registry(admin):
    """确保清单集合存在（幂等）。未授予任何角色动作 → hr/employee/manager 均不可访问。"""
    r = admin.call("GET", "/api/collections:get", params={"filterByTk": REGISTRY})
    if r.get("data"):
        return
    body = {
        "name": REGISTRY, "title": "演示准备工具清单（勿动）",
        "fields": [
            {"name": "kind", "type": "string", "interface": "input", "title": "类型"},
            {"name": "refId", "type": "bigInt", "interface": "number", "title": "记录ID"},
            {"name": "label", "type": "string", "interface": "input", "title": "标识"},
        ],
    }
    r = admin.call("POST", "/api/collections:create", json_body=body)
    if not r.get("data"):
        raise RuntimeError(f"创建工具清单集合失败: {json.dumps(r, ensure_ascii=False)[:200]}")
    log(f"工具清单集合已创建: {REGISTRY}（仅 root 可访问）")


def registry_map(admin, kind):
    """清单中某类型的 {refId: label}。"""
    rows = admin.call("GET", f"/api/{REGISTRY}:list",
                      params={"filter": jf({"kind": kind}), "pageSize": 500}).get("data", [])
    return {int(r["refId"]): r["label"] for r in rows}


def register(admin, kind, ref_id, label):
    admin.call("POST", f"/api/{REGISTRY}:create",
               json_body={"kind": kind, "refId": ref_id, "label": label})


def unregister(admin, kind, ref_id):
    rows = admin.call("GET", f"/api/{REGISTRY}:list",
                      params={"filter": jf({"kind": kind, "refId": ref_id})}).get("data", [])
    for r in rows:
        admin.call("POST", f"/api/{REGISTRY}:destroy", params={"filterByTk": r["id"]})


# ---- 身份与申请 ------------------------------------------------------------


def nn_of(label):
    m = TOOL_NO_RE.match(label or "")
    return int(m.group(1)) if m else None


def owned_identities(admin):
    """清单登记的身份，按编号降序：[{nn, label(DEMO-NN), empId, userId(登记账号 id)}]，
    附当前员工实际状态（存在性/status/当前绑定 user）。"""
    emp_reg = registry_map(admin, "employee")        # refId=员工id -> label=DEMO-NN
    user_by_label = {v: k for k, v in registry_map(admin, "user").items()}  # DEMO-NN -> 账号id
    ids = list(emp_reg.keys())
    emps = []
    if ids:
        rows = admin.call("GET", "/api/employees:list",
                          params={"filter": jf({"id": {"$in": ids}}),
                                  "pageSize": 500, "appends": ["user"]}).get("data", [])
        by_id = {r["id"]: r for r in rows}
        for eid, label in emp_reg.items():
            nn = nn_of(label)
            if nn is None:
                continue
            e = by_id.get(eid, {})
            emps.append({
                "nn": nn, "label": label, "empId": eid,
                "userId": user_by_label.get(label),   # 清单登记的账号 id
                "emp": e,                               # 当前员工行（可能已不存在）
                "boundUserId": (e.get("user") or {}).get("id"),
            })
    emps.sort(key=lambda x: -x["nn"])
    return emps


def requests_of(admin, emp_ids):
    if not emp_ids:
        return []
    return admin.call("GET", "/api/confirmation_requests:list",
                      params={"filter": jf({"employeeId": {"$in": emp_ids}}),
                              "pageSize": 500}).get("data", [])


def identity_usable(ident, reqs_of_emp):
    """身份可复用条件：员工在库 + PROBATION + 绑定=清单账号 + 名下无任何申请。"""
    e = ident["emp"]
    if not e or e.get("status") != "PROBATION":
        return False
    if not ident["userId"] or ident["boundUserId"] != ident["userId"]:
        return False
    return not reqs_of_emp


def alloc_nn(admin, identities):
    """分配下一个编号：从 清单最大编号+1（空清单=1）起，工号/邮箱均无占用即用。
    被【非清单】记录占用的编号跳过并记录原因（不接管、不改绑）。"""
    start = max((i["nn"] for i in identities), default=0) + 1
    for nn in range(start, 100):
        no, email = f"DEMO-{nn:02d}", f"demo{nn:02d}@example.com"
        why = []
        if admin.call("GET", "/api/employees:list",
                      params={"filter": jf({"employeeNo": no})}).get("data"):
            why.append(f"工号 {no} 已被占用")
        if admin.call("GET", "/api/users:list",
                      params={"filter": jf({"email": email})}).get("data"):
            why.append(f"邮箱 {email} 已被占用")
        if why:
            log(f"编号 {nn:02d} 碰撞跳过（{'；'.join(why)}，不接管）")
            continue
        return nn
    raise RuntimeError("DEMO-01..99 编号全部被占用，无法分配新身份")


def create_identity(admin, env, nn):
    """创建第 nn 号工具身份（员工+账号+绑定+清单登记），返回 (employee, user)。"""
    no, email = f"DEMO-{nn:02d}", f"demo{nn:02d}@example.com"
    name = f"演示员工{nn:02d}"
    dept = admin.call("GET", "/api/departments:list",
                      params={"filter": jf({"code": DEPT_CODE})}).get("data", [{}])[0]
    mgr = admin.call("GET", "/api/employees:list",
                     params={"filter": jf({"employeeNo": MGR_EMP_NO})}).get("data", [{}])[0]
    r = admin.call("POST", "/api/employees:create", json_body={
        "employeeNo": no, "name": name,
        "departmentId": dept.get("id"), "jobTitle": "演示工程师",
        "managerId": mgr.get("id"), "status": "PROBATION",
        "hireDate": date.today().isoformat(),
    })
    emp = r.get("data")
    check(f"创建工具员工 {no}", bool(emp), json.dumps(r, ensure_ascii=False)[:150])
    register(admin, "employee", emp["id"], no)

    r = admin.create_user(email, env["DEMO_PASSWORD"], name, username=f"demo{nn:02d}")
    u = r.get("data")
    check(f"创建账号 {email}", bool(u), json.dumps(r, ensure_ascii=False)[:150])
    admin.call("POST", "/api/users:update", params={"filterByTk": u["id"]},
               json_body={"roles": [{"name": "employee"}]})
    register(admin, "user", u["id"], no)  # refId=账号 id，label=工号（复用校验比对绑定）

    admin.call("POST", "/api/employees:update", params={"filterByTk": emp["id"]},
               json_body={"user": u["id"]})
    bound = admin.call("GET", "/api/employees:list",
                       params={"filter": jf({"employeeNo": no}),
                               "appends": ["user"]}).get("data", [{}])[0]
    check(f"新身份账号绑定 {no}", (bound.get("user") or {}).get("id") == u["id"],
          f"user={(bound.get('user') or {}).get('id')}")
    ident = {"nn": nn, "label": no, "empId": emp["id"], "userId": u["id"],
             "emp": bound, "boundUserId": u["id"]}
    return ident, u


def pick_reusable(identities, all_reqs):
    """最新可复用身份（PROBATION+绑定完整+名下无任何申请）；无可复用返回 None。"""
    by_emp = {}
    for r in all_reqs:
        by_emp.setdefault(r["employeeId"], []).append(r)
    for ident in identities:
        if identity_usable(ident, by_emp.get(ident["empId"], [])):
            return ident
    return None


def submit_pending(env, email):
    """以演示身份走受控 saveDraft + submit（不绕过服务端校验），返回申请 id。"""
    c = signed(email, env["DEMO_PASSWORD"])
    d = c.call("POST", "/api/confirmation_requests:saveDraft", json_body={
        "selfReview": "入职以来完成了演示需求的开发与联调，请审批转正。",
        "trialHarvest": "熟悉了团队的组件规范与发布流程。",
    })
    rid = d.get("data", {}).get("id")
    check("saveDraft 成功", bool(rid), f"resp={json.dumps(d, ensure_ascii=False)[:150]}")
    s = c.call("POST", "/api/confirmation_requests:submit", json_body={"requestId": rid})
    check("submit 成功且 PENDING", s.get("data", {}).get("status") == "PENDING",
          f"resp={json.dumps(s, ensure_ascii=False)[:150]}")
    return rid


def mgr_todo_has(admin, emp_name):
    pend = admin.call("GET", "/api/confirmation_requests:list",
                      params={"filter": jf({"status": "PENDING"}),
                              "appends": ["employee"]}).get("data", [])
    names = [(p.get("employee") or {}).get("name") for p in pend]
    return emp_name in names, names


def print_stats(admin):
    emps = admin.call("GET", "/api/employees:list",
                      params={"pageSize": 500}).get("data", [])
    total = len(emps)
    left = sum(1 for e in emps if e.get("status") == "LEFT")
    probation = sum(1 for e in emps if e.get("status") == "PROBATION")
    reqs = admin.call("GET", "/api/confirmation_requests:list",
                      params={"pageSize": 1000}).get("data", [])
    pending = sum(1 for r in reqs if r.get("status") == "PENDING")
    approved = sum(1 for r in reqs if r.get("status") == "APPROVED")
    rejected = sum(1 for r in reqs if r.get("status") == "REJECTED")
    n_registry = len(registry_map(admin, "employee"))
    print("\n[prep] ===== 实时统计（演示话术从这取数，勿写死）=====", flush=True)
    print(f"[prep] 员工总数 {total} | 在职 {total - left} | 试用 {probation} | 离职 {left}",
          flush=True)
    print(f"[prep] 申请：待审批 {pending} | 已批 {approved} | 已驳 {rejected}"
          f"（演示审批 1 条后待审批 = {max(pending - 1, 0)}）", flush=True)
    print(f"[prep] 工具演示身份 {n_registry} 个（清单登记，历史全部保留）", flush=True)


# ---- 两种模式 ------------------------------------------------------------


def run_blank(admin, env, identities):
    """空白开始：只删清单在途申请；确保可登录/试用/无在途的专属身份并输出账号。"""
    emp_ids = [i["empId"] for i in identities]
    reqs = requests_of(admin, emp_ids)
    req_reg = registry_map(admin, "request")
    inflight = [r for r in reqs
                if r["id"] in req_reg and r.get("status") in ("DRAFT", "PENDING")]
    if inflight:
        cleanup_requests(admin, [r["id"] for r in inflight])
        for r in inflight:
            unregister(admin, "request", r["id"])
    kept_all = [r["id"] for r in requests_of(admin, emp_ids)
                if r.get("status") in ("DRAFT", "PENDING")]
    kept_reg = [rid for rid in kept_all if rid in req_reg]
    log(f"空白开始：已清【清单登记】在途单 {len(inflight)} 条；"
        f"清单员工名下非清单在途单保留 {len(kept_all) - len(kept_reg)} 条"
        f"（手工单不删；APPROVED/REJECTED 历史与种子数据未动）")
    check("空白模式后无【清单登记】在途单", not kept_reg, f"remaining={kept_reg}")

    # 确保空白演示身份：可登录 + PROBATION + 名下无任何申请；没有就新建（不提交）。
    ident = pick_reusable(identities, requests_of(admin, emp_ids))
    if ident:
        log(f"空白演示身份：复用 {ident['label']}"
            f"（登记账号 userId={ident['userId']}，绑定完整）")
        email = f"demo{ident['nn']:02d}@example.com"
    else:
        nn = alloc_nn(admin, identities)
        log(f"空白演示身份：清单中无可复用身份（均已批/已驳/绑定变动），新建 DEMO-{nn:02d}")
        ident, _u = create_identity(admin, env, nn)
        email = f"demo{nn:02d}@example.com"

    c = signed(email, env["DEMO_PASSWORD"])  # 验证可登录
    check("空白演示身份可登录", bool(c.token), f"email={email}")
    print("\n" + "=" * 62, flush=True)
    print(f"[prep] 空白路径演示账号：{email}（密码=.env 的 DEMO_PASSWORD）", flush=True)
    print(f"[prep] 员工 {ident['label']}（PROBATION，无在途申请）——演示完整走第 2 步：",
          flush=True)
    print("[prep]   新建草稿 → 编辑 → 提交 → 主管审批（互斥：勿再跑默认模式）",
          flush=True)
    print("=" * 62, flush=True)
    print_stats(admin)
    return summary()


def run_default(admin, env, identities):
    """预填待办：确保恰有一条清单身份的 PENDING（已有则复用；否则复用/新建后提交）。"""
    emp_ids = [i["empId"] for i in identities]
    reqs = requests_of(admin, emp_ids)
    req_reg = registry_map(admin, "request")

    pending = [r for r in reqs
               if r["id"] in req_reg and r.get("status") == "PENDING"]
    if pending:
        p = pending[0]
        owner = next((i for i in identities if i["empId"] == p["employeeId"]), None)
        email = f"demo{owner['nn']:02d}@example.com" if owner else "?"
        log(f"已有【清单登记】PENDING 申请 id={p['id']}，跳过提交（直接审批路径）")
        ok, names = mgr_todo_has(admin, (owner and owner["emp"] or {}).get("name"))
        check("主管A 待办包含该演示员工", ok, f"pending={names}")
        print("\n" + "=" * 62, flush=True)
        print(f"[prep] 预填路径演示账号：{email}（密码=.env 的 DEMO_PASSWORD）", flush=True)
        print(f"[prep] 待办申请 id={p['id']} 已 PENDING——演示【跳过第 2 步】，直接从",
              flush=True)
        print("[prep]   第 3 步主管审批开始（互斥：勿再跑 --blank）", flush=True)
        print("=" * 62, flush=True)
        print_stats(admin)
        return summary()

    # 无清单 PENDING：优先复用最新可用身份，否则顺延新建（碰撞跳过不接管）。
    ident = pick_reusable(identities, reqs)
    if ident:
        log(f"复用最新工具身份 {ident['label']}（PROBATION 且名下无申请）")
        email = f"demo{ident['nn']:02d}@example.com"
        # 绑定完整性已在 pick_reusable 校验；再核对账号在库
        u = admin.call("GET", "/api/users:list",
                       params={"filter": jf({"email": email})}).get("data", [])
        if not u:
            log(f"登记账号 {email} 已不存在，改用新身份")
            ident = None
    else:
        if identities:
            log(f"无可复用身份（最新 DEMO-{identities[0]['nn']:02d} "
                "已批/已驳/在途/绑定变动，历史保留）")
        else:
            log("清单为空（首次运行或全新实例），从 DEMO-01 开始")
    if not ident:
        nn = alloc_nn(admin, identities)
        log(f"新建工具身份 DEMO-{nn:02d}（历史一律保留）")
        ident, _u = create_identity(admin, env, nn)
        email = f"demo{nn:02d}@example.com"

    rid = submit_pending(env, email)
    register(admin, "request", rid, ident["label"])
    ok, names = mgr_todo_has(admin, ident["emp"].get("name"))
    check(f"主管A 待办包含 {ident['emp'].get('name')}", ok, f"pending={names}")
    print("\n" + "=" * 62, flush=True)
    print(f"[prep] 预填路径演示账号：{email}（密码=.env 的 DEMO_PASSWORD）", flush=True)
    print(f"[prep] 新 PENDING 申请 id={rid}——演示【跳过第 2 步】，直接从第 3 步",
          flush=True)
    print("[prep]   主管审批开始（互斥：欲演示完整草稿流请用 --blank）", flush=True)
    print("=" * 62, flush=True)
    print_stats(admin)
    return summary()


def main():
    env = load_env()
    blank = "--blank" in sys.argv
    if "--reset" in sys.argv:
        print("[prep] --reset 已移除（REV-010：会删审批历史）。用 --blank 清在途单，"
              "或直接默认模式幂等补待办。", flush=True)
        return 1
    admin = NbClient(email=env["INIT_ROOT_EMAIL"], password=env["INIT_ROOT_PASSWORD"])
    admin.signin()

    ensure_registry(admin)
    hr = signed("hr@example.com", env["DEMO_PASSWORD"])
    r = hr.req("GET", f"/api/{REGISTRY}:list")
    check("工具清单仅 root 可访问（HR 读取被拒）", r.status_code in (403, 404),
          f"status={r.status_code}")
    identities = owned_identities(admin)

    if blank:
        return run_blank(admin, env, identities)
    return run_default(admin, env, identities)


if __name__ == "__main__":
    sys.exit(main())
