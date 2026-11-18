#!/usr/bin/env python3
"""S4 页面构建 v2：按 NocoBase 2.2.37 官方形态重建 6 个业务页面。

背景（REV-001 渲染阻塞根因，2026-11-10 定位）：
- v1 页面官方形态：desktopRoutes(page, schemaUid=s, enableTabs=false)
  + tabs 子路由 {schemaUid=d, tabSchemaName=u, hidden=true}；
  页面 schema：Page(x-uid=s) -> {u: Grid(x-uid=d, x-async=true, x-initializer=page:addBlock)}。
  旧 build_ui.py 把 tabs.schemaUid 误设为页面根 uid，导致客户端二次渲染 Page；
  非 root 的 listAccessible 给 tabs 子路由序列化 children=[]，二次渲染即崩溃。
- 区块（TableV2/FormV2）也必须用官方模板（TableBlockProvider/CardItem/
  TableV2.Column.Decorator 等），手工猜测的形态会触发 formily 内部错误。

2026-11-11 N2 增量（操作体验收尾；区块刷新机制经官方 bundle 反解 + 浏览器实测）：
- 受控按钮（保存草稿/保存修改/提交/通过/确认驳回）统一用
  x-action-settings.onSuccess.blocksToRefresh 显式列出要刷新的表格区块 uid——
  这是 Action 组件包装器在 onClick 完成后按 uid 调 service.refresh() 的通用机制，
  对任何按钮类型有效。自己的表格也必须列出：useCustomizeRequestActionProps 的
  __parent 刷新只覆盖按钮直接所在区块，抽屉表单内的按钮其 __parent 是表单而非表格。
- x-component-props.refreshDataBlockRequest 对弹窗打开型按钮（customize:popup /
  create / update 无自定义 onClick）不生效——bundle 中刷新回调只传给带自定义
  onClick 的按钮；不再依赖该标志（原生 create/update 抽屉保留，走平台 stock 路径）。
- 我的转正：新建/重新申请表单放"我的草稿"表工具栏抽屉 + DRAFT 行（编辑草稿抽屉
  走 saveDraft 带 requestId、提交）+ "历史申请"表（非 DRAFT 只读 + 查看详情与
  轨迹）；按状态拆表保证"状态不合法的按钮不出现"。
- 待我审批：PENDING 待办 + 通过/驳回 + 查看详情与轨迹（审批轨迹走
  confirmation_requests.approvalActions 关联表格，复用 approval_actions 集合）。
- 员工/部门管理（HR）：内置筛选按钮（Filter.Action）+ 原生"新增/编辑"抽屉
  （x-action create/update，走平台 ACL 与 HR 字段白名单，不含 status）。

2026-11-12 N3 增量（人事概览统计页，结构全部经 @nocobase/plugin-data-visualization
2.2.37 客户端 bundle 反解核实）：
- 图表区块两层结构：ChartCardItem（x-decorator: ChartBlockProvider，内含
  Grid(x-decorator: ChartV2Block, x-initializer: charts:addBlock)）→ Grid 内每个
  渲染器按官方 gridRowColWrap 包 Grid.Row/Grid.Col；渲染器 x-decorator:
  ChartRendererProvider + x-decorator-props={query, config, dataSource,
  collection, mode:"builder"}（设计器 onOk 保存时的精确形态）。
- 基础 charts 插件（免 echarts）注册两组图表：组 "antd" 下 statistic（计数卡，
  config.field 取查询别名，value=data[0][field]）与 table（自动按结果键出列）。
  chartType 写法 "antd.statistic" / "antd.table"。
- 统计走服务端 charts:queryData（结构化 builder 查询），经 plugin-acl
  applyQueryPermission：view 动作 + 数据范围 filter + 字段白名单裁剪，实测
  员工查"全公司在职"仍只得本人行（范围不泄露）；系统字段 id/version 不在白名单，
  count 必须挂在业务字段（employeeNo/status）上，否则 403。
- 离职人数卡（LEFT）为部门在职表的同口径补充，非 N3 硬性要求。

前置：build_ui.py（路由）→ fix_field_meta.py（字段元数据）→ 本脚本；
关联表格依赖 build_s2.py 的 ensure_o2m_relation()（confirmation_requests.approvalActions）。

用法：python scripts/build_pages_v2.py   （.env 提供 INIT_ROOT_EMAIL/PASSWORD）
"""
import json
import os
import random
import string
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nb import NbClient  # noqa: E402

UID_ALPHABET = string.ascii_lowercase + "0123456789"
MARKER = "build_pages_v2"
API = "/confirmation_requests:"  # 注意：不带 /api 前缀——客户端 APIClient baseURL=/api 会自动拼接


