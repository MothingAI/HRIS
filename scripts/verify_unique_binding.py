#!/usr/bin/env python3
"""REV-003 复核探针：普通字段配置后，账号唯一绑定必须持续成立。

对应验收要求（docs/quality/ACCEPTANCE.md REV-003）：
- 连续新增两个不同字段（官方 fields:create，普通选填 string），全程不重启、
  不跑任何“补索引”脚本——每一步之后 userId 唯一索引必须在库；fields:apply
  （fix_field_meta 同款路径，曾触发索引被删）同样核验；
- 然后测重复 create / 重复 update 换绑 / 关系赋值 / NULL 多个 / 并发绑定：
  重复绑定必须被拒绝，并发首次绑定只允许一个成功；
- 约束消失时的字段同步会被平台阻止（重复数据在场时 SyncRunner 建唯一索引
  失败，该次字段操作报错回滚）——属设计兜底，本探针在健康库上不触发。

前置：隔离实例（NB_BASE / NB_APP_CONTAINER / NB_DB_CONTAINER 显式指向同一
Compose 项目；本脚本执行 SQL 前经 target_binding 校验同源）。建议只在隔离
实例运行——它会向 employees 模型永久添加两个探针字段（uniqProbeA/B，选填、
无 UI 影响），幂等可重跑。

用法：python scripts/verify_unique_binding.py
退出码：0 = 全部通过；1 = 存在失败。
"""
import concurrent.futures
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from demo_lowcode_employee_field import (normalize_null_fields,  # noqa: E402
                                         restore_null_fields)
from nb import NbClient  # noqa: E402
from target_binding import verify_target  # noqa: E402
from verify_common import check, jf, load_env, signed, status_of, summary  # noqa: E402

PROBE_FIELDS = [
    {"name": "uniqProbeA", "title": "唯一性探针A"},
    {"name": "uniqProbeB", "title": "唯一性探针B"},
]


def run_sql(env, sql):
    e = dict(os.environ)
    e["PGPASSWORD"] = env["POSTGRES_PASSWORD"]
    r = subprocess.run(
        ["docker", "exec", env["NB_DB_CONTAINER"], "psql",
         "-U", env["POSTGRES_USER"], "-d", env["POSTGRES_DB"],
         "-t", "-A", "-c", sql],
        capture_output=True, text=True, env=e)
    if r.returncode != 0:
        raise RuntimeError(f"SQL 失败: {r.stderr[:200]}")
    return (r.stdout or "").strip()


def unique_user_index(env):
    """employees.\"userId\" 列上的唯一索引数（按列找，不依赖索引名）。"""
    return run_sql(env,
                   "SELECT count(*) FROM pg_indexes WHERE tablename='employees' "
                   "AND indexdef LIKE 'CREATE UNIQUE%(\"userId\")%'")


def create_field(admin, body):
    """官方 fields:create（带 null 白名单归一化规避 2.2.37 ACL 钩子缺陷）。"""
    null_ids = normalize_null_fields(admin)
    try:
        return status_of(admin, "POST", "/api/fields:create", json_body={
            "collectionName": "employees", "type": "string",
            "interface": "input", **body})
    finally:
        restore_null_fields(admin, null_ids)


