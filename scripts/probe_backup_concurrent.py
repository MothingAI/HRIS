#!/usr/bin/env python3
"""REV-011 验收探针：备份期间对业务库持续写入（创建/提交申请），用于证明
dump 与清单共享同一导出快照后，备份期间正常发生的写入不再造成
"dump 内 N 人 / 清单内 N+1 人"的快照间隙差异。

行为：
- 创建工具自有员工 BKW-TEST（PROBATION，技术部，直属 E001）+ 账号
  bkw.test@example.com（角色 employee）；
- 在 --seconds 秒内循环：saveDraft → submit（PENDING）→ 删除该申请与轨迹，
  每 0.3s 一轮（备份通常 5–20s，覆盖整个窗口）；
- 结束后清理本轮在途申请/员工/账号（只删本工具创建的记录）；
- 退出码 0 = 按时完成；1 = 失败。

用法（与 backup.ps1 并行）：
  python scripts/probe_backup_concurrent.py --seconds 30
"""
import argparse
import sys
import time
from datetime import date

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))

from nb import NbClient  # noqa: E402
from verify_common import check, cleanup_requests, jf, load_env, signed, summary  # noqa: E402

BKW_NO = "BKW-TEST"
BKW_EMAIL = "bkw.test@example.com"
DEPT_CODE = "TECH"
MGR_EMP_NO = "E001"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=30)
    args = ap.parse_args()
    env = load_env()
    admin = NbClient(email=env["INIT_ROOT_EMAIL"], password=env["INIT_ROOT_PASSWORD"])
    admin.signin()

    # 工具自有员工 + 账号（幂等：已存在则复用）
    emps = admin.call("GET", "/api/employees:list",
                      params={"filter": jf({"employeeNo": BKW_NO})}).get("data", [])
    if not emps:
        dept = admin.call("GET", "/api/departments:list",
                          params={"filter": jf({"code": DEPT_CODE})}).get("data", [{}])[0]
        mgr = admin.call("GET", "/api/employees:list",
                         params={"filter": jf({"employeeNo": MGR_EMP_NO})}).get("data", [{}])[0]
        r = admin.call("POST", "/api/employees:create", json_body={
            "employeeNo": BKW_NO, "name": "备份并发写入探针",
            "departmentId": dept.get("id"), "jobTitle": "测试工程师",
            "managerId": mgr.get("id"), "status": "PROBATION",
            "hireDate": date.today().isoformat(),
        })
        check("创建探针员工", bool(r.get("data")), str(r)[:150])
    users = admin.call("GET", "/api/users:list",
                       params={"filter": jf({"email": BKW_EMAIL})}).get("data", [])
    if not users:
        r = admin.create_user(BKW_EMAIL, env["DEMO_PASSWORD"], "备份并发写入探针",
                              username="bkw.test")
        admin.call("POST", "/api/users:update", params={"filterByTk": r["data"]["id"]},
                   json_body={"roles": [{"name": "employee"}]})
        check("创建探针账号", bool(r.get("data")), str(r)[:150])
        users = [r["data"]]
    # 绑定员工 ↔ 账号（探针员工以本人身份走受控动作）
    e_now = admin.call("GET", "/api/employees:list",
                       params={"filter": jf({"employeeNo": BKW_NO}),
                               "appends": ["user"]}).get("data", [{}])[0]
    if (e_now.get("user") or {}).get("id") != users[0]["id"]:
        admin.call("POST", "/api/employees:update", params={"filterByTk": e_now["id"]},
                   json_body={"user": users[0]["id"]})
        check("探针账号绑定", True, f"emp={e_now.get('id')} user={users[0]['id']}")

    emp = signed(BKW_EMAIL, env["DEMO_PASSWORD"])
    deadline = time.time() + args.seconds
    rounds = 0
    while time.time() < deadline:
        d = emp.call("POST", "/api/confirmation_requests:saveDraft",
                     json_body={"selfReview": f"备份窗口并发写入第 {rounds} 轮"})
        rid = d.get("data", {}).get("id")
        if not rid:
            check(f"第 {rounds} 轮 saveDraft", False, str(d)[:150])
            break
        s = emp.call("POST", "/api/confirmation_requests:submit",
                     json_body={"requestId": rid})
        if s.get("data", {}).get("status") != "PENDING":
            check(f"第 {rounds} 轮 submit", False, str(s)[:150])
            break
        cleanup_requests(admin, [rid])
        rounds += 1
        time.sleep(0.3)
    check(f"并发写入持续 {args.seconds}s", rounds > 0, f"rounds={rounds}")

    # 清理：探针员工名下所有申请/轨迹 + 员工 + 账号（全部为本工具创建）
    e = admin.call("GET", "/api/employees:list",
                   params={"filter": jf({"employeeNo": BKW_NO})}).get("data", [{}])[0]
    reqs = admin.call("GET", "/api/confirmation_requests:list",
                      params={"filter": jf({"employeeId": e.get("id")})}).get("data", [])
    cleanup_requests(admin, [r["id"] for r in reqs])
    if e.get("id"):
        admin.call("POST", "/api/employees:destroy", params={"filterByTk": e["id"]})
    u = admin.call("GET", "/api/users:list",
                   params={"filter": jf({"email": BKW_EMAIL})}).get("data", [])
    if u:
        admin.call("POST", "/api/users:destroy", params={"filterByTk": u[0]["id"]})
    print(f"[bkw] 完成 {rounds} 轮写入并清理探针数据", flush=True)
    return summary()


if __name__ == "__main__":
    sys.exit(main())
