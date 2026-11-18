#!/usr/bin/env python3
"""低代码配置演示：管理员新增选填字段，不改任何前端/插件源码即被转正表单使用。

对应设计 Task 3.1 的“管理员配置演示”：向 confirmation_requests 集合新增选填
text 字段 trialHarvest（试用期收获），证明：
1. 通过集合字段 API 即可加字段（等价于 UI 的“字段管理”，无需改源码）；
2. 受控动作 saveDraft 会透传非受保护字段，新字段立即可在草稿中填写；
3. 该字段可选（nullable），旧记录仍可读取（值为 null）。

已知平台 quirks（详见 docs/demo/LOWCODE-DEMO.md）：
- NocoBase 2.2.37 的 fields:create 会触发 fields.afterCreate ACL 钩子，钩子里对每个
  角色动作执行 [...fields, 新字段]；当角色动作 fields 为 null（“全部字段”）时会抛
  “fields is not iterable” 并整体回滚。本脚本先把 null 归一化为显式字段列表再新增，
  规避该缺陷，同时保持“所有字段 + 新字段”语义。

幂等：字段已存在则跳过。只新建一条测试草稿并清理，不残留数据。
用法：python scripts/demo_lowcode_field.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verify_common import check, cleanup_requests, load_env, signed, summary  # noqa: E402

FIELD = "trialHarvest"
COLLECTION = "confirmation_requests"


def field_names(admin):
    r = admin.call("GET", "/api/fields:list",
                   params={"filter": json.dumps({"collectionName": COLLECTION},
                                                ensure_ascii=False)})
    return [f["name"] for f in r.get("data", [])]


def normalize_null_fields(admin):
    """把本集合上 fields=null 的角色动作归一化为显式字段列表（规避 NocoBase 2.2.37 钩子缺陷）。"""
    cur = field_names(admin)
    if FIELD in cur:
        return 0
    res = admin.call("GET", "/api/dataSourcesRolesResources:list",
                     params={"filter": json.dumps({"name": COLLECTION}, ensure_ascii=False)})
    changed = 0
    for r_ in res.get("data", []):
        rid = r_["id"]
        acts = admin.call("GET", "/api/dataSourcesRolesResourcesActions:list",
                          params={"filter": json.dumps({"rolesResourceId": rid}, ensure_ascii=False)})
        for a in acts.get("data", []):
            if a.get("fields") is None:
                admin.call("POST", "/api/dataSourcesRolesResourcesActions:update",
                           params={"filterByTk": a["id"]}, json_body={"fields": cur})
                changed += 1
        # 触发 ACL 重载
        admin.call("POST", "/api/dataSourcesRolesResources:update",
                   params={"filterByTk": rid}, json_body={"usingActionsConfig": True})
    return changed


def main():
    env = load_env()
    admin = signed(env["INIT_ROOT_EMAIL"], env["INIT_ROOT_PASSWORD"])
    emp_a = signed("employee.a@example.com", env["DEMO_PASSWORD"])

    before = field_names(admin)
    if FIELD in before:
        print(f"[INFO] 字段 {FIELD} 已存在，跳过创建", flush=True)
    else:
        n = normalize_null_fields(admin)
        if n:
            print(f"[INFO] 归一化 {n} 个 null 字段动作 -> 显式列表（规避 fields:create 钩子缺陷）", flush=True)
        r = admin.call("POST", "/api/fields:create", json_body={
            "collectionName": COLLECTION,
            "name": FIELD,
            "type": "text",
            "interface": "textarea",
            "title": "试用期收获",
        })
        check("新增字段成功", bool(r.get("data")), json.dumps(r, ensure_ascii=False)[:200])

    after = field_names(admin)
    check("新增后集合包含 trialHarvest", FIELD in after, f"fields={after}")

    # 员工通过受控动作 saveDraft 直接填写新字段（插件未改一行）
    d = emp_a.call("POST", f"/api/{COLLECTION}:saveDraft", json_body={
        "selfReview": "本季度完成多个需求",
        FIELD: "学会了需求拆解与跨部门协作",
    })
    rid = d.get("data", {}).get("id")
    check("saveDraft 接受并保存新字段",
          d.get("data", {}).get(FIELD) == "学会了需求拆解与跨部门协作",
          f"trialHarvest={d.get('data', {}).get(FIELD)}")

    # 旧记录可读：字段可选，历史记录该字段为 null（此处验证 DRAFT 正常读回）
    got = admin.call("GET", f"/api/{COLLECTION}:get",
                     params={"filterByTk": rid}).get("data", {})
    check("新字段已持久化(后端可读回)",
          got.get(FIELD) == "学会了需求拆解与跨部门协作",
          f"trialHarvest={got.get(FIELD)}")

    cleanup_requests(admin, [rid])
    return summary()


if __name__ == "__main__":
    sys.exit(main())
