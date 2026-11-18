#!/usr/bin/env python3
"""报表口径验证（真实登录会话，后端 API）：在职/待转正/待审批/部门人数。

REV-012 重写要点：
- 不再对全库写死 20/18/8/0 等绝对数（全量初始化后 pending=1 等合法状态会误报）；
  改为【相对变化】断言：创建/提交/审批前后同一口径的差值。
- 不再借用 E002/employee.a 的员工做状态迁移（那是用户现有数据）；全部使用
  工具自有测试数据：员工 RPT-TEST（技术部，直属主管 E001=manager.a，PROBATION）
  + 账号 rpt.test@example.com（角色 employee）。所有权标记 = RPT- 前缀；
  结束时申请/轨迹/员工/账号全部删除，不改动任何既有员工状态与历史。
- 建议在隔离实例运行（NB_BASE/NB_APP_CONTAINER/NB_DB_CONTAINER 指向同一项目）。

口径定义：在职 = PROBATION + REGULAR；待转正 = PROBATION；待审批 = PENDING；
部门人数只统计在职，按当前部门分组；主管/员工只能统计到授权范围内数据。

用法：python scripts/verify_reports.py
退出码：0 = 全部通过；1 = 存在失败。
"""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from target_binding import verify_target  # noqa: E402
from verify_common import check, cleanup_requests, jf, load_env, signed, summary  # noqa: E402

RPT_NO = "RPT-TEST"
RPT_EMAIL = "rpt.test@example.com"
RPT_NAME = "统计探针"


def emp_rows(client):
    return client.call("GET", "/api/employees:list",
                       params={"pageSize": 500}).get("data", [])


def dept_map(client):
    rows = client.call("GET", "/api/departments:list").get("data", [])
    return {r["id"]: r["name"] for r in rows}


def stats_of(rows, dept_by_id):
    status = Counter(r["status"] for r in rows)
    active = status.get("PROBATION", 0) + status.get("REGULAR", 0)
    dept = Counter()
    for r in rows:
        if r["status"] in ("PROBATION", "REGULAR"):
            dept[dept_by_id.get(r.get("departmentId"))] += 1
    return {
        "total": len(rows),
        "active": active,
        "probation": status.get("PROBATION", 0),
        "regular": status.get("REGULAR", 0),
        "left": status.get("LEFT", 0),
        "by_dept": dict(dept),
    }


def pending_count(client):
    rows = client.call("GET", "/api/confirmation_requests:list",
                       params={"filter": jf({"status": "PENDING"})}).get("data", [])
    return len(rows)


def cleanup_tool_data(admin):
    """删除可证明为本工具创建的数据（RPT-TEST 员工 + 账号 + 其申请/轨迹）。"""
    emp = admin.call("GET", "/api/employees:list",
                     params={"filter": jf({"employeeNo": RPT_NO})}).get("data", [])
    for e in emp:
        reqs = admin.call("GET", "/api/confirmation_requests:list",
                          params={"filter": jf({"employeeId": e["id"]})}).get("data", [])
        cleanup_requests(admin, [r["id"] for r in reqs])
        admin.call("POST", "/api/employees:update",
                   params={"filterByTk": e["id"]}, json_body={"userId": None})
        admin.call("POST", "/api/employees:destroy", params={"filterByTk": e["id"]})
    users = admin.call("GET", "/api/users:list",
                       params={"filter": jf({"email": RPT_EMAIL})}).get("data", [])
    for u in users:
        admin.call("POST", "/api/users:destroy", params={"filterByTk": u["id"]})


