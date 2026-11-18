#!/usr/bin/env python3
"""补齐集合字段元数据：中文标题 + 官方 uiSchema + 关联显示字段 + 选项枚举。

背景（REV-001 验收发现）：build_s2.py 用 collections:create 的 fields 数组建字段，
只给了 name/type/interface，没有 title/uiSchema。结果：
- 表格列头无文字（列头标题取自字段 title/uiSchema.title）；
- CollectionField 单元格解析不到组件（组件由字段 uiSchema 的 x-component 决定），
  单元格渲染为空白；
- 表单里的 CollectionField 同样渲染不出输入控件。

修复走 NocoBase 2.2.37 官方设计器同款路径：POST /api/fields:apply
（plugin-data-source-main modeling service 的 upsertFieldDefinition，
按 interface 生成标准 uiSchema，见 .tools/dsm-fields.js buildUiSchema）。
不触碰列类型/不删字段；m2o 只补 targetTitleField 控制列表里显示名称字段。

用法：python scripts/fix_field_meta.py   （.env 提供 INIT_ROOT_EMAIL/PASSWORD）
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nb import NbClient  # noqa: E402

# 每集合待补元数据：{字段名: {title, enum?, targetTitleField?}}
# m2o 的 target/foreignKey/targetKey 沿用库中现值（脚本自动带出）。
META = {
    "employees": {
        "employeeNo": {"title": "工号"},
        "name": {"title": "姓名"},
        "department": {"title": "部门", "targetTitleField": "name"},
        "jobTitle": {"title": "职位"},
        "manager": {"title": "直属主管", "targetTitleField": "name"},
        "user": {"title": "绑定账号", "targetTitleField": "nickname"},
        "hireDate": {"title": "入职日期"},
        "probationEndDate": {"title": "试用截止"},
        "status": {"title": "任职状态", "enum": [
            {"label": "试用中", "value": "PROBATION", "color": "orange"},
            {"label": "正式", "value": "REGULAR", "color": "green"},
            {"label": "离职", "value": "LEFT", "color": "red"},
        ]},
    },
    "departments": {
        "name": {"title": "部门名称"},
        "code": {"title": "部门编码"},
    },
    "confirmation_requests": {
        "employee": {"title": "申请人", "targetTitleField": "name"},
        "approver": {"title": "审批人", "targetTitleField": "nickname"},
        "status": {"title": "申请状态", "enum": [
            {"label": "草稿", "value": "DRAFT", "color": "default"},
            {"label": "待审批", "value": "PENDING", "color": "orange"},
            {"label": "已通过", "value": "APPROVED", "color": "green"},
            {"label": "已驳回", "value": "REJECTED", "color": "red"},
        ]},
        "selfReview": {"title": "自我评价"},
        "trialHarvest": {"title": "试用期收获"},
        "submittedAt": {"title": "提交时间"},
        "decidedAt": {"title": "审批时间"},
        "decisionComment": {"title": "审批意见"},
        "version": {"title": "版本号"},
    },
    "approval_actions": {
        "action": {"title": "操作", "enum": [
            {"label": "提交", "value": "SUBMIT", "color": "blue"},
            {"label": "通过", "value": "APPROVE", "color": "green"},
            {"label": "驳回", "value": "REJECT", "color": "red"},
        ]},
        "actorUser": {"title": "操作人", "targetTitleField": "nickname"},
        "comment": {"title": "意见"},
        "actedAt": {"title": "时间"},
    },
}


def load_env():
    env = {}
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(base, ".env"), encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v
    return env


def log(msg):
    print(f"[meta] {msg}", flush=True)


def main():
    env = load_env()
    admin = NbClient(email=env["INIT_ROOT_EMAIL"], password=env["INIT_ROOT_PASSWORD"])
    admin.signin()
    log(f"已登录: {admin.user.get('nickname')} @ {admin.base}")

    total, done, skipped = 0, 0, 0
    for coll, fields_meta in META.items():
        r = admin.call("GET", "/api/collections:get",
                       params={"filterByTk": coll, "appends": ["fields"], "autoGenerate": "false"})
        existing = {f["name"]: f for f in (r.get("data") or {}).get("fields") or []}
        for fname, meta in fields_meta.items():
            total += 1
            f = existing.get(fname)
            if not f:
                log(f"跳过（字段不存在）: {coll}.{fname}")
                skipped += 1
                continue
            ui_have = f.get("uiSchema") or {}
            enum_ok = ("enum" not in meta or (
                {item.get("value") for item in (ui_have.get("enum") or [])}
                >= {item["value"] for item in meta["enum"]}))
            if (ui_have.get("title") == meta["title"]
                    and ui_have.get("x-component") and enum_ok):
                log(f"已合规跳过: {coll}.{fname}")
                skipped += 1
                continue

            body = {
                "collectionName": coll,
                "name": fname,
                "interface": f.get("interface"),
                "type": f.get("type"),
                "title": meta["title"],
            }
            # 关联字段带出现有 target/键，保证 update 不漂移
            for k in ("target", "foreignKey", "targetKey"):
                if f.get(k):
                    body[k] = f[k]
            if f.get("interface") == "m2o" and meta.get("targetTitleField"):
                body["targetTitleField"] = meta["targetTitleField"]
            if meta.get("enum"):
                body["enum"] = meta["enum"]
            if f.get("defaultValue") is not None and fname == "status":
                body["defaultValue"] = f["defaultValue"]

            resp = admin.call("POST", "/api/fields:apply", json_body=body)
            ok = isinstance(resp, dict) and resp.get("data")
            if ok:
                done += 1
            else:
                log(f"FAIL {coll}.{fname} resp={json.dumps(resp, ensure_ascii=False)[:200]}")
                return 1

    # 终验：回读全部字段，确认 uiSchema 齐备
    bad = []
    for coll, fields_meta in META.items():
        r = admin.call("GET", "/api/collections:get",
                       params={"filterByTk": coll, "appends": ["fields"], "autoGenerate": "false"})
        existing = {f["name"]: f for f in (r.get("data") or {}).get("fields") or []}
        for fname, meta in fields_meta.items():
            ui = existing.get(fname, {}).get("uiSchema") or {}
            okf = (ui.get("title") == meta["title"] and ui.get("x-component")
                   and ("enum" not in meta or ui.get("enum")))
            log(f"{'OK ' if okf else 'BAD'} {coll}.{fname} "
                f"uiSchema.title={ui.get('title')} x-component={ui.get('x-component')} "
                f"enum={'yes' if ui.get('enum') else 'no'}")
            if not okf:
                bad.append(f"{coll}.{fname}")

    log(f"== 完成: 处理 {total} 字段，更新 {done}，跳过 {skipped}；终验异常 {len(bad)} {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
