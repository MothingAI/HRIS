#!/usr/bin/env python3
"""业务闭环验证（真实登录会话）：转正提交/通过/驳回、越权、并发、事务回滚。

对应验收：
- 正向：员工提交 -> 被指定主管通过 -> 员工 REGULAR -> 轨迹完整；驳回可再申请。
- 越权：伪造 employeeId/approverId/status 不能生效；非指定审批人/自批被拒绝。
- 并发：同时通过与驳回只有一个成功，轨迹不重复。
- 事务：故障注入（DB 触发器阻断轨迹写入）时，申请与员工状态整体回滚。

用法：python scripts/verify_business.py
退出码：0 = 全部通过；1 = 存在失败。
"""
import concurrent.futures
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from target_binding import verify_target  # noqa: E402
from verify_common import (check, cleanup_requests, jf, load_env, reset_employee_status,
                           signed, status_of, summary)  # noqa: E402

created = []  # 本次运行创建的 request id，供清理


def emp_id(admin, no):
    r = admin.call("GET", "/api/employees:list", params={"filter": jf({"employeeNo": no})})
    return r.get("data", [{}])[0].get("id")


def emp_status(admin, no):
    r = admin.call("GET", "/api/employees:list", params={"filter": jf({"employeeNo": no})})
    rows = r.get("data", [])
    return rows[0]["status"] if rows else None


def actions_of(admin, request_id):
    r = admin.call("GET", "/api/approval_actions:list",
                   params={"filter": jf({"requestId": request_id})})
    return [a["action"] for a in r.get("data", [])]


def track(rid):
    created.append(rid)
    return rid


def save_and_submit(client, review):
    d = client.call("POST", "/api/confirmation_requests:saveDraft",
                    json_body={"selfReview": review})
    rid = d["data"]["id"]
    track(rid)
    s = client.call("POST", "/api/confirmation_requests:submit",
                    json_body={"requestId": rid})
    return rid, s


def db_container(env):
    """故障注入 / SQL 直连的数据库容器，与 NB_BASE 指向的实例保持一致。

    默认指向演示库容器；故障注入额外要求 HRIS_ALLOW_FAULT_INJECT=1 且目标不是
    默认演示库（见 main 第 7 节），防止误把全库故障触发器装到原库。
    """
    return env.get("NB_DB_CONTAINER", "hris-postgres-1")


def run_sql(env, sql):
    """在指定的 postgres 容器内执行 SQL（供故障注入用）。"""
    pw = env["POSTGRES_PASSWORD"]
    env_psql = dict(os.environ)
    env_psql["PGPASSWORD"] = pw
    subprocess.run(
        ["docker", "exec", db_container(env), "psql", "-U", env["POSTGRES_USER"],
         "-d", env["POSTGRES_DB"], "-c", sql],
        check=True, capture_output=True, text=True, env=env_psql,
    )


