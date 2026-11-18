#!/usr/bin/env python3
"""权限验证（真实登录会话，后端 API）：行级隔离、字段级授权、受控动作保护。

对应验收：
- 员工 A/B 互相隔离，不能读写他人档案。
- 主管 A/B 只能看本人 + 直属下属。
- HR 看全部，但不能直接把员工改成 REGULAR。
- 员工不能用默认 create 绕过受控动作伪造状态/审批人。

用法：python scripts/verify_permissions.py
退出码：0 = 全部通过；1 = 存在失败。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verify_common import (check, cleanup_requests, jf, load_env, reset_employee_status,
                           signed, status_of, summary)  # noqa: E402


def emp_id(admin, no):
    r = admin.call("GET", "/api/employees:list", params={"filter": jf({"employeeNo": no})})
    rows = r.get("data", [])
    return rows[0]["id"] if rows else None


def main():
    env = load_env()
    pw = env["DEMO_PASSWORD"]
    admin = signed(env["INIT_ROOT_EMAIL"], env["INIT_ROOT_PASSWORD"])
    emp_a = signed("employee.a@example.com", pw)
    emp_b = signed("employee.b@example.com", pw)
    mgr_a = signed("manager.a@example.com", pw)
    mgr_b = signed("manager.b@example.com", pw)
    hr = signed("hr@example.com", pw)

    e001 = emp_id(admin, "E001")
    e002 = emp_id(admin, "E002")
    e011 = emp_id(admin, "E011")

    # ---- 员工行级隔离 ----
    rows = emp_a.call("GET", "/api/employees:list").get("data", [])
    check("员工A 仅见本人档案", len(rows) == 1 and rows[0]["employeeNo"] == "E002",
          f"count={len(rows)} nos={[r['employeeNo'] for r in rows]}")

    code, body = status_of(emp_a, "GET", "/api/employees:get", params={"filterByTk": e001})
    check("员工A 直接 :get 他人返回 null", code == 200 and body.get("data") is None,
          f"status={code}")

    code, body = status_of(emp_a, "POST", "/api/employees:update",
                           json_body={"jobTitle": "x"}, params={"filterByTk": e002})
    check("员工A 不能更新本人档案", code == 403, f"status={code}")

    rows = emp_b.call("GET", "/api/employees:list").get("data", [])
    check("员工B 仅见本人档案", len(rows) == 1 and rows[0]["employeeNo"] == "E011",
          f"count={len(rows)} nos={[r['employeeNo'] for r in rows]}")

    # ---- 主管行级隔离 ----
    # 期望集从全量数据推导（本人 + 直属下属），不写死 8/7 人——演示/工具员工
    # （如 prep_demo 的 DEMO-NN，直属 E001）加入后仍成立（REV-012 同款修正）
    all_emps = admin.call("GET", "/api/employees:list",
                          params={"pageSize": 500}).get("data", [])
    e009 = emp_id(admin, "E009")

    def mgr_expected(mgr_db_id):
        return sorted(r["employeeNo"] for r in all_emps
                      if r["id"] == mgr_db_id or r.get("managerId") == mgr_db_id)

    nos_a = sorted(r["employeeNo"] for r in mgr_a.call("GET", "/api/employees:list").get("data", []))
    check("主管A 恰见本人+直属下属（按数据推导）", nos_a == mgr_expected(e001),
          f"nos={nos_a} expected={mgr_expected(e001)}")

    nos_b = sorted(r["employeeNo"] for r in mgr_b.call("GET", "/api/employees:list").get("data", []))
    check("主管B 恰见本人+直属下属（按数据推导）", nos_b == mgr_expected(e009),
          f"nos={nos_b} expected={mgr_expected(e009)}")

    # ---- HR ----
    rows = hr.call("GET", "/api/employees:list",
                   params={"pageSize": 500}).get("data", [])
    check("HR 可见全部员工（与 root 全量一致）",
          sorted(r["employeeNo"] for r in rows) ==
          sorted(r["employeeNo"] for r in all_emps),
          f"hr={len(rows)} root={len(all_emps)}")

    before = [r["status"] for r in hr.call("GET", "/api/employees:list",
             params={"filter": jf({"employeeNo": "E002"})}).get("data", [])][0]
    hr.call("POST", "/api/employees:update", params={"filterByTk": e002},
            json_body={"status": "REGULAR"})
    after = [r["status"] for r in hr.call("GET", "/api/employees:list",
            params={"filter": jf({"employeeNo": "E002"})}).get("data", [])][0]
    check("HR 无法直接把员工改为 REGULAR", before == "PROBATION" and after == "PROBATION",
          f"before={before} after={after}")

    # ---- 受控动作保护：员工不能用默认 create 伪造 ----
    code, body = status_of(emp_a, "POST", "/api/confirmation_requests:create",
                           json_body={"selfReview": "x", "status": "PENDING"})
    check("员工不能用默认 create 绕过受控动作", code == 403, f"status={code}")

    return summary()


if __name__ == "__main__":
    sys.exit(main())