def main():
    env = load_env()
    # SQL 写入/索引检查前先钉死目标（REV-007：API/app/DB 同一隔离项目）。
    # 本探针会写字段/员工/关系数据：任何写入前要求显式隔离实例且非主项目。
    binding = verify_target(env, "唯一绑定探针（写入前置门禁）",
                            require_explicit=True, forbid_main=True)
    print(f"[bind] 目标一致: {binding['base']} / {binding['app']} / "
          f"{binding['db']} (project={binding['project']})", flush=True)

    admin = signed(env["INIT_ROOT_EMAIL"], env["INIT_ROOT_PASSWORD"])

    e002 = admin.call("GET", "/api/employees:list",
                      params={"filter": jf({"employeeNo": "E002"})}).get("data")[0]
    uid_a = e002["userId"]
    check("前置：E002 已绑定账号", bool(uid_a), f"userId={uid_a}")

    # ---- 0. 起点：唯一索引在库 ----
    check("起点：userId 唯一索引在库", unique_user_index(env) != "0",
          f"uniq={unique_user_index(env)}")

    # ---- 1. 连续新增两个不同字段，每步之后索引仍在（不重启、不补建） ----
    for body in PROBE_FIELDS:
        exists = admin.call("GET", "/api/fields:list", params={
            "filter": jf({"collectionName": "employees", "name": body["name"]})}
        ).get("data", [])
        if exists:
            # 重跑：先删再建（fields:destroy 同样触发表同步，顺带覆盖该路径）
            code, resp = status_of(admin, "POST", "/api/fields:destroy",
                                   params={"filterByTk": exists[0]["key"]})
            check(f"fields:destroy {body['name']}（重跑重置）成功", code == 200,
                  f"status={code}")
            check(f"删字段 {body['name']} 后唯一索引仍在",
                  unique_user_index(env) != "0", f"uniq={unique_user_index(env)}")
        code, resp = create_field(admin, body)
        check(f"fields:create {body['name']} 返回 200", code == 200,
              f"status={code} {json.dumps(resp, ensure_ascii=False)[:120]}")
        check(f"建字段 {body['name']} 后唯一索引仍在", unique_user_index(env) != "0",
              f"uniq={unique_user_index(env)}")

    # fields:apply 再走一遍（fix_field_meta 同款路径）
    code, resp = status_of(admin, "POST", "/api/fields:apply", json_body={
        "collectionName": "employees", "name": PROBE_FIELDS[0]["name"],
        "interface": "input", "type": "string", "title": PROBE_FIELDS[0]["title"]})
    check("fields:apply 探针字段成功", code == 200, f"status={code}")
    check("fields:apply 后唯一索引仍在", unique_user_index(env) != "0",
          f"uniq={unique_user_index(env)}")

    # ---- 2. 重复 create：HR 用已绑定账号建第二名员工 → 必须失败 ----
    hr = signed("hr@example.com", env["DEMO_PASSWORD"])
    code, resp = status_of(hr, "POST", "/api/employees:create", json_body={
        "employeeNo": "UNIQ-PROBE-DUP", "name": "唯一性探针重复",
        "user": {"id": uid_a}})
    check("重复 create（已绑定账号）被拒绝", code != 200,
          f"status={code} {json.dumps(resp, ensure_ascii=False)[:150]}")
    dup_rows = admin.call("GET", "/api/employees:list",
                          params={"filter": jf({"userId": uid_a})}).get("data", [])
    check("该账号仍只绑定一名员工", len(dup_rows) == 1,
          f"bound={[r.get('employeeNo') for r in dup_rows]}")
    for r in dup_rows:  # 防御性清理（正常路径创建已失败，不应有残留）
        if r.get("employeeNo") == "UNIQ-PROBE-DUP":
            admin.call("POST", "/api/employees:destroy", params={"filterByTk": r["id"]})

    # ---- 3. 重复 update 换绑：把 E004 改绑到 E002 的账号 → 必须失败 ----
    e004 = admin.call("GET", "/api/employees:list",
                      params={"filter": jf({"employeeNo": "E004"})}).get("data")[0]
    code, resp = status_of(hr, "POST", "/api/employees:update",
                           params={"filterByTk": e004["id"]},
                           json_body={"user": {"id": uid_a}})
    check("重复 update 换绑被拒绝", code != 200,
          f"status={code} {json.dumps(resp, ensure_ascii=False)[:150]}")
    e004_after = admin.call("GET", "/api/employees:list",
                            params={"filter": jf({"employeeNo": "E004"}),
                                    "appends": ["user"]}).get("data")[0]
    check("E004 绑定未变化", (e004_after.get("user") or {}).get("id") != uid_a,
          f"user={(e004_after.get('user') or {}).get('id')}")

    # ---- 4. 关系字段直接赋值（与 update 同语义的关系写入路径） ----
    code, resp = status_of(hr, "POST", "/api/employees:update",
                           params={"filterByTk": e004["id"]},
                           json_body={"user": uid_a})
    check("关系字段直接赋值换绑同样被拒绝", code != 200,
          f"status={code} {json.dumps(resp, ensure_ascii=False)[:120]}")

    # ---- 5. NULL 多个：多名未绑定员工合法 ----
    nulls = run_sql(env, 'SELECT count(*) FROM employees WHERE "userId" IS NULL')
    check("允许多名未绑定（NULL）员工", nulls not in ("0", ""),
          f"null_employees={nulls}")

    # ---- 6. 并发绑定：只允许一个成功 ----
    # 6a. 并发把两名探针员工绑到【已绑定】账号 → 全部拒绝
    probe_ids = []
    for no in ("UNIQ-PROBE-C1", "UNIQ-PROBE-C2"):
        r = admin.call("POST", "/api/employees:create", json_body={
            "employeeNo": no, "name": f"并发探针{no[-1]}"})
        probe_ids.append(r["data"]["id"])

    def bind(emp_id, target_uid):
        c = NbClient(email="hr@example.com", password=env["DEMO_PASSWORD"])
        c.signin()
        return c.req("POST", "/api/employees:update",
                     params={"filterByTk": emp_id},
                     json_body={"user": {"id": target_uid}}).status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        codes = sorted([f.result() for f in
                        (ex.submit(bind, probe_ids[0], uid_a),
                         ex.submit(bind, probe_ids[1], uid_a))])
    check("并发绑到已绑定账号：全部被拒", codes[0] != 200, f"codes={codes}")

    # 6b. 解绑 E002 后并发【首次】绑定同一账号 → 恰一个 200
    admin.call("POST", "/api/employees:update", params={"filterByTk": e002["id"]},
               json_body={"userId": None})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        codes = sorted([f.result() for f in
                        (ex.submit(bind, probe_ids[0], uid_a),
                         ex.submit(bind, probe_ids[1], uid_a))])
    check("并发首次绑定恰一个成功 [200,非200]", codes[0] == 200 and codes[1] != 200,
          f"codes={codes}")
    bound_cnt = run_sql(env, f'SELECT count(*) FROM employees WHERE "userId"={uid_a}')
    check("并发后该账号只绑定一名员工", bound_cnt == "1", f"count={bound_cnt}")

    # ---- 清理：只清本探针创建的资源（探针员工），并还原 E002 绑定 ----
    for emp_id in probe_ids:
        admin.call("POST", "/api/employees:update", params={"filterByTk": emp_id},
                   json_body={"userId": None})
        admin.call("POST", "/api/employees:destroy", params={"filterByTk": emp_id})
    admin.call("POST", "/api/employees:update", params={"filterByTk": e002["id"]},
               json_body={"userId": uid_a})
    ok_e002 = admin.call("GET", "/api/employees:list",
                         params={"filter": jf({"employeeNo": "E002"}),
                                 "appends": ["user"]}).get("data")[0]
    check("清理后 E002 绑定还原", (ok_e002.get("user") or {}).get("id") == uid_a,
          f"user={(ok_e002.get('user') or {}).get('id')}")

    return summary()


if __name__ == "__main__":
    sys.exit(main())
