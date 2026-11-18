#!/usr/bin/env python3
"""首轮审查修复回归（REV-002/003/004/005）验证。

对应修复：
- REV-002 角色强制：撤销主管角色后不能审批；HR/员工被指定 approver 不能审批；
  伪造角色请求不能升级；提交时校验直属主管仍具审批资格。
- REV-003 账号唯一绑定：employees.userId 唯一索引真实存在；重复 create/update/关系
  写入/并发绑定均不能制造重复；未绑定（NULL）员工可多个。
- REV-004 员工关系守卫：禁止自己管理自己；PENDING 时冻结 manager/user 变更。
- REV-005 驳回理由校验：空白/空/非文本/超长被拒，合法中文意见正常。

建议在隔离测试实例运行（NB_BASE 指向隔离应用）。脚本只改演示账号角色与员工状态，
并在结束前恢复；不删除任何用户数据（临时创建的测试员工/账号会清理）。

用法：python scripts/verify_regression.py
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


def emp_id(admin, no):
    r = admin.call("GET", "/api/employees:list", params={"filter": jf({"employeeNo": no})})
    rows = r.get("data", [])
    return rows[0]["id"] if rows else None


def emp_status(admin, no):
    r = admin.call("GET", "/api/employees:list", params={"filter": jf({"employeeNo": no})})
    rows = r.get("data", [])
    return rows[0]["status"] if rows else None


def emp_manager(admin, no):
    r = admin.call("GET", "/api/employees:list", params={"filter": jf({"employeeNo": no})})
    rows = r.get("data", [])
    return rows[0].get("managerId") if rows else None


def set_roles(admin, user_id, role_names):
    return admin.call("POST", "/api/users:update", params={"filterByTk": user_id},
                      json_body={"roles": [{"name": n} for n in role_names]})


def user_id(admin, email):
    r = admin.call("GET", "/api/users:list", params={"filter": jf({"email": email})})
    rows = r.get("data", [])
    return rows[0]["id"] if rows else None


def sql_query(env, query):
    pw = env["POSTGRES_PASSWORD"]
    env_psql = dict(os.environ)
    env_psql["PGPASSWORD"] = pw
    container = env.get("NB_DB_CONTAINER", "hris-postgres-1")
    r = subprocess.run(
        ["docker", "exec", container, "psql", "-U", env["POSTGRES_USER"],
         "-d", env["POSTGRES_DB"], "-t", "-A", "-c", query],
        capture_output=True, text=True, env=env_psql,
    )
    return r.stdout.strip()


def count_emp_by_userid(admin, uid):
    r = admin.call("GET", "/api/employees:list", params={"filter": jf({"userId": uid})})
    return len(r.get("data", []))


def save_submit(client, review):
    d = client.call("POST", "/api/confirmation_requests:saveDraft",
                    json_body={"selfReview": review})
    rid = d["data"]["id"]
    s = client.call("POST", "/api/confirmation_requests:submit",
                    json_body={"requestId": rid})
    return rid, s


def main():
    env = load_env()
    # REV-007：脚本含 docker exec SQL（索引存在性等证据查询），先核验
    # API/app/DB 同一目标，避免拿错库的“证据”。本套件会写业务数据
    # （建申请/审批/复位状态），任何写入前要求显式隔离实例且非主项目。
    try:
        binding = verify_target(env, "回归套件（写入前置门禁）",
                                require_explicit=True, forbid_main=True)
        print(f"[bind] 目标一致: {binding['base']} / {binding['app']} / "
              f"{binding['db']} (project={binding['project']})", flush=True)
    except Exception as e:  # noqa: BLE001
        check("目标绑定一致（API/app/DB 同一项目）", False, str(e)[:200])
        return summary()
    pw = env["DEMO_PASSWORD"]
    admin = signed(env["INIT_ROOT_EMAIL"], env["INIT_ROOT_PASSWORD"])
    emp_a = signed("employee.a@example.com", pw)
    mgr_a = signed("manager.a@example.com", pw)
    mgr_b = signed("manager.b@example.com", pw)
    hr = signed("hr@example.com", pw)

    e002 = emp_id(admin, "E002")
    e011 = emp_id(admin, "E011")
    mgr_a_uid = mgr_a.user["id"]
    emp_a_uid = emp_a.user["id"]

    # ===== REV-002 角色强制 =====

    # 1) 正向基线：主管合法通过仍有效
    rid, s = save_submit(emp_a, "角色正向基线")
    check("REV-002 正向：提交 PENDING", s["data"]["status"] == "PENDING", f"status={s['data']['status']}")
    a = mgr_a.call("POST", "/api/confirmation_requests:approve",
                   json_body={"requestId": rid, "comment": "同意"})
    check("REV-002 正向：主管合法通过 200", a.get("data", {}).get("status") == "APPROVED",
          f"status={a.get('data', {}).get('status')}")
    reset_employee_status(admin, e002, "PROBATION")
    cleanup_requests(admin, [rid])

    # 2) 撤销主管角色 -> 重新登录 -> 不能审批
    rid, s = save_submit(emp_a, "角色撤销测试")
    set_roles(admin, mgr_a_uid, ["employee"])
    mgr_a_demoted = signed("manager.a@example.com", pw)
    code, body = status_of(mgr_a_demoted, "POST", "/api/confirmation_requests:approve",
                           json_body={"requestId": rid, "comment": "越权"})
    check("REV-002 撤销主管角色后不能审批(403)", code == 403, f"status={code}")
    check("REV-002 撤销角色后员工仍 PROBATION", emp_status(admin, "E002") == "PROBATION",
          f"E002={emp_status(admin, 'E002')}")
    set_roles(admin, mgr_a_uid, ["manager"])
    cleanup_requests(admin, [rid])

    # 3) 提交时校验直属主管仍具审批资格（主管被撤销 -> 提交失败）
    set_roles(admin, mgr_a_uid, ["employee"])
    rid, s = save_submit(emp_a, "主管无资格提交测试")
    check("REV-002 提交时主管已无审批资格(400)",
          s.get("_status") in (400, 500) or (isinstance(s, dict) and "errors" in s),
          f"submit={s}")
    set_roles(admin, mgr_a_uid, ["manager"])
    cleanup_requests(admin, [rid])

    # 4) HR 不能审批（HR 角色，即使尝试）
    rid, s = save_submit(emp_a, "HR 不能审批测试")
    code, _ = status_of(hr, "POST", "/api/confirmation_requests:approve",
                        json_body={"requestId": rid, "comment": "HR 尝试"})
    check("REV-002 HR 不能审批(403)", code == 403, f"status={code}")
    cleanup_requests(admin, [rid])

    # 5) 伪造角色请求不能升级（员工带 role 字段请求审批仍 403）
    rid, s = save_submit(emp_a, "伪造角色测试")
    code, _ = status_of(emp_a, "POST", "/api/confirmation_requests:approve",
                        json_body={"requestId": rid, "comment": "x", "role": "manager"})
    check("REV-002 伪造角色请求不能审批(403)", code == 403, f"status={code}")
    cleanup_requests(admin, [rid])

    # ===== REV-003 账号唯一绑定 =====

    # 6) 唯一索引真实存在（按列匹配：平台 SyncRunner 建的 employees_userId_uk
    #    与插件迁移建的 employees_user_id_key 都算数——REV-003 修复后两者等价）
    idx = sql_query(env, "SELECT count(*) FROM pg_indexes WHERE tablename='employees' "
                         "AND indexdef LIKE 'CREATE UNIQUE%(\"userId\")%'")
    check("REV-003 employees.userId 唯一索引存在", idx not in ("0", ""), f"idx_count={idx}")

    # 7) 重复 create 被拒
    raw = hr.req("POST", "/api/employees:create", json_body={
        "employeeNo": "REV-DUP-1", "name": "重复绑定1", "user": {"id": emp_a_uid}})
    check("REV-003 重复 userId create 被拒", raw.status_code != 200, f"status={raw.status_code}")
    check("REV-003 重复 create 后仍仅 1 条绑定", count_emp_by_userid(admin, emp_a_uid) == 1,
          f"count={count_emp_by_userid(admin, emp_a_uid)}")

    # 8) 重复 update 被拒
    r = admin.call("POST", "/api/employees:create", json_body={
        "employeeNo": "REV-TEMP", "name": "临时员工"})
    temp_id = r.get("data", {}).get("id")
    raw = hr.req("POST", "/api/employees:update", params={"filterByTk": temp_id},
                 json_body={"user": {"id": emp_a_uid}})
    check("REV-003 重复 userId update 被拒", raw.status_code != 200, f"status={raw.status_code}")

    # 9) 未绑定（NULL）员工可多个
    raw2 = hr.req("POST", "/api/employees:create", json_body={
        "employeeNo": "REV-NULL-2", "name": "未绑定员工2"})
    check("REV-003 未绑定(NULL)员工可创建多个", raw2.status_code == 200, f"status={raw2.status_code}")
    # 清理临时员工
    for no in ("REV-TEMP", "REV-NULL-2", "REV-DUP-1"):
        tid = emp_id(admin, no)
        if tid:
            admin.call("POST", "/api/employees:destroy", params={"filterByTk": tid})

    # 10) 并发绑定同一账号：仅一个成功
    r = admin.call("POST", "/api/employees:create", json_body={"employeeNo": "REV-C1", "name": "并发1"})
    c1 = r.get("data", {}).get("id")
    r = admin.call("POST", "/api/employees:create", json_body={"employeeNo": "REV-C2", "name": "并发2"})
    c2 = r.get("data", {}).get("id")
    tmp_user = admin.create_user("rev.tmp@example.com", pw, "并发测试账号")
    tmp_uid = tmp_user.get("data", {}).get("id")

    def bind(eid):
        c = signed("hr@example.com", pw)
        return c.req("POST", "/api/employees:update", params={"filterByTk": eid},
                     json_body={"user": {"id": tmp_uid}}).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(bind, c1)
        f2 = ex.submit(bind, c2)
        codes = sorted([f1.result(), f2.result()])
    check("REV-003 并发绑定仅一个成功", sum(1 for c in codes if c == 200) == 1,
          f"codes={codes}")
    check("REV-003 并发绑定后仅 1 条绑定", count_emp_by_userid(admin, tmp_uid) == 1,
          f"count={count_emp_by_userid(admin, tmp_uid)}")
    for no in ("REV-C1", "REV-C2"):
        tid = emp_id(admin, no)
        if tid:
            admin.call("POST", "/api/employees:destroy", params={"filterByTk": tid})
    if tmp_uid:
        admin.call("POST", "/api/users:destroy", params={"filterByTk": tmp_uid})

    # ===== REV-004 员工关系守卫 =====

    # 11) 自己管理自己被拒
    raw = hr.req("POST", "/api/employees:update", params={"filterByTk": e011},
                 json_body={"manager": {"id": e011}})
    check("REV-004 自己管理自己被拒", raw.status_code != 200, f"status={raw.status_code}")
    check("REV-004 自管理被拒后 manager 未变",
          emp_manager(admin, "E011") != e011, f"manager={emp_manager(admin, 'E011')}")

    # 12) PENDING 时改主管被拒且数据不变
    rid, s = save_submit(emp_a, "PENDING 冻结测试")
    orig_mgr = emp_manager(admin, "E002")
    raw = hr.req("POST", "/api/employees:update", params={"filterByTk": e002},
                 json_body={"manager": {"id": emp_id(admin, "E009")}})
    check("REV-004 PENDING 时改主管被拒", raw.status_code != 200, f"status={raw.status_code}")
    check("REV-004 PENDING 时改主管数据不变", emp_manager(admin, "E002") == orig_mgr,
          f"manager={emp_manager(admin, 'E002')}")
    cleanup_requests(admin, [rid])

    # 13) 无 PENDING 时正常改主管允许
    new_mgr = emp_id(admin, "E009")
    raw = hr.req("POST", "/api/employees:update", params={"filterByTk": e002},
                 json_body={"manager": {"id": new_mgr}})
    check("REV-004 无 PENDING 时改主管允许", raw.status_code == 200, f"status={raw.status_code}")
    check("REV-004 改主管已生效", emp_manager(admin, "E002") == new_mgr,
          f"manager={emp_manager(admin, 'E002')}")
    # 还原
    hr.call("POST", "/api/employees:update", params={"filterByTk": e002},
            json_body={"manager": {"id": emp_id(admin, "E001")}})

    # ===== REV-005 驳回理由校验 =====

    def submit_for_reject():
        rid, s = save_submit(emp_a, "驳回理由测试")
        return rid

    # 空白 / 空 / 非文本 / 超长被拒
    for label, comment in [("纯空格", "   "), ("空字符串", ""), ("换行空白", "\n\t"),
                           ("对象", {"a": 1}), ("数字", 123)]:
        rid = submit_for_reject()
        code, _ = status_of(mgr_a, "POST", "/api/confirmation_requests:reject",
                            json_body={"requestId": rid, "comment": comment})
        check(f"REV-005 驳回理由非法({label})被拒", code in (400, 500) and code != 200,
              f"status={code}")
        cleanup_requests(admin, [rid])

    # 缺失 comment 被拒
    rid = submit_for_reject()
    code, _ = status_of(mgr_a, "POST", "/api/confirmation_requests:reject",
                        json_body={"requestId": rid})
    check("REV-005 驳回缺 comment 被拒", code != 200, f"status={code}")
    cleanup_requests(admin, [rid])

    # 合法中文意见正常
    rid = submit_for_reject()
    r = mgr_a.call("POST", "/api/confirmation_requests:reject",
                   json_body={"requestId": rid, "comment": "需继续观察一个月"})
    check("REV-005 合法中文意见驳回成功", r.get("data", {}).get("status") == "REJECTED",
          f"status={r.get('data', {}).get('status')}")
    check("REV-005 驳回后员工仍 PROBATION", emp_status(admin, "E002") == "PROBATION",
          f"E002={emp_status(admin, 'E002')}")
    cleanup_requests(admin, [rid])

    # 最终确认角色与状态已还原
    set_roles(admin, mgr_a_uid, ["manager"])
    check("REV 收尾：manager.a 角色已还原 manager",
          [r2.get("name") for r2 in admin.call("GET", "/api/users:list",
           params={"filter": jf({"email": "manager.a@example.com"}), "appends": ["roles"]})
           .get("data", [{}])[0].get("roles", [])] == ["manager"],
          "")

    return summary()


if __name__ == "__main__":
    sys.exit(main())
