# 低代码配置演示（S3 + N3.4）

日期：2026-11-10 建，2026-11-12 更新（N3.4 员工字段浏览器实测）。实施人：Claude Code。

本文记录“管理员不改任何前端 / 插件源码即可为业务表单增加选填字段”的**实际证据**与
**平台 quirks**，并给出在 UI 中操作的等价步骤。两个演示：
S3 给转正申请加字段（API 级闭环）；N3.4 给**员工表单**加字段并**在浏览器实测**
（HR 填写保存、重开回显、旧记录兼容、员工侧新列可见）。

## 1. 演示目标

向 `confirmation_requests`（转正申请）集合新增一个选填文本字段 `trialHarvest`
（试用期收获），并证明：

1. 通过集合字段配置即可加字段（等价于 UI 的“字段管理”，不写源码）；
2. 新字段立即能被 `saveDraft` 受控动作接受并保存；
3. 字段可选（nullable），历史记录仍可读取（该字段为 `null`）。

## 2. 实际证据（已执行）

[demo_lowcode_field.py](../../scripts/demo_lowcode_field.py) 运行结果：**通过 4 / 4**。

- 新增前集合字段：`selfReview / decidedAt / submittedAt / status / decisionComment /
  employee / version / approver`（8 个业务字段）。
- 调用 `POST /api/fields:create`（`collectionName=confirmation_requests`,
  `name=trialHarvest`, `type=text`, `interface=textarea`）新增字段。
- 新增后集合字段包含 `trialHarvest`。
- 员工 `POST /api/confirmation_requests:saveDraft` 提交
  `{"selfReview":"...", "trialHarvest":"学会了需求拆解与跨部门协作"}`，
  返回并持久化了 `trialHarvest`（后端 `:get` 可读回）。

关键点：`saveDraft` 只剥离 `PROTECTED_FIELDS`（见 [APPROVAL-IMPLEMENTATION.md](../design/APPROVAL-IMPLEMENTATION.md)
第 5.1 节），其余字段一律透传。因此**新增业务字段无需改动插件任何代码**即可被表单使用。

## 3. 平台 quirks 与处理（实测）

NocoBase 2.2.37 的 `fields:create` 会触发 `fields.afterCreate` ACL 钩子，钩子对每个
角色动作执行 `[...fields, 新字段]`。当某角色动作的 `fields` 为 `null`（本作品 S2
用 `null` 表示“全部字段”）时，`[...null]` 抛 `TypeError: fields is not iterable`，
导致字段创建**整体回滚**（HTTP 500）。

处理方式（`normalize_null_fields()`，S3 起用于两个 demo 脚本）：在新增字段前，把
`confirmation_requests` 上 `fields=null` 的角色动作**临时**改为显式字段列表（当前
业务字段名数组），再调用 `fields:create`；此时钩子正确地把新字段追加到显式列表。

**N4 修正**：显式列表不能保留——注册字段列表不含 FK 列（`departmentId` 等，
`collections:get` / `fields:list` 均不返回），永久写成显式列表会把 FK 列从
list/get 响应中剥掉（hr/employee/manager 实测全表 `departmentId=null`）。正确做法
是建完字段**立即复位 null**（=全部字段，语义与设计一致）；另含自愈：检测到缺 FK
列的既有 `view` 白名单即复位。见 `demo_lowcode_employee_field.py` 的
`restore_null_fields()` / `heal_view_whitelist()` 与 §8。

> 该 quirks 属于 NocoBase 2.2.37 原生字段创建路径的行为，非本作品引入；已在脚本与
> 本文档记录，避免误导为平台默认即可无痛新增字段。

## 4. UI 等价操作

**S3 演示（转正申请字段，API 级闭环已实测）**：

1. 以平台管理员登录 `http://127.0.0.1:13000`。
2. 左侧「集合管理」→ 找到「转正申请」→「字段」→「新增字段」。
3. 字段名 `trialHarvest`、类型“长文本”、显示名“试用期收获”、必填关闭。
4. 保存后进入「转正申请」表单配置，将新字段拖入“新建 / 编辑”区块。
5. 以员工账号新建草稿即可看到并填写该字段；打开历史记录，新字段为空但记录可读。

**N3.4 演示（员工字段，浏览器全链路已实测，见第 6 节）**：同上路径，集合选
「员工」，新增字段 `emergencyContact`（紧急联系人，单行文本），再在「员工管理」
编辑抽屉与「我的档案」表格配置里把字段加入表单/列。

## 5. 可复现性（S3）

- 新实例先 `python scripts/build_s2.py`（建立基础模型），再
  `python scripts/demo_lowcode_field.py`（新增演示字段，幂等）。
- 字段 `trialHarvest` 为演示性“管理员加字段”示例；`selfReview`（自我评价/试用期收获）
  是基础模型自带字段，二者并存。

## 6. N3.4 员工字段浏览器实测（2026-11-12，全部通过）

