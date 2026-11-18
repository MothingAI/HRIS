# 角色与数据权限（S2）

本文件记录 HRIS 的角色、账号绑定、数据范围（行级授权）与字段级权限。全部通过
NocoBase 官方 API 配置，并由后端 ACL 在数据库查询层强制执行（非前端隐藏）。

可重建脚本见 [build_s2.py](../scripts/build_s2.py) 的 `build_acl()`；演示账号见 [DEMO-ACCOUNTS.md](../docs/demo/DEMO-ACCOUNTS.md)。

## 角色

| 角色 | 名称 | 说明 |
|---|---|---|
| `root` | 平台管理员 | NocoBase 内置，配置与排障，不参与业务演示 |
| `admin` | 管理员 | NocoBase 内置，本作品不使用 |
| `member` | 成员 | NocoBase 内置默认角色，本作品不赋予业务账号 |
| `hr` | HR | 全公司只读 + 维护基本档案（不可改状态） |
| `manager` | 主管 | 本人 + 直属下属只读，审批本人被指定的申请 |
| `employee` | 员工 | 本人只读，提交/查看本人转正申请 |

业务账号只绑定单一业务角色（`hr` / `manager` / `employee`），不叠加 `member`。

## 数据范围（行级授权）

数据范围通过 `dataSourcesRolesResourcesScopes`（命名过滤器）+ 动作的 `scopeId` 关联实现。
过滤器模板变量为 `{{ ctx.state.currentUser.id }}`（当前登录用户 id）。

| 范围 key | 过滤器 | 用于 |
|---|---|---|
| `emp_self` | `{"userId": "{{ ctx.state.currentUser.id }}"}` | 员工看本人档案 |
| `emp_manager` | `{"$or": [{"userId": "…"}, {"manager.userId": "…"}]}` | 主管看本人 + 直属下属 |
| `req_self` | `{"employee.userId": "{{ ctx.state.currentUser.id }}"}` | 员工看本人申请 |
| `req_manager` | `{"$or": [{"employee.userId": "…"}, {"approverId": "…"}]}` | 主管看本人 + 被指定审批的申请 |
| `act_self` | `{"request.employee.userId": "{{ ctx.state.currentUser.id }}"}` | 员工看本人申请轨迹 |
| `act_manager` | `{"$or": [{"request.employee.userId": "…"}, {"request.approverId": "…"}]}` | 主管看相关申请轨迹 |

`$or` 支持模板变量（已实测）；`manager.userId` 为两层关系过滤（`employees.manager` →
`employees.user`），已验证在查询层生效。

## 动作矩阵（按集合）

`view` 默认 `fields: null`＝全部字段；`create/update` 用字段白名单限制可写字段。

### 员工 `employee`

| 集合 | 动作 | 范围 | 字段 |
|---|---|---|---|
| departments | view | 全部 | 全部 |
| employees | view | `emp_self` | 全部（只读） |
| confirmation_requests | view / create | `req_self` / — | 全部 |
| approval_actions | view | `act_self` | 全部 |

员工无 `update/destroy`，无法改他人或改状态；状态受 S3 受控动作保护。

### 主管 `manager`

| 集合 | 动作 | 范围 | 字段 |
|---|---|---|---|
| departments | view | 全部 | 全部 |
| employees | view | `emp_manager` | 全部（只读） |
| confirmation_requests | view | `req_manager` | 全部 |
| approval_actions | view | `act_manager` | 全部 |

主管无直接 `update` 申请的动作；通过/驳回走 S3 服务端受控动作（校验 `approverId`＝本人、
状态＝PENDING、且非本人申请）。

### HR `hr`

| 集合 | 动作 | 范围 | 字段 |
|---|---|---|---|
| departments | view / create / update | 全部 | 全部 |
| employees | view | 全部 | 全部 |
| employees | create / update | — | 白名单（见下），**不含 `status`** |
| confirmation_requests | view | 全部 | 全部（只读） |
| approval_actions | view | 全部 | 全部（只读） |

HR 员工 `create/update` 字段白名单：`employeeNo, name, department, jobTitle, manager,
user, hireDate, probationEndDate`。`status` 不在白名单内，因此 HR 不能直接把员工改为
`REGULAR`（实测：HR 提交 `{"status":"REGULAR"}` 后状态仍为 `PROBATION`）。