def uid():
    return "".join(random.choices(UID_ALPHABET, k=11))


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
    print(f"[v2] {msg}", flush=True)


# ---- 官方模板（与 2.2.37 客户端设计器生成的结构逐字段一致） --------------------

def col_field(coll, fname, ftype="string"):
    """表格列内字段节点（CollectionField 只读展示）。"""
    return {
        "type": ftype,
        "x-component": "CollectionField",
        "x-read-pretty": True,
        "x-collection-field": f"{coll}.{fname}",
    }


def table_column(coll, fname, ftype="string"):
    """官方列壳：TableV2.Column.Decorator + TableV2.Column + 字段。"""
    return {
        "type": "void",
        "x-decorator": "TableV2.Column.Decorator",
        "x-toolbar": "TableColumnSchemaToolbar",
        "x-settings": "fieldSettings:TableColumn",
        "x-component": "TableV2.Column",
        "properties": {fname: col_field(coll, fname, ftype)},
    }


def custom_request_btn(title, url, x_action="customize:table:request",
                       component="Action.Link", data=None, success=None,
                       primary=False, html_submit=False, refresh_blocks=None):
    """官方 Custom request 按钮：useCustomizeRequestActionProps。
    refresh_blocks: 额外刷新的兄弟区块 uid 列表（x-action-settings.onSuccess.
    blocksToRefresh，Action 组件通用机制，2026-11-11 从官方 bundle 反解验证）。
    按钮所在区块由 hook 自身刷新（__parent/own service），兄弟区块需显式列出。"""
    comp_props = {}
    if primary:
        comp_props["type"] = "primary"
    if html_submit:
        comp_props["htmlType"] = "submit"
    settings = {
        "requestSettings": {"url": url, "method": "POST"},
        "onSuccess": {
            "successMessage": success or f"{title}成功",
            "manualClose": False,
            "redirecting": False,
        },
    }
    if refresh_blocks:
        settings["onSuccess"]["blocksToRefresh"] = list(refresh_blocks)
    if data is not None:
        settings["requestSettings"]["data"] = data
    return {
        "type": "void",
        "title": title,
        "x-component": component,
        "x-action": x_action,
        "x-align": "left",
        "x-use-component-props": "useCustomizeRequestActionProps",
        "x-component-props": comp_props,
        "x-action-settings": settings,
    }


def actions_column(buttons):
    """官方操作列（x-action-column）+ DndContext/Space 容器装行按钮。"""
    return {
        "type": "void",
        "title": "操作",
        "x-action-column": "actions",
        "x-decorator": "TableV2.Column.ActionBar",
        "x-component": "TableV2.Column",
        "x-toolbar": "TableColumnSchemaToolbar",
        "x-initializer": "table:configureItemActions",
        "x-toolbar-props": {"initializer": "table:configureItemActions"},
        "properties": {
            uid(): {
                "type": "void",
                "x-decorator": "DndContext",
                "x-component": "Space",
                "x-component-props": {"split": "|"},
                "properties": {f"btn_{i}_{b['title']}": b for i, b in enumerate(buttons)},
            }
        },
    }


def table_block(coll, columns, row_buttons=None, static_filter=None,
                association=None, toolbar_buttons=None, blk_uid=None):
    """官方 TableBlock 区块（emZ 模板）。columns: [(field, type)]。
    association="collection.field" 时为关联数据区块（抽屉内展示当前行关联记录，
    如 confirmation_requests.approvalActions 审批轨迹）。
    toolbar_buttons 放表格顶部 ActionBar（如内置筛选 / 新增）。
    blk_uid 可显式指定区块 uid，供其他按钮的 blocksToRefresh 引用。"""
    cols = {f"col_{f}": table_column(coll, f, t) for f, t in columns}
    if row_buttons:
        cols["actions"] = actions_column(row_buttons)
    params = {"pageSize": 20}
    if static_filter:
        params["filter"] = static_filter
    deco = {
        "collection": coll,
        "dataSource": "main",
        "action": "list",
        "params": params,
        "showIndex": True,
        "dragSort": False,
    }
    if association:
        deco["association"] = association
    toolbar = dict(toolbar_buttons or {})
    blk_uid = blk_uid or uid()
    return {
        "type": "void",
        "x-uid": blk_uid,
        "x-decorator": "TableBlockProvider",
        "x-acl-action": f"{coll}:list",
        "x-use-decorator-props": "useTableBlockDecoratorProps",
        "x-decorator-props": deco,
        "x-toolbar": "BlockSchemaToolbar",
        "x-settings": "blockSettings:table",
        "x-component": "CardItem",
        "x-filter-targets": [],
        "properties": {
            "actions": {
                "type": "void",
                "x-initializer": "table:configureActions",
                "x-component": "ActionBar",
                "x-component-props": {"style": {"marginBottom": "var(--nb-spacing)"}},
                "properties": toolbar,
            },
            uid(): {
                "type": "array",
                "x-initializer": "table:configureColumns",
                "x-component": "TableV2",
                "x-use-component-props": "useTableBlockProps",
                "x-component-props": {"rowKey": "id"},
                "properties": cols,
            },
        },
    }


