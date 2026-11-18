#!/usr/bin/env python3
"""HRIS 验证脚本共享工具：登录、断言、清理。

只读 .env（本地），不打印真实密钥。用法见 verify_permissions.py / verify_business.py。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nb import NbClient  # noqa: E402

RESULTS = []


def load_env():
    env = {}
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v
    # 环境变量优先：便于把 NB_BASE / NB_DB_CONTAINER / HRIS_ALLOW_FAULT_INJECT 等
    # 指向隔离测试实例，而无需改动 .env（REV-007：测试 API 与 DB 目标保持一致）。
    for k, v in os.environ.items():
        if v:
            env[k] = v
    return env


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}"
    if detail:
        line += f"  -- {detail}"
    print(line, flush=True)
    return ok


def summary():
    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [(name, detail) for name, ok, detail in RESULTS if not ok]
    print("\n=== 结果汇总 ===", flush=True)
    print(f"通过 {passed} / {total}", flush=True)
    for name, detail in failed:
        print(f"  失败: {name} -- {detail}", flush=True)
    return 1 if failed else 0


def jf(obj):
    """JSON-encode a filter object for query params."""
    return json.dumps(obj, ensure_ascii=False)


def signed(email, password):
    c = NbClient(email=email, password=password)
    c.signin()
    return c


def status_of(client, method, path, json_body=None, params=None):
    """Return (status_code, parsed_json) for a request without raising on 4xx/5xx."""
    r = client.req(method, path, json_body=json_body, params=params)
    try:
        body = r.json()
    except ValueError:
        body = {"_text": r.text}
    return r.status_code, body


def data_list(client, path, params=None):
    """Return the 'data' array of a :list response."""
    r = client.call("GET", path, params=params)
    return r.get("data", [])


def reset_employee_status(admin, employee_id, status):
    """Root/admin 直接复位员工状态（仅验证脚本清理用）。"""
    return admin.call("POST", "/api/employees:update",
                      params={"filterByTk": employee_id},
                      json_body={"status": status})


def cleanup_requests(admin, request_ids):
    """删除指定申请及其审批轨迹（先删轨迹再删申请）。"""
    for rid in request_ids:
        acts = admin.call("GET", "/api/approval_actions:list",
                          params={"filter": jf({"requestId": rid})}).get("data", [])
        for a in acts:
            admin.call("POST", "/api/approval_actions:destroy", params={"filterByTk": a["id"]})
        admin.call("POST", "/api/confirmation_requests:destroy", params={"filterByTk": rid})