def main():
    env = load_env()
    # REV-007：本套件创建/审批/删除 RPT-TEST 工具数据（写操作），任何写入前
    # 要求显式隔离实例且非主项目（不计入断言数，失败即退出 1）。
    try:
        verify_target(env, "报表套件（写入前置门禁）",
                      require_explicit=True, forbid_main=True)
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {e}", flush=True)
        return 1
    pw = env["DEMO_PASSWORD"]
    admin = signed(env["INIT_ROOT_EMAIL"], env["INIT_ROOT_PASSWORD"])
    hr = signed("hr@example.com", pw)
    mgr_a = signed("manager.a@example.com", pw)
    emp_a = signed("employee.a@example.com", pw)

    # 重跑自愈：清理上次可能残留的工具数据（所有权 = RPT- 前缀，可证明）
    cleanup_tool_data(admin)

    dmap = dept_map(hr)

    # ---- 基线（不假设具体数字，只记录当前口径） ----
    base = stats_of(emp_rows(hr), dmap)
    base_pending = pending_count(hr)
    base_mgr_scope = len(emp_rows(mgr_a))
    base_emp_scope = len(emp_rows(emp_a))
    print(f"[rpt] 基线: total={base['total']} active={base['active']} "
          f"probation={base['probation']} regular={base['regular']} "
          f"tech={base['by_dept'].get('技术部')} pending={base_pending} "
          f"mgr_scope={base_mgr_scope} emp_scope={base_emp_scope}", flush=True)

    # ---- 创建工具员工：total/active/probation/技术部 各 +1，待审批不变 ----
    dept = admin.call("GET", "/api/departments:list",
                      params={"filter": jf({"code": "TECH"})}).get("data", [{}])[0]
    mgr = admin.call("GET", "/api/employees:list",
                     params={"filter": jf({"employeeNo": "E001"})}).get("data", [{}])[0]
    r = admin.call("POST", "/api/employees:create", json_body={
        "employeeNo": RPT_NO, "name": RPT_NAME,
        "departmentId": dept.get("id"), "jobTitle": "统计口径探针",
        "managerId": mgr.get("id"), "status": "PROBATION",
    })
    check("创建工具员工 RPT-TEST", bool(r.get("data")),
          json.dumps(r, ensure_ascii=False)[:150])
    u = admin.create_user(RPT_EMAIL, pw, RPT_NAME, username="rpt.test")
    admin.call("POST", "/api/users:update", params={"filterByTk": u["data"]["id"]},
               json_body={"roles": [{"name": "employee"}]})
    admin.call("POST", "/api/employees:update",
               params={"filterByTk": r["data"]["id"]},
               json_body={"user": u["data"]["id"]})

    s1 = stats_of(emp_rows(hr), dmap)
    check("创建后 HR 员工总数 +1", s1["total"] == base["total"] + 1,
          f"{base['total']} -> {s1['total']}")
    check("创建后在职 +1", s1["active"] == base["active"] + 1,
          f"{base['active']} -> {s1['active']}")
    check("创建后试用 +1", s1["probation"] == base["probation"] + 1,
          f"{base['probation']} -> {s1['probation']}")
    check("创建后技术部在职 +1",
          s1["by_dept"].get("技术部") == base["by_dept"].get("技术部", 0) + 1,
          f"{base['by_dept'].get('技术部')} -> {s1['by_dept'].get('技术部')}")
    check("创建后待审批不变（未提交）", pending_count(hr) == base_pending,
          f"{base_pending} -> {pending_count(hr)}")

    # ---- 授权范围（相对）：主管 +1、员工不变 ----
    check("主管A 员工范围 +1（含新工具员工）", len(emp_rows(mgr_a)) == base_mgr_scope + 1,
          f"{base_mgr_scope} -> {len(emp_rows(mgr_a))}")
    check("员工A 员工范围不变（不含新员工）", len(emp_rows(emp_a)) == base_emp_scope,
          f"{base_emp_scope} -> {len(emp_rows(emp_a))}")

    # ---- 提交：待审批 +1 ----
    rpt = signed(RPT_EMAIL, pw)
    d = rpt.call("POST", "/api/confirmation_requests:saveDraft",
                 json_body={"selfReview": "统计口径探针申请"})
    rid = d["data"]["id"]
    rpt.call("POST", "/api/confirmation_requests:submit", json_body={"requestId": rid})
    check("提交后 HR 待审批 +1", pending_count(hr) == base_pending + 1,
          f"{base_pending} -> {pending_count(hr)}")

    # ---- 通过：在职不变、试用 -1、正式 +1、待审批复原 ----
    mgr_a.call("POST", "/api/confirmation_requests:approve",
               json_body={"requestId": rid, "comment": "口径探针：同意转正"})
    s2 = stats_of(emp_rows(hr), dmap)
    check("通过后在职不变", s2["active"] == s1["active"],
          f"{s1['active']} -> {s2['active']}")
    check("通过后试用 -1 / 正式 +1",
          s2["probation"] == s1["probation"] - 1 and s2["regular"] == s1["regular"] + 1,
          f"probation {s1['probation']}->{s2['probation']} "
          f"regular {s1['regular']}->{s2['regular']}")
    check("通过后待审批复原", pending_count(hr) == base_pending,
          f"-> {pending_count(hr)}")

    # ---- 清理（只删工具数据），并断言口径回到基线 ----
    cleanup_tool_data(admin)
    s3 = stats_of(emp_rows(hr), dmap)
    check("清理后口径回到基线",
          s3["total"] == base["total"] and s3["probation"] == base["probation"]
          and s3["by_dept"].get("技术部") == base["by_dept"].get("技术部"),
          f"total={s3['total']} probation={s3['probation']} "
          f"tech={s3['by_dept'].get('技术部')}")

    return summary()


if __name__ == "__main__":
    sys.exit(main())