def filter_action():
    """官方内置筛选按钮（Filter.Action + useFilterActionProps）：
    打开平台筛选构造器，按集合字段过滤当前表格，无需手写筛选表单接线。"""
    return {
        "type": "void",
        "title": "筛选",
        "x-action": "filter",
        "x-toolbar": "ActionSchemaToolbar",
        "x-settings": "actionSettings:filter",
        "x-component": "Filter.Action",
        "x-use-component-props": "useFilterActionProps",
        "x-component-props": {"icon": "FilterOutlined"},
    }


# ---- 图表区块（N3 人事概览；结构经 charts 插件 2.2.37 bundle 反解核实） ----------

def chart_renderer(coll, query, chart_type, title, field=None):
    """官方 ChartRenderer 节点：ChartRendererProvider + ChartRenderer。
    x-decorator-props 是设计器 onOk 保存的精确形态（query/config/dataSource/
    collection/mode）。渲染组件从 config.general.field 取值列（=查询别名，
    bundle 实测：ChartRenderer 读 c.general/c.advanced，根级 field 无效；
    table 图表的 props 会被 general 整体透传给 antd Table，title 必须是函数，
    故 table 不写 general）。"""
    cfg = {
        "chartType": chart_type,
        "title": title,
        "bordered": False,
        "advanced": {},
    }
    if field:  # statistic 计数卡
        cfg["general"] = {"title": title, "field": field}
    return {
        "type": "void",
        "x-decorator": "ChartRendererProvider",
        "x-decorator-props": {
            "query": query,
            "config": cfg,
            "dataSource": "main",
            "collection": coll,
            "mode": "builder",
        },
        "x-acl-action": f"{coll}:list",
        "x-toolbar": "ChartRendererToolbar",
        "x-settings": "chart:renderer",
        "x-component": "CardItem",
        "x-component-props": {"size": "small", "title": title, "bordered": False},
        "x-initializer": "charts:addBlock",
        "properties": {
            "actions": {
                "type": "void",
                "x-decorator": "div",
                "x-decorator-props": {"style": {"position": "absolute", "top": 0,
                                                 "right": 0, "zIndex": 10}},
                "x-component": "ActionBar",
                "x-component-props": {"style": {
                    "marginRight": "var(--nb-designer-offset)",
                    "marginTop": "var(--nb-designer-offset)"}},
                "x-initializer": "chart:configureActions",
            },
            uid(): {"type": "void", "x-component": "ChartRenderer"},
        },
    }


def chart_card_block(renderers, widths=None):
    """官方 ChartCardItem 区块：ChartBlockProvider + 内层 Grid（ChartV2Block）。
    每个渲染器按官方 gridRowColWrap 包 Grid.Row/Grid.Col；widths 提供时写
    Grid.Col 宽度（百分比数值），让多张卡并排。"""
    rows = {}
    for i, r in enumerate(renderers):
        col = {"type": "void", "x-component": "Grid.Col", "properties": {f"chart{i}": r}}
        if widths:
            col["x-component-props"] = {"width": widths[i]}
        rows[f"row{i}"] = {
            "type": "void",
            "x-component": "Grid.Row",
            "properties": {f"col{i}": col},
        }
    return {
        "type": "void",
        "x-component": "ChartCardItem",
        "x-use-component-props": "useChartBlockCardProps",
        "x-settings": "chart:block",
        "x-decorator": "ChartBlockProvider",
        "properties": {
            "actions": {
                "type": "void",
                "x-component": "ActionBar",
                "x-component-props": {"style": {"marginBottom":
                                                "var(--nb-designer-offset)"}},
                "x-initializer": "chartBlock:configureActions",
            },
            uid(): {
                "type": "void",
                "x-component": "Grid",
                "x-decorator": "ChartV2Block",
                "x-initializer": "charts:addBlock",
                "properties": rows,
            },
        },
    }


def count_query(field, flt=None):
    """count 聚合查询（builder 模式）。count 必须挂业务字段：系统字段 id/version
    不在角色字段白名单内，会被 applyQueryPermission 裁剪成 403。"""
    return {
        "measures": [{"field": [field], "aggregation": "count", "alias": "count"}],
        "dimensions": [],
        "filter": flt or {},
        "orders": [],
    }