## 受控动作的角色/动作强制（REV-002）

四个受控动作（`saveDraft`/`submit`/`approve`/`reject`）只向 `loggedIn` 放行到动作处理器，
**角色、账号绑定、数据范围与状态校验在动作内部从权威数据源完成**，不信任客户端指定角色：

- 申请角色：`saveDraft` / `submit` 要求当前账号具备 `employee` 或 `manager` 角色（`users` + `appends:['roles']` 查询）。
- 审批角色：`approve` / `reject` 要求当前账号具备 `manager` 角色，且必须是申请的 `approver`，且申请为 `PENDING`。
- 提交前校验：`submit` 解析直属主管账号后，再确认该主管账号**当前仍具备 `manager` 角色**，
  否则返回 400“直属主管账号当前无审批资格”。角色被撤销后重新登录，审批返回 403。
- HR 不具备审批角色，即使被指定为 approver 也返回 403；伪造 `role` 请求体无法升级。

角色矩阵（REV-002 修复后）：

| 动作 | employee | manager | hr |
|---|---|---|---|
| saveDraft / submit | ✅ 本人 | ✅ 本人 | ❌ |
| approve / reject | ❌ | ✅ 仅被指定 approver | ❌ |

## 菜单权限（REV-001）

菜单与页面通过受支持配置 API 建立（[build_ui.py](../scripts/build_ui.py)，幂等）：页面 schema 经
`uiSchemas:insertNewSchema` 落库，菜单经 `desktopRoutes:create` 建项并把 `menuSchemaUid`
指向页面根 schema；菜单可见性通过 `desktopRoutes.roles` 关联控制，客户端用
`desktopRoutes:listAccessible` 渲染（已实测各角色可见菜单）：

| 菜单 | 可见角色 |
|---|---|
| 员工管理 / 部门管理 / 人事概览 | `hr` |
| 我的档案 | `employee` + `manager` |
| 我的转正 | `employee` |
| 待我审批 | `manager` |

> 页面 schema 的**可视化渲染**与表单→受控动作按钮接线仍需浏览器验收（本环境无浏览器，
> 见 `BLACKBOARD.md` REV-001 BLOCKED）。菜单与角色菜单权限已通过 API 结构级验证。

## 已验证结论（真实登录会话，2026-11-10）

| 断言 | 结果 |
|---|---|
| 员工 A 只能 `view` 本人档案，直接 `:get` 他人返回 `null` | 通过 |
| 员工 B 只能看本人，看不到员工 A | 通过 |
| 主管 A 只看到本人 + 直属下属（8 人），看不到主管 B 及其下属 | 通过 |
| 主管 B 只看到本人 + 直属下属（7 人） | 通过 |
| 主管 A 看不到主管 B 的待审批申请 | 通过 |
| HR 看到全部 20 名员工与全部申请 | 通过 |
| HR 提交 `status=REGULAR` 无法改状态 | 通过 |
| 员工无 `update` 动作，无法改任何档案 | 通过 |

命令与断言复现脚本：S3 将交付 [verify-permissions.ps1](../scripts/verify-permissions.ps1)。

## 关键实现要点（NocoBase 2.x ACL，易踩坑）

1. **现代数据权限表**：NocoBase 2.x 运行时读取 `dataSourcesRolesResources*` 系列表
   （含 `…Scopes` / `…Actions`），**不是**旧版 `rolesResources*` 表。旧接口
   `/api/roles/<name>/resources:create` 写入旧表，**不生效**。

2. **正确 API**：
   - `POST /api/dataSourcesRolesResourcesScopes:create` 建命名过滤器；
   - `POST /api/dataSourcesRolesResources:create` 授权集合（`dataSourceKey:"main"`）；
   - `POST /api/dataSourcesRolesResourcesActions:create/update` 配置动作与 `scopeId`。

3. **ACL 重载触发**：动作 `create` 只触发 `afterCreate`，而重载监听的是
   `afterUpdateWithAssociations`；因此新建动作后必须**再更新一次 resource**（或动作）
   触发 `writeToACL`，否则内存 ACL 不刷新（首次实测为空列表的原因）。

4. **`fields` 语义**：`null`＝全部字段；`[]`＝无任何字段；`[列表]`＝白名单。集合默认值
   是 `[]`，所以“全部字段”必须**显式写 `null`**，否则 view 只返回 `id`。