目标：证明“管理员配置新增选填字段，**不改源码**即进入实际员工表单与员工侧表格，
可填写、保存、重新查看，旧记录正常打开”——而不只是 API 能写入一个已存在字段。

脚本：`python scripts/demo_lowcode_employee_field.py` → **10/10 通过**（幂等），步骤
全部走平台已验证路径（设计器等价 API，无源码改动）：

1. **归一化** `employees` 上 `fields=null` 的角色动作（第 3 节 quirks，S3 同款处理）；
2. `POST /api/fields:create` 新增 `emergencyContact`（紧急联系人，input/string）；
   fields:create 的 ACL 钩子**自动**把新字段追加进 hr create/update 的显式字段
   白名单（脚本断言 `hr update fields` 已含 `emergencyContact`）——即“加字段后
   HR 立即可写”，无需再改权限；
3. `POST /api/fields:apply` 补 uiSchema（title/输入组件，设计器同款路径）；
4. `POST /api/uiSchemas:insertAdjacent`（`position: beforeEnd`）把字段节点插入
   「员工管理」编辑抽屉表单（`Grid.Row > Grid.Col > CollectionField + FormItem`，
   官方字段形态）与「我的档案」表格列（官方列壳 `TableV2.Column.Decorator`）——
   等价于管理员在设计器里把字段拖进表单/表格。

浏览器实测（无头 Chrome + CDP，`node scripts/browser/probe-n3-lowcode.js`
——REV-012 起探针入受跟踪目录，截图直写 `docs/evidence/`；
**9/9 通过**；证据 `docs/evidence/n3-lowcode-1..6.png` + `n3-lowcode-console.txt`）：

- HR 打开「员工管理」→ 李思远（E002）行「编辑」：抽屉表单出现“紧急联系人”输入框；
- 填 `王芳 13800001234` → 原生“提交”（走平台 update 动作，字段白名单已含新字段）
  → 重新打开抽屉，值回显；
- 旧记录 E004（从未填写）编辑抽屉正常打开，值为空，不报错；
- 员工 A 登录「我的档案」：表格出现“紧急联系人”列，李思远行显示本人值；
- 管理员 token 复核 `E002.emergencyContact` 已持久化（非仅 UI）。

复现：新实例先跑 S2/S3 种子与页面构建（见 [REPRODUCE.md](../operations/REPRODUCE.md)），再依次
`python scripts/demo_lowcode_employee_field.py`（建字段+插表单）与浏览器实测探针。
演示数据准备（可重复的待审批申请，与本字段演示相互独立）：`python
scripts/prep_demo.py`（幂等，为 PROBATION 员工 E003 张敏建演示账号并以受控
saveDraft+submit 提交一条 PENDING 申请）。

## 7. N3.4 平台 quirks 补充（实测）

- **`uiSchemas:insertAdjacent` 是设计器等价的 schema 打点 API**：
  `POST /api/uiSchemas:insertAdjacent`，`params.resourceIndex` = 目标节点 uid、
  `params.position: "beforeEnd"`，`body.schema` = 要插入的节点树。用于把字段节点
  加进已有表单 Grid / 表格列容器，不需要整树重建。
- **表单/表格对“动态加字段”的真实边界**：字段建好 + uiSchema 齐备后，新字段
  **不会自动出现**在既有表单/表格区块里（区块字段是 schema 显式节点），这正是
  “设计器里拖字段”的本质——`insertAdjacent` 补的就是这一步。
- **FormV2 字段 DOM 是 Formily 渲染**：表单项类名为 `.ant-formily-item`（非
  `.ant-form-item`），自动化断言需用前者；且输入框的值在 `input.value`，不在
  `textContent`（对抽屉内容做文本断言时会漏）。
- **关闭抽屉会触发区块刷新**：上一个抽屉关闭动画期间，紧接着的行内按钮点击可能
  被吞（无请求），自动化需等待抽屉完全卸载或重试点击（人工操作无此问题）。

## 8. N4 空库复现新增 quirks（实测 2026-11-12）

- **显式字段白名单会剥 FK 列**：角色动作 `fields` 一旦是显式列表，list/get 响应
  只保留列表内字段；而注册字段列表（`collections:get` / `fields:list`）**不含**
  FK 列（`departmentId`/`managerId`/`userId`），把它们当“全部字段”写入白名单
  会让所有角色读不到 FK 列（`verify_reports` 部门分组全变 `None` 才暴露）。
  处理：归一化只在建字段期间临时生效，建完复位 null + 自愈 + FK 可读断言
  （`demo_lowcode_employee_field.py` 第 6 步）。
- **`fields:create` 触发的表同步会删非平台模型声明的索引/约束**：实测 2.2.37
  连表级 UNIQUE 约束都会被删（临时索引消失、约束消失，二次实证）；插件迁移建的
  `employees_user_id_key` 唯一索引在字段操作后必然丢失。处理：插件迁移每次启动
  幂等重建（`migrations.js`），demo 脚本建字段后立即用同 SQL 重申并断言
  （REV-003 的唯一性防线因此完整）。