def form_field(coll, fname, title, ftype="string", required=False,
               collection_bound=True, read_pretty=False):
    """表单字段节点。collection_bound=False 时是纯 Input.TextArea（如驳回意见 comment，
    值仍会进 form.values，由 customize:form:request 合并进请求体）。
    read_pretty=True 用于详情展示（只读态渲染，等同表格单元格的展示组件）。"""
    node = {
        "type": ftype,
        "title": title,
        "x-component": "CollectionField" if collection_bound else "Input.TextArea",
        "x-decorator": "FormItem",
    }
    if collection_bound:
        node["x-collection-field"] = f"{coll}.{fname}"
    if read_pretty:
        node["x-read-pretty"] = True
    if required:
        node["x-decorator-props"] = {"rules": [{"required": True, "message": "必填"}]}
    return node


def create_form_block(coll, fields, buttons, edit_record=False, read_pretty=False):
    """官方 Form 区块（em5 模板）。edit_record=True 时按官方行级抽屉表单形态
    （action:get + useParamsFromRecord + useFormBlockProps，加载当前行记录，
    使表单内按钮的 currentRecord 可用——create 形态的空 RecordProvider 会覆盖行记录）。
    fields: [(name, title, type, required, collection_bound)]。
    read_pretty=True 时字段只读展示（详情视图）。"""
    grid_props = {}
    for fname, ftitle, ftype, req, bound in fields:
        grid_props[fname] = {
            "type": "void",
            "x-component": "Grid.Row",
            "properties": {
                uid(): {
                    "type": "void",
                    "x-component": "Grid.Col",
                    "properties": {
                        fname: form_field(coll, fname, ftitle, ftype, req, bound,
                                          read_pretty=read_pretty),
                    },
                }
            },
        }
    action_props = {
        f"act_{i}_{b['title']}": b for i, b in enumerate(buttons)
    }
    if edit_record:
        decorator = {
            "x-use-decorator-props": "useEditFormBlockDecoratorProps",
            "x-decorator-props": {
                "resource": coll,
                "collection": coll,
                "dataSource": "main",
                "action": "get",
                "useParams": "{{ useParamsFromRecord }}",
            },
        }
        form_props_hook = "useFormBlockProps"
    else:
        decorator = {
            "x-use-decorator-props": "useCreateFormBlockDecoratorProps",
            "x-decorator-props": {
                "collection": coll,
                "dataSource": "main",
                "association": None,
                "isCusomeizeCreate": False,
            },
        }
        form_props_hook = "useCreateFormBlockProps"
    return {
        "type": "void",
        "x-decorator": "FormBlockProvider",
        **decorator,
        "x-toolbar": "BlockSchemaToolbar",
        "x-settings": "blockSettings:createForm",
        "x-component": "CardItem",
        "properties": {
            uid(): {
                "type": "void",
                "x-component": "FormV2",
                "x-use-component-props": form_props_hook,
                "properties": {
                    "grid": {
                        "type": "void",
                        "x-component": "Grid",
                        "x-initializer": "form:configureFields",
                        "properties": grid_props,
                    },
                    uid(): {
                        "type": "void",
                        "x-initializer": "createForm:configureActions",
                        "x-component": "ActionBar",
                        "x-component-props": {"layout": "one-column"},
                        "properties": action_props,
                    },
                },
            }
        },
    }


def native_submit(title, use_props):
    """官方原生提交按钮（create/update 表单）：走平台 ACL 的 create/update 动作。"""
    return {
        "type": "void",
        "title": title,
        "x-component": "Action",
        "x-action": "submit",
        "x-use-component-props": use_props,
        "x-component-props": {"type": "primary", "htmlType": "submit"},
    }


def grid_of_blocks(blocks):
    """抽屉体内的 Grid：每个区块一行（Grid 直挂子块不渲染，必须包 Row/Col）。"""
    rows = {}
    for i, b in enumerate(blocks):
        rows[f"row{i}"] = {
            "type": "void",
            "x-component": "Grid.Row",
            "properties": {
                f"col{i}": {
                    "type": "void",
                    "x-component": "Grid.Col",
                    "properties": {f"blk{i}": b},
                }
            },
        }
    return {"type": "void", "x-component": "Grid", "properties": rows}


def drawer_container(title, blocks):
    """官方 Action.Container 抽屉 + Grid 包裹的区块列表（驳回抽屉同款形态）。"""
    return {
        "type": "void",
        "title": title,
        "x-component": "Action.Container",
        "x-component-props": {"className": "nb-action-popup"},
        "properties": {"grid": grid_of_blocks(blocks)},
    }