def main():
    env = load_env()
    # REV-007（11-18 复核补强）：本套件从第 1 节起就写目标实例，门禁必须在
    # 【任何写入前】执行，不能等第 7 节注入前才拒绝——显式指向主实例同样
    # 拒绝（require_explicit + forbid_main：回环主机 + 非主项目/主容器/主端口）。
    binding = None
    try:
        binding = verify_target(env, "业务套件写入前置门禁",
                                require_explicit=True, forbid_main=True)
        check("写入前目标门禁（显式隔离实例，非主项目）", True,
              f"project={binding['project']} base={binding['base']}")
    except Exception as e:  # noqa: BLE001
        check("写入前目标门禁（显式隔离实例，非主项目）", False, str(e)[:250])
        return summary()
    pw = env["DEMO_PASSWORD"]
    admin = signed(env["INIT_ROOT_EMAIL"], env["INIT_ROOT_PASSWORD"])
    emp_a = signed("employee.a@example.com", pw)
    emp_b = signed("employee.b@example.com", pw)
    mgr_a = signed("manager.a@example.com", pw)
    mgr_b = signed("manager.b@example.com", pw)

    e002 = emp_id(admin, "E002")
    e011 = emp_id(admin, "E011")
    mgr_a_uid = mgr_a.user["id"]
    mgr_b_uid = mgr_b.user["id"]
    emp_a_uid = emp_a.user["id"]

    # ===== 1. 正向：提交 -> 通过 -> 员工 REGULAR =====
    rid, s = save_and_submit(emp_b, "产品试用期表现优秀，申请转正")
    check("提交后状态 PENDING", s["data"]["status"] == "PENDING", f"status={s['data']['status']}")
    check("提交后 approver = 直属主管账号",
          s["data"]["approverId"] == mgr_b_uid,
          f"approverId={s['data']['approverId']} expect={mgr_b_uid}")

    a = mgr_b.call("POST", "/api/confirmation_requests:approve",
                   json_body={"requestId": rid, "comment": "同意转正"})
    check("通过后状态 APPROVED", a["data"]["status"] == "APPROVED", f"status={a['data']['status']}")
    check("通过后员工 REGULAR", emp_status(admin, "E011") == "REGULAR",
          f"E011={emp_status(admin, 'E011')}")
    check("轨迹完整 [SUBMIT, APPROVE]", actions_of(admin, rid) == ["SUBMIT", "APPROVE"],
          f"actions={actions_of(admin, rid)}")

    # 复位 E011，清理
    reset_employee_status(admin, e011, "PROBATION")
    cleanup_requests(admin, [rid])
    created.clear()

    # ===== 2. 驳回 + 再申请 =====
    rid, s = save_and_submit(emp_a, "试用期自我评价一般")
    r = mgr_a.call("POST", "/api/confirmation_requests:reject",
                   json_body={"requestId": rid, "comment": "需继续观察一个月"})
    check("驳回后状态 REJECTED", r["data"]["status"] == "REJECTED", f"status={r['data']['status']}")
    check("驳回后员工仍 PROBATION", emp_status(admin, "E002") == "PROBATION",
          f"E002={emp_status(admin, 'E002')}")
    check("驳回轨迹 [SUBMIT, REJECT]", actions_of(admin, rid) == ["SUBMIT", "REJECT"],
          f"actions={actions_of(admin, rid)}")

    rid2, s2 = save_and_submit(emp_a, "改进后再申请转正")
    check("驳回后可再申请(PENDING)", s2["data"]["status"] == "PENDING",
          f"status={s2['data']['status']}")
    cleanup_requests(admin, [rid, rid2])
    created.clear()

    # ===== 3. 重复提交保护 =====
    d1 = emp_a.call("POST", "/api/confirmation_requests:saveDraft",
                    json_body={"selfReview": "草稿1"}); rid_a = track(d1["data"]["id"])
    d2 = emp_a.call("POST", "/api/confirmation_requests:saveDraft",
                    json_body={"selfReview": "草稿2"}); rid_b = track(d2["data"]["id"])
    emp_a.call("POST", "/api/confirmation_requests:submit", json_body={"requestId": rid_a})
    code2, _ = status_of(emp_a, "POST", "/api/confirmation_requests:submit",
                          json_body={"requestId": rid_b})
    check("同一员工不能有第二个 PENDING", code2 == 409, f"status={code2}")
    code3, _ = status_of(emp_a, "POST", "/api/confirmation_requests:submit",
                          json_body={"requestId": rid_a})
    check("重复提交同一申请被拒绝", code3 in (400, 409), f"status={code3}")
    cleanup_requests(admin, [rid_a, rid_b])
    created.clear()

    # ===== 4. 跨主管隔离 + 越权审批 =====
    rid, s = save_and_submit(emp_b, "跨主管隔离测试")
    vis_a = [x["id"] for x in mgr_a.call("GET", "/api/confirmation_requests:list").get("data", [])]
    vis_b = [x["id"] for x in mgr_b.call("GET", "/api/confirmation_requests:list").get("data", [])]
    check("主管A 看不到主管B 的申请", rid not in vis_a, f"vis_a={vis_a}")
    check("主管B 看到自己的待办", rid in vis_b, f"vis_b={vis_b}")
    code, _ = status_of(mgr_a, "POST", "/api/confirmation_requests:approve",
                        json_body={"requestId": rid, "comment": "越权尝试"})
    check("非指定审批人不能通过", code == 403, f"status={code}")
    cleanup_requests(admin, [rid])
    created.clear()

    # ===== 5. 伪造字段 + 自批防护 =====
    fd = emp_a.call("POST", "/api/confirmation_requests:saveDraft",
                    json_body={"selfReview": "伪造测试", "status": "APPROVED",
                               "approverId": emp_a_uid, "employeeId": 999999})
    check("saveDraft 伪造 status/approver/employee 被剥离",
          fd["data"]["status"] == "DRAFT"
          and fd["data"]["approverId"] is None
          and fd["data"]["employeeId"] == e002,
          f"status={fd['data']['status']} approver={fd['data']['approverId']} emp={fd['data']['employeeId']}")
    fake_rid = fd["data"]["id"]; track(fake_rid)

    # 自批：emp_a 提交自己的申请后，emp_a 不是 approver，试图通过 -> 403
    emp_a.call("POST", "/api/confirmation_requests:submit", json_body={"requestId": fake_rid})
    code, _ = status_of(emp_a, "POST", "/api/confirmation_requests:approve",
                        json_body={"requestId": fake_rid})
    check("员工不能审批自己的申请(非approver)", code == 403, f"status={code}")
    cleanup_requests(admin, [fake_rid])
    created.clear()

    # ===== 6. 并发通过/驳回：只有一个终态 =====
    rid, s = save_and_submit(emp_a, "并发决定测试")
    def do_approve():
        c = signed("manager.a@example.com", pw)
        return c.req("POST", "/api/confirmation_requests:approve",
                     json_body={"requestId": rid, "comment": "并发通过"}).status_code

    def do_reject():
        c = signed("manager.a@example.com", pw)
        return c.req("POST", "/api/confirmation_requests:reject",
                     json_body={"requestId": rid, "comment": "并发驳回"}).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(do_approve)
        f2 = ex.submit(do_reject)
        codes = sorted([f1.result(), f2.result()])

    acts = actions_of(admin, rid)
    req = admin.call("GET", "/api/confirmation_requests:get",
                     params={"filterByTk": rid}).get("data")
    check("并发决定仅一个成功(200)，另一个冲突(409)", codes == [200, 409], f"codes={codes}")
    check("并发后只有一个终态", req["status"] in ("APPROVED", "REJECTED"), f"status={req['status']}")
    check("并发后轨迹不重复(仅2条)", len(acts) == 2 and acts[0] == "SUBMIT",
          f"actions={acts}")

    if req["status"] == "APPROVED":
        reset_employee_status(admin, e002, "PROBATION")
    cleanup_requests(admin, [rid])
    created.clear()

    # ===== 7. 事务回滚（故障注入，仅隔离环境） =====
    allow_fault = env.get("HRIS_ALLOW_FAULT_INJECT", "").strip().lower() in ("1", "true", "yes")
    if not allow_fault:
        print("[SKIP] 故障注入回滚：需显式 HRIS_ALLOW_FAULT_INJECT=1 且 NB_BASE/NB_APP_CONTAINER/"
              "NB_DB_CONTAINER 全部显式指向同一隔离项目（默认不触碰原库）",
              flush=True)
    else:
        # 门禁已在 main() 开头（任何写入前）完成：显式隔离实例 + 非主项目。
        if not binding:
            return summary()
        rid, s = save_and_submit(emp_a, "回滚测试")
        check("回滚前置：提交 PENDING", s["data"]["status"] == "PENDING", f"status={s['data']['status']}")

        create_trigger = (
            "CREATE OR REPLACE FUNCTION hris_fault_inject() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION 'hris-fault-inject: approval_actions blocked'; END; "
            "$$ LANGUAGE plpgsql; "
            "DROP TRIGGER IF EXISTS hris_block_action ON approval_actions; "
            "CREATE TRIGGER hris_block_action BEFORE INSERT ON approval_actions "
            "FOR EACH ROW EXECUTE FUNCTION hris_fault_inject();"
        )
        drop_trigger = (
            "DROP TRIGGER IF EXISTS hris_block_action ON approval_actions; "
            "DROP FUNCTION IF EXISTS hris_fault_inject();"
        )

        try:
            run_sql(env, create_trigger)
            code, _ = status_of(mgr_a, "POST", "/api/confirmation_requests:approve",
                                json_body={"requestId": rid, "comment": "回滚测试"})
            req = admin.call("GET", "/api/confirmation_requests:get",
                             params={"filterByTk": rid}).get("data")
            check("故障注入时通过失败(非200)", code != 200, f"status={code}")
            check("回滚：申请仍 PENDING", req["status"] == "PENDING", f"status={req['status']}")
            check("回滚：员工仍 PROBATION", emp_status(admin, "E002") == "PROBATION",
                  f"E002={emp_status(admin, 'E002')}")
            check("回滚：无 APPROVE 轨迹", actions_of(admin, rid) == ["SUBMIT"],
                  f"actions={actions_of(admin, rid)}")
        finally:
            run_sql(env, drop_trigger)

        cleanup_requests(admin, [rid])
        created.clear()

    return summary()


if __name__ == "__main__":
    sys.exit(main())
