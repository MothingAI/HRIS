# 已知限制

更新：2026-11-18。

本文记录当前业务范围、运行边界和平台适配细节。

## 1. 当前交付与限制

- 员工/主管/HR页面、统计UI、动态字段和完整审批闭环均已交付并验证。
- 全栈新卷空库初始化已执行，seed已串联模型、权限、插件重启、页面与演示数据；当前没有以旧版本未完成状态作为缺口。
- 演示工具以root专用prepDemoRegistry登记实际创建的员工、账号、申请ID。该保护依赖平台ACL；管理员授权变更应遵循同一信任边界。
- 演示身份顺延编号范围为DEMO-01..99，重复操作保留终态历史，不自动清空演示库。

## 2. 业务范围边界（设计既定，非缺陷）

- **单机单实例**：无高可用、无多租户；平台仅绑定 `127.0.0.1`，不发布公网。
- **单级直属主管审批**：只支持“提交 → 一名直属主管通过/驳回”，不支持多级会签、
  会签、转交、抄送。
- **无离职 / 调岗动作**：`LEFT` 状态由种子只读演示提供，第一版不实现离职流转；
  调岗期间的待办转交不在范围。
- **无组织调动历史 / 通用审计**：审批轨迹仅覆盖转正闭环，非通用审计平台。
- **不实现**：薪酬税务、社保、招聘、考勤、文件上传、复杂公式、短信邮件、外部 HR
  系统集成、Excel 导入（可选，未做）。
- **固定主管快照**：`approver` 在提交瞬间解析并保存，PENDING 期间冻结该员工的汇报关系
  变更（服务端数据库触发器 `hris_employee_relation_guard` 强制，覆盖所有写路径，见
  [model.md](../../config/model.md)）。禁止自己管理自己同样由该触发器在数据库层强制。

## 3. 平台 quirks（NocoBase 2.2.37 原生行为）

- **`fields:create` 的 null 字段缺陷**：新增字段到已有集合时，若该集合角色动作的
  `fields=null`（“全部字段”），原生 `fields.afterCreate` 钩子执行 `[...null, 新字段]`
  会抛 `TypeError: fields is not iterable` 并回滚。处理为“临时归一化 → 建字段 →
  复位 null”（不能保留显式列表，见下条；[LOWCODE-DEMO.md](../demo/LOWCODE-DEMO.md) §3/§8）。
- **显式字段白名单剥 FK 列**（N4 空库实测）：注册字段列表（`collections:get` /
  `fields:list`）不含 FK 列（`departmentId`/`managerId`/`userId`），把它当“全部
  字段”写进角色动作 `fields` 后，list/get 响应的 FK 列全部变 `null`（hr 读员工
  列表部门分组全 `None`）。`view` 的设计语义保持 `null`（build_s2 canonical）；
  demo 脚本建字段期间临时归一化、建完复位，并带自愈与 FK 可读回归断言。
- **`fields:create` 表同步删非模型索引/约束**（二次实证）：字段创建/应用触发的
  表同步会删除任何非平台模型声明的索引，连表级 UNIQUE 约束也会被删——曾导致
  REV-003 防线（`employees."userId"` 唯一索引）在字段操作后丢失。
  终验修复（REV-003 根治）：插件在**平台模型上声明**该约束——`plugin-hris-
  confirmation/dist/server/unique-binding.js` 通过 sequelize `beforeSync` 钩子给
  `employees.userId` 属性打 `unique:true`，字段同步因此把它当作模型索引持续维护，
  不再删除（终验探针：连续建两个低代码字段 + fields:apply + destroy，索引全程
  在库，[rev03-unique-binding-probe.txt](../evidence/rev03-unique-binding-probe.txt)）。启动迁移与 demo 脚本
  重申仅作非应用路径（恢复/直连 SQL）的兜底。
- **`desktopRoutes:create` 默认挂 `member/admin` 角色**（N4 空库实测）：tabs 子路由
  若与所属 page 角色不一致，`listAccessible` 把它过滤成 `children=[]`，前端
  `children[0].schemaUid` 直接崩溃、**整页渲染失败**。处理：`build_pages_v2.py`
  补建 tabs 子路由时显式带上 page 同款角色，且对既有不一致的自愈纠正。
- **`customize:form:request` 按钮不走 formily 必填校验**：弹窗表单里的自定义请求按钮
  点击后直接发请求，表单的 required 校验不生效。“驳回必填意见”实际由服务端 400
  （`驳回必须填写审批意见`）+ 页面错误通知兜底强制——属有意的纵深防御，但演示时
  表单上不出现红字必填提示（见 [n2-05-reject-comment-required.png](../evidence/n2-05-reject-comment-required.png)）。
- **弹窗内表单按钮默认不刷新外层表格**：动作按钮的刷新钩子只覆盖所在区块，弹窗
  （drawer）内表单的保存/确认按钮必须显式配置 `x-action-settings.onSuccess.
  blocksToRefresh`（指向外层表格 uid）才会刷新列表；本仓库
  `build_pages_v2.py` 的 `custom_request_btn(refresh_blocks=...)` 已统一接线。
- **工作流能力边界**（口径修正，REV-013）：社区版自带**基础工作流引擎**（可配置
  通用流程），官方**审批流插件**属商业版。本作品的审批动作以自研最小插件实现
  （只做服务端校验与状态机），是有意取舍而非"社区版没有工作流"。
- **charts（数据可视化）插件权限语义**（N3 实测）：`charts:queryData` 服务端对
  builder 模式查询合并角色数据范围过滤 + 视图动作字段白名单裁剪（源码
  `apply-query-permission` + 实测员工=1/主管=7）——统计页安全不依赖前端隐藏菜单；
  但两点 quirks：① `count` 度量不能用系统字段 `id`/`version`（被白名单裁掉 → 403），
  必须用业务字段（本作品用 `employeeNo`/`status`）；② 角色动作 `fields:null` 在
  charts 权限路径被当作**空**白名单而非“全部”，需显式列表。
- **charts 图表 schema 的渲染约束**（逆向官方 bundle 得出，实测有效）：statistic
  读值字段在 `config.general.field`（放根级不生效，卡片显示 0）；antd.table 配置
  不能带 `general.title`（antd Table 的 title prop 必须是函数，字符串导致渲染崩溃
  "g is not a function"）。见 [build_pages_v2.py](../../scripts/build_pages_v2.py) 文档注释。

## 4. 演示口径

- 所有业务数据为**虚构**（保留域名 `example.com`、虚构姓名），无真实个人信息。
- 演示账号密码统一为本地 `.env` 的 `DEMO_PASSWORD`，明显非生产口令。
- 备份文件含演示账号密码哈希，`backups/` 已被忽略，不提交 Git。

## 5. 自研 vs 平台

| 部分 | 归属 |
|---|---|
| 集合建模、角色/数据权限、动态字段、原生登录、页面编排、PostgreSQL | NocoBase 平台能力（Apache-2.0 社区版） |
| 数据模型设计、权限矩阵、种子数据、统计口径 | 本人配置 |
| `hris-confirmation` 插件（submit/approve/reject 受控动作，事务 + 行锁 + 字段保护） | 本人自定义扩展（最小，复用平台会话/ACL/事务） |
| 验证脚本、备份恢复脚本、文档 | 本人交付物 |