def drawer_link_button(title, drawer_title, blocks, refresh=True):
    """行按钮 -> 抽屉（查看详情/轨迹、编辑草稿等）。blocks 为抽屉内区块列表。"""
    btn = {
        "type": "void",
        "title": title,
        "x-component": "Action.Link",
        "x-action": "customize:popup",
        "x-align": "left",
        "x-component-props": {"openMode": "drawer",
                              "refreshDataBlockRequest": refresh},
        "properties": {"drawer": drawer_container(drawer_title, blocks)},
    }
    return btn


def create_form_drawer_button(title, drawer_title, coll, fields, save_btn):
    """工具栏"新建转正申请"按钮：抽屉内 create 表单 + 保存草稿（受控 saveDraft）。
    保存成功后 hook 默认关闭抽屉，refreshDataBlockRequest 触发父表格刷新——
    平铺表单的兄弟表格无法被 hook 刷新，抽屉形态是官方推荐解法。"""
    return {
        "type": "void",
        "title": title,
        "x-component": "Action",
        "x-action": "customize:popup",
        "x-align": "right",
        "x-toolbar": "ActionSchemaToolbar",
        "x-component-props": {"openMode": "drawer", "refreshDataBlockRequest": True},
        "properties": {"drawer": drawer_container(
            drawer_title, [create_form_block(coll, fields, [save_btn])])},
    }


def native_create_drawer(coll, fields, drawer_title):
    """官方"新增"按钮（表格 ActionBar，右对齐）+ 原生 create 表单抽屉。
    fields 与 HR 角色字段白名单一致（不含 status）。"""
    submit = native_submit("提交", "useCreateActionProps")
    return {
        "type": "void",
        "title": "新增",
        "x-component": "Action",
        "x-action": "create",
        "x-acl-action": "create",
        "x-align": "right",
        "x-decorator": "ACLActionProvider",
        "x-toolbar": "ActionSchemaToolbar",
        "x-settings": "actionSettings:addNew",
        "x-component-props": {"openMode": "drawer", "type": "default",
                              "refreshDataBlockRequest": True},
        "properties": {"drawer": drawer_container(
            drawer_title, [create_form_block(coll, fields, [submit])])},
    }


def native_edit_drawer(coll, fields, drawer_title):
    """官方行级"编辑"按钮（x-action: update，走平台 ACL）+ 原生 update 表单抽屉。"""
    submit = native_submit("提交", "useUpdateActionProps")
    return {
        "type": "void",
        "title": "编辑",
        "x-component": "Action.Link",
        "x-action": "update",
        "x-acl-action": "update",
        "x-align": "left",
        "x-decorator": "ACLActionProvider",
        "x-toolbar": "ActionSchemaToolbar",
        "x-settings": "actionSettings:edit",
        "x-component-props": {"openMode": "drawer", "refreshDataBlockRequest": True},
        "properties": {"drawer": drawer_container(
            drawer_title, [create_form_block(coll, fields, [submit], edit_record=True)])},
    }


