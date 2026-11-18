# 页面配置（S2.3 / S3.1 / S4.1，2026-11-12 N3 同步）

日期：2026-11-10 建，2026-11-12 更新。实施人：Claude Code。

> **状态（REV-001 + N2 + N3）：7 个业务页面已按官方形态交付并浏览器实测，N2 操作
> 体验（草稿编辑、按状态显隐按钮、审批轨迹、HR 筛选/新建/编辑）与 N3 人事概览
> 统计（charts 计数卡 + 部门表 + 明细）均已落地并实测。**
> 构建顺序（幂等）：`build_ui.py`（路由骨架 + 角色菜单）→ `fix_field_meta.py`
> （字段中文标题/uiSchema/枚举）→ `build_pages_v2.py`（页面 schema + 受控按钮接线，
> `--rebuild <页面标题>` 可强制重建单页）。
> 无头 Chrome 实测记录与截图见 `docs/evidence/` 与 `BLACKBOARD.md` 交接记录。

## 1. 页面清单与权限

| 页面 | 可见角色 | 说明 |
|---|---|---|
| 工作台 | 全部 | 按角色展示入口（员工看“我的档案/我的转正”，主管看“待我审批”，HR 看“人事概览”） |
| 部门管理 | HR | 部门列表（增删改） |
| 员工管理 | HR | 员工列表，姓名/工号、部门、任职状态筛选；维护基本档案（不可改 status） |
| 我的档案 | 员工 / 主管 | 员工看本人；主管看本人 + 直属下属 |
| 我的转正 | 员工 | 新建草稿、编辑、提交，展示申请状态与轨迹 |
| 待我审批 | 主管 | 仅本人被指定审批的 PENDING 申请，可查看申请与历史轨迹 |
| 人事概览 | HR | 在职 / 待转正 / 待审批 / 部门人数统计（口径见 [REPORT-DEFINITIONS.md](../docs/design/REPORT-DEFINITIONS.md)） |

数据授权仍由后端 ACL 强制执行：页面只做展示，不能替代行级 / 字段级权限。

## 2. 通用配置步骤（NocoBase UI）

1. 平台管理员登录 `http://127.0.0.1:13000`。
2. 左侧「页面」→「新增页面」→ 选「数据区块页面」/ 空白页。
3. 拖入「表格区块」/「详情区块」/「表单区块」，绑定对应集合
   （`departments` / `employees` / `confirmation_requests` / `approval_actions`）。
4. 「菜单」中把页面挂到对应菜单，并在「角色」里把菜单/页面权限赋予对应角色。

## 3. 关键页面配置要点

### 3.1 员工管理（HR）

- 表格列：工号、姓名、部门、岗位、直属主管、入职日期、试用期结束日期、任职状态。
- 筛选：姓名 / 工号、部门、任职状态（PROBATION / REGULAR / LEFT）。
- 编辑表单字段白名单与 HR 的 `update` 字段一致（**不含 status**）；状态列只读，
  由转正审批流转（受控动作）决定。

### 3.2 我的转正（员工）

- 新建 / 编辑表单：`selfReview`（自我评价）、`trialHarvest`（试用期收获，低代码新增
  选填字段，见 [LOWCODE-DEMO.md](../docs/demo/LOWCODE-DEMO.md)）。
- 双表布局：**草稿表**（status=DRAFT，行内“编辑草稿/提交”）+ **历史表**（非 DRAFT，
  行内仅“查看详情与轨迹”只读）。
- 草稿编辑：编辑抽屉带 `requestId` 走 `saveDraft` 更新同一记录（不新建）；新建/
  编辑/提交按钮均配置 `blocksToRefresh` 指向草稿表，保存后列表即时刷新。
- 已驳回可在历史表上方“重新申请”（旧申请保留）；驳回意见列在历史表可见。
- 冲突时显示“已处理 / 冲突”等中文反馈并刷新。

### 3.3 待我审批（主管）

- 列表：`approverId == 本人` 且 `status == PENDING` 的申请（行内 通过 / 驳回 /
  查看详情与轨迹）。
- 详情抽屉：申请内容 + 审批轨迹（关联表 `confirmation_requests.approvalActions`，
  列：操作/操作人/意见/时间）；驳回走弹窗表单填意见，服务端 400 兜底必填。

### 3.4 人事概览（HR，N3 交付）

- 数据可视化插件（charts）区块，服务端聚合（`charts:queryData` 按角色数据范围）：
  - 四张统计卡（antd.statistic）：在职人数 / 试用人数 / 待审批数 / 离职人数；
  - 部门在职人数表（antd.table）：按部门分组，只计在职；
  - 员工明细表（工号/姓名/部门/职位/任职状态）。
- 口径见 [REPORT-DEFINITIONS.md](../docs/design/REPORT-DEFINITIONS.md)；计数不依赖 20 条分页（服务端全量聚合，
  卡片值与 API 口径浏览器实测逐一相等）。
- 越权：员工/主管菜单无此页；其 token 直接调 `charts:queryData` 仍被数据范围
  过滤（实测员工=1、主管=7，见 [VERIFICATION.md](../docs/quality/VERIFICATION.md) §6.1）。
- 图表 schema 的平台 quirks（计数字段须用业务字段、statistic 读
  `config.general.field`、antd.table 不能带 general.title 等）见
  [LIMITATIONS.md](../docs/quality/LIMITATIONS.md) §3 与 [build_pages_v2.py](../scripts/build_pages_v2.py) 文档注释。

## 4. 空状态 / 无权限 / 失败反馈

- 空列表显示“暂无数据”；无权限页面返回平台的中文“无权限”提示；表单校验失败显示
  后端返回的可解释中文错误（如“未配置直属主管”“已存在待审批的转正申请”等）。

## 5. 已交付与未交付边界（2026-11-12）

- **已浏览器实测交付**：三角色页面渲染（员工/主管/HR）、表格中文列头与状态标签、
  保存草稿/提交/驳回动作流、数据范围隔离在页面层的体现（员工只见本人）。
- **N2 补齐并实测**：草稿编辑（同一记录）、按状态显隐动作按钮、申请详情+审批轨迹
  抽屉、驳回必填意见（服务端 400 兜底）、HR 筛选/新建/编辑入口；全链路
  “草稿→编辑→提交→驳回→看意见→重新申请→再提交→通过→转正”（证据
  `docs/evidence/n2-*.png|log`）。
- **N3 补齐并实测**：人事概览统计（计数卡+部门表，审批联动与越权双层）；
  低代码员工字段 `emergencyContact` 进编辑表单与员工侧表格（证据
  `docs/evidence/n3-*.png|txt`）。
- 后端结论以 [VERIFICATION.md](../docs/quality/VERIFICATION.md) 与各 verify 脚本为准；页面不替代后端 ACL。