def reject_popup_button(pending_uid):
    """驳回：官方 Popup 抽屉 + 纯 comment 输入 + 表单内提交按钮（受控动作 reject）。

    注意：提交按钮必须放在 FormV2 内部的 ActionBar（同"保存草稿"模式）。
    Action.Container.Footer 里的按钮不在 FormBlockProvider 的 React 上下文中，
    customize:form:request 找不到 form，点击不会发请求（2026-11-10 实测）。
    pending_uid: 待我审批表格区块 uid——表单内按钮的 hook 只刷新表单自身，
    必须经 blocksToRefresh 显式刷新背后的待办表，驳回后行才会消失。
    """
    confirm_btn = custom_request_btn(
        "确认驳回", f"{API}reject",
        x_action="customize:form:request",
        component="Action", primary=True, html_submit=True,
        data='{"requestId": "{{currentRecord.id}}"}',
        success="已驳回",
        refresh_blocks=[pending_uid],
    )
    return {
        "type": "void",
        "title": "驳回",
        "x-component": "Action.Link",
        "x-action": "customize:popup",
        "x-align": "left",
        "x-component-props": {"openMode": "drawer", "refreshDataBlockRequest": True},
        "properties": {
            "drawer": {
                "type": "void",
                "title": "驳回申请",
                "x-component": "Action.Container",
                "x-component-props": {"className": "nb-action-popup"},
                "properties": {
                    "grid": {
                        "type": "void",
                        "x-component": "Grid",
                        "properties": {
                            "row_comment": {
                                "type": "void",
                                "x-component": "Grid.Row",
                                "properties": {
                                    "col_comment": {
                                        "type": "void",
                                        "x-component": "Grid.Col",
                                        "properties": {
                                            "comment_form": create_form_block(
                                                "confirmation_requests",
                                                [("comment", "审批意见", "string", True, False)],
                                                [confirm_btn],
                                                edit_record=True,
                                            ),
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            }
        },
    }


# ---- 页面定义 -----------------------------------------------------------------

EMP_COLS = [("employeeNo", "string"), ("name", "string"), ("department", "string"),
            ("jobTitle", "string"), ("manager", "string"), ("hireDate", "string"),
            ("probationEndDate", "string"), ("status", "string")]

# HR 可维护字段（与 build_s2.py HR_EDITABLE_FIELDS 一致；不含 status——状态只能走审批流转）
HR_EMP_FORM = [
    ("employeeNo", "工号", "string", True, True),
    ("name", "姓名", "string", True, True),
    ("department", "部门", "string", False, True),
    ("jobTitle", "职位", "string", False, True),
    ("manager", "直属主管", "string", False, True),
    ("hireDate", "入职日期", "string", False, True),
    ("probationEndDate", "试用截止", "string", False, True),
]
HR_DEPT_FORM = [
    ("name", "部门名称", "string", True, True),
    ("code", "部门编码", "string", True, True),
]

REQ_CONTENT_FIELDS = [
    ("selfReview", "自我评价", "string", False, True),
    ("trialHarvest", "试用期收获", "string", False, True),
]
REQ_DETAIL_FIELDS = [
    ("status", "申请状态", "string", False, True),
    ("employee", "申请人", "string", False, True),
    ("approver", "审批人", "string", False, True),
    ("selfReview", "自我评价", "string", False, True),
    ("trialHarvest", "试用期收获", "string", False, True),
    ("submittedAt", "提交时间", "string", False, True),
    ("decidedAt", "审批时间", "string", False, True),
    ("decisionComment", "审批意见", "string", False, True),
]
ACT_COLS = [("action", "string"), ("actorUser", "string"),
            ("comment", "string"), ("actedAt", "string")]


def trajectory_drawer_button():
    """行按钮"查看详情与轨迹"：抽屉内上半部分为申请详情（只读），下半部分为
    该申请的审批轨迹关联表格（操作人/操作/时间/意见）。"""
    detail = create_form_block("confirmation_requests", REQ_DETAIL_FIELDS, [],
                               edit_record=True, read_pretty=True)
    trajectory = table_block(
        "approval_actions", ACT_COLS,
        association="confirmation_requests.approvalActions")
    return drawer_link_button("查看详情与轨迹", "申请详情与审批轨迹",
                              [detail, trajectory], refresh=False)


def edit_draft_drawer_button(draft_uid):
    """行按钮"编辑草稿"：编辑本人 DRAFT，调 saveDraft 且带 requestId（不新建记录）。
    draft_uid: 我的草稿表区块 uid（表单内按钮须显式 blocksToRefresh 刷新该表）。"""
    save = custom_request_btn(
        "保存修改", f"{API}saveDraft",
        x_action="customize:form:request",
        component="Action", primary=True, html_submit=True,
        data='{"requestId": "{{currentRecord.id}}"}',
        success="草稿已更新",
        refresh_blocks=[draft_uid],
    )
    return drawer_link_button("编辑草稿", "编辑转正草稿", [
        create_form_block("confirmation_requests", REQ_CONTENT_FIELDS, [save],
                          edit_record=True)])


def my_confirmation_blocks():
    """我的转正页（N2）："我的草稿"表（DRAFT 行：编辑草稿/提交）+ "历史申请"表
    （非 DRAFT 只读 + 查看详情与轨迹），按状态拆表保证"状态不合法的按钮不出现"。
    新建/重新申请表单放草稿表工具栏抽屉；抽屉表单内的保存按钮经 blocksToRefresh
    显式刷新草稿表（hook 的 __parent 是表单自身，不会刷新背后的表格——
    2026-11-11 浏览器实测定位）；提交按钮为表格行按钮，hook 刷新草稿表，
    另经 blocksToRefresh 同步刷新兄弟的"历史申请"表。"""
    history = table_block(
        "confirmation_requests",
        [("status", "string"), ("selfReview", "string"),
         ("submittedAt", "string"), ("decidedAt", "string"),
         ("decisionComment", "string")],
        row_buttons=[trajectory_drawer_button()],
        static_filter={"status": {"$ne": "DRAFT"}})
    draft_uid = uid()
    draft = table_block(
        "confirmation_requests",
        [("status", "string"), ("selfReview", "string"), ("trialHarvest", "string")],
        row_buttons=[
            edit_draft_drawer_button(draft_uid),
            custom_request_btn("提交", f"{API}submit",
                               data='{"requestId": "{{currentRecord.id}}"}',
                               success="已提交，等待审批",
                               refresh_blocks=[history["x-uid"]]),
        ],
        toolbar_buttons={"new": create_form_drawer_button(
            "新建转正申请", "发起转正申请", "confirmation_requests",
            [("selfReview", "自我评价", "string", False, True),
             ("trialHarvest", "试用期收获", "string", False, True)],
            custom_request_btn("保存草稿", f"{API}saveDraft",
                               x_action="customize:form:request",
                               component="Action", primary=True,
                               html_submit=True,
                               success="草稿已保存",
                               refresh_blocks=[draft_uid]))},
        static_filter={"status": {"$eq": "DRAFT"}},
        blk_uid=draft_uid)
    return [draft, history]


def pending_approvals_blocks():
    """待我审批页（N2）：仅 PENDING 待办 + 通过/驳回/查看详情与轨迹。
    pending 表 uid 先生成，供驳回抽屉内确认按钮的 blocksToRefresh 引用。"""
    pending_uid = uid()
    return [table_block(
        "confirmation_requests",
        [("employee", "string"), ("status", "string"),
         ("selfReview", "string"), ("trialHarvest", "string"),
         ("submittedAt", "string")],
        row_buttons=[
            custom_request_btn("通过", f"{API}approve",
                               data='{"requestId": "{{currentRecord.id}}"}',
                               success="已通过"),
            reject_popup_button(pending_uid),
            trajectory_drawer_button(),
        ],
        static_filter={"status": {"$eq": "PENDING"}},
        blk_uid=pending_uid)]


ACTIVE_FILTER = {"status": {"$in": ["PROBATION", "REGULAR"]}}


def hr_overview_blocks():
    """人事概览页（N3）：在职/试用/待审批/离职 四张计数卡 + 部门在职人数表 +
    员工明细表。计数全部由服务端 charts:queryData 在角色数据范围内聚合，
    口径与 docs/design/REPORT-DEFINITIONS.md 一致（在职=PROBATION+REGULAR；
    待审批=confirmation_requests PENDING，与"待转正人数"明确区分）。"""
    cards = chart_card_block([
        chart_renderer("employees", count_query("employeeNo", ACTIVE_FILTER),
                       "antd.statistic", "在职人数", field="count"),
        chart_renderer("employees", count_query("employeeNo",
                                                {"status": {"$eq": "PROBATION"}}),
                       "antd.statistic", "试用人数", field="count"),
        chart_renderer("confirmation_requests", count_query(
            "status", {"status": {"$eq": "PENDING"}}),
            "antd.statistic", "待审批数", field="count"),
        chart_renderer("employees", count_query("employeeNo",
                                                {"status": {"$eq": "LEFT"}}),
                       "antd.statistic", "离职人数", field="count"),
    ], widths=[25, 25, 25, 25])
    dept = chart_card_block([
        chart_renderer("employees", {
            "measures": [{"field": ["employeeNo"], "aggregation": "count",
                          "alias": "在职人数"}],
            "dimensions": [{"field": ["department", "name"], "alias": "部门"}],
            "filter": ACTIVE_FILTER,
            "orders": [],
        }, "antd.table", "部门在职人数"),
    ])
    detail = table_block("employees", [("employeeNo", "string"), ("name", "string"),
                                       ("department", "string"), ("jobTitle", "string"),
                                       ("status", "string")])
    return [cards, dept, detail]


PAGES = [
    ("员工管理", ["hr"], [
        table_block("employees", EMP_COLS,
                    toolbar_buttons={"flt": filter_action(),
                                     "add": native_create_drawer(
                                         "employees", HR_EMP_FORM, "新增员工")},
                    row_buttons=[native_edit_drawer("employees", HR_EMP_FORM, "编辑员工")]),
    ]),
    ("部门管理", ["hr"], [
        table_block("departments", [("name", "string"), ("code", "string")],
                    toolbar_buttons={"flt": filter_action(),
                                     "add": native_create_drawer(
                                         "departments", HR_DEPT_FORM, "新增部门")},
                    row_buttons=[native_edit_drawer("departments", HR_DEPT_FORM, "编辑部门")]),
    ]),
    ("我的档案", ["employee", "manager"],
     [table_block("employees", [c for c in EMP_COLS if c[0] not in ("hireDate", "probationEndDate")])]),
    ("我的转正", ["employee"], my_confirmation_blocks()),
    ("待我审批", ["manager"], pending_approvals_blocks()),
    ("人事概览", ["hr"], hr_overview_blocks()),
]


def build_page_schema(blocks):
    """Page(s) -> {u: Grid(d)} -> Row/Col -> 区块（官方 N4 + VR wrap）。"""
    s, d, u = uid(), uid(), uid()
    rows = {}
    for i, b in enumerate(blocks):
        rows[f"row{i}"] = {
            "type": "void",
            "x-component": "Grid.Row",
            "properties": {
                f"col{i}": {
                    "type": "void",
                    "x-component": "Grid.Col",
                    "properties": {f"block{i}": b},
                }
            },
        }
    schema = {
        "type": "void",
        "x-component": "Page",
        "x-uid": s,
        "properties": {
            u: {
                "type": "void",
                "x-component": "Grid",
                "x-uid": d,
                "x-async": True,
                "x-initializer": "page:addBlock",
                "properties": rows,
            }
        },
    }
    return schema, s, d, u


def main():
    env = load_env()
    admin = NbClient(email=env["INIT_ROOT_EMAIL"], password=env["INIT_ROOT_PASSWORD"])
    admin.signin()
    log(f"已登录: {admin.user.get('nickname')} @ {admin.base}")

    # --rebuild <标题>...：无视 builtBy 标记强制重建指定页（幂等marker仍会写入）
    argv = sys.argv[1:]
    force = set()
    if "--rebuild" in argv:
        force = set(argv[argv.index("--rebuild") + 1:]) - {"--rebuild"}

    routes = admin.call("GET", "/api/desktopRoutes:list",
                        params={"pageSize": 200, "appends": ["roles"]}).get("data", [])
    by_title = {}
    for r in routes:
        by_title.setdefault(r.get("title"), []).append(r)

    built, skipped = [], []
    for title, roles, blocks in PAGES:
        pages = [r for r in by_title.get(title, []) if r.get("type") == "page"]
        tabs = [r for r in routes if r.get("type") == "tabs"
                and r.get("parentId") in {p.get("id") for p in pages}]
        page = pages[0] if pages else None
        tab = tabs[0] if tabs else None
        if not page:
            log(f"跳过（缺少 page 路由，请先运行 build_ui.py）: {title}")
            continue
        # tabs 子路由的角色必须与所属 page 完全一致：desktopRoutes:create
        # 默认挂 member/admin，角色不符时 listAccessible 把它过滤成
        # children=[]，客户端 children[0].schemaUid 直接崩溃、整页渲染失败
        # （N4 空库初始化实测 2.2.37）。
        page_roles = [x.get("name") for x in (page.get("roles") or [])]
        role_refs = [{"name": rn} for rn in page_roles]
        if not tab:
            # 空库初始化时没有历史脚本留下的 tabs 子路由，按官方形态补建
            # （title=null 不与 build_ui 的按标题判重冲突；schemaUid 稍后统一 update）
            body = {"type": "tabs", "parentId": page["id"],
                    "hidden": True, "sort": 1}
            if role_refs:
                body["roles"] = role_refs
            r = admin.call("POST", "/api/desktopRoutes:create", json_body=body)
            tab = (r or {}).get("data")
            if not tab:
                log(f"跳过（tabs 子路由创建失败）: {title} -> "
                    f"{json.dumps(r, ensure_ascii=False)[:200]}")
                continue
            log(f"已创建 tabs 子路由: {title} tab#{tab.get('id')} roles={page_roles}")
        elif set(x.get("name") for x in (tab.get("roles") or [])) != set(page_roles) and page_roles:
            # 自愈：既有 tabs 子路由角色与 page 不一致（如早期脚本默认挂
            # member/admin）时纠正为 page 同款；置于 builtBy 跳过检查之前，
            # 已建页面无需重建即可修复菜单。
            admin.update("desktopRoutes", tab["id"], {"roles": role_refs})
            log(f"tabs 子路由角色纠正: {title} -> {page_roles}")
        if (page.get("options") or {}).get("builtBy") == MARKER and title not in force:
            skipped.append(title)
            continue

        old_root = page.get("schemaUid")
        schema, s, d, u = build_page_schema(blocks)
        r = admin.call("POST", "/api/uiSchemas:insertNewSchema", json_body=schema)
        ok_insert = isinstance(r, dict) and (r.get("data") or {}).get("x-uid") == s or (
            isinstance(r, dict) and r.get("data") == s)
        if not ok_insert:
            log(f"schema 插入失败: {title} -> {json.dumps(r, ensure_ascii=False)[:300]}")
            continue

        admin.update("desktopRoutes", page["id"], {
            "schemaUid": s, "menuSchemaUid": s, "enableTabs": False,
            "options": {"builtBy": MARKER},
        })
        admin.update("desktopRoutes", tab["id"], {
            "schemaUid": d, "tabSchemaName": u, "hidden": True,
        })
        # 清理旧页面 schema 树（孤儿）
        if old_root and old_root != s:
            admin.call("POST", "/api/uiSchemas:remove", params={"resourceIndex": old_root})
        log(f"已重建: {title} page#{page['id']} s={s} d={d} u={u} roles={roles}")
        built.append(title)

    log(f"== 完成: 重建 {len(built)} 页 {built}；已标记跳过 {len(skipped)} 页 {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
