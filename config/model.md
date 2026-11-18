# 数据模型（S2）

本文件记录 HRIS 的数据模型：集合、字段、类型、关系、约束与枚举。全部通过 NocoBase
官方 API 建立（`POST /api/collections:create`，字段随集合嵌套创建），**不直接修改平台
系统表**。可重建脚本见 [build_s2.py](../scripts/build_s2.py)（幂等）。

## 集合总览

| 集合 | 中文 | 用途 |
|---|---|---|
| `departments` | 部门 | 平铺 3 个部门 |
| `employees` | 员工 | 员工档案 + 直属主管 + 账号绑定 |
| `confirmation_requests` | 转正申请 | 转正草稿/待审/结果 |
| `approval_actions` | 审批轨迹 | 只追加的审批动作记录 |

平台原生 `users`（账号）与 `roles`（角色）复用，不自建密码表。

## 字段定义

### departments（部门）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `name` | string | 必填 | 部门名称 |
| `code` | string | 唯一、必填 | 部门编码（TECH / PROD / MKT） |

### employees（员工）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `employeeNo` | string | 唯一、必填 | 工号（E001…） |
| `name` | string | 必填 | 姓名 |
| `department` | belongsTo `departments` | — | 所属部门（FK `departmentId`） |
| `jobTitle` | string | — | 岗位 |
| `manager` | belongsTo `employees` | 自关联 | 直属主管（FK `managerId`），禁止自己管理自己 |
| `user` | belongsTo `users` | 唯一（非空值） | 账号绑定（FK `userId`）；唯一约束声明在平台模型上（`unique-binding.js`，beforeSync 注入，字段同步持续维护），DB 层唯一索引名可为 `employees_userId_uk`（平台建）或 `employees_user_id_key`（迁移兜底建），按列匹配；允许多个未绑定（NULL）员工 |
| `hireDate` | dateOnly | — | 入职日期 |
| `probationEndDate` | dateOnly | — | 试用期结束日期 |
| `status` | string | 默认 `PROBATION` | 任职状态，见下方枚举 |

### confirmation_requests（转正申请）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `employee` | belongsTo `employees` | — | 申请人（FK `employeeId`），由服务端确定 |
| `approver` | belongsTo `users` | — | 待审批人＝主管账号（FK `approverId`），由服务端确定 |
| `status` | string | 默认 `DRAFT` | 申请状态，见下方枚举 |
| `selfReview` | text | — | 试用期收获 / 自我评价 |
| `submittedAt` | date | — | 提交时间 |
| `decidedAt` | date | — | 决定时间 |
| `decisionComment` | text | — | 审批意见 |
| `version` | integer | 默认 `0` | 并发控制版本号 |

### approval_actions（审批轨迹）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `request` | belongsTo `confirmation_requests` | — | 所属申请（FK `requestId`） |
| `actorUser` | belongsTo `users` | — | 操作账号（FK `actorUserId`） |
| `action` | string | — | 动作（SUBMIT / APPROVE / REJECT） |
| `comment` | text | — | 备注 |

## 枚举值

| 字段 | 允许值 | 含义 |
|---|---|---|
| `employees.status` | `PROBATION` / `REGULAR` / `LEFT` | 试用中 / 已转正 / 已离职 |
| `confirmation_requests.status` | `DRAFT` / `PENDING` / `APPROVED` / `REJECTED` | 草稿 / 待审批 / 已通过 / 已驳回 |
| `approval_actions.action` | `SUBMIT` / `APPROVE` / `REJECT` | 提交 / 通过 / 驳回 |

> 说明：`status` / `action` 以 string 存储（`interface: select` 提供下拉），枚举的合法值
> 由 S3 的服务端受控动作强制校验，不依赖数据库 ENUM 类型。普通集合 CRUD 无法绕过
> 受控动作写入状态（见 [permissions.md](permissions.md)）。

## 数据库层约束（REV-003 终验修复 / REV-004）

账号唯一绑定与关系守卫由自研插件在加载时建立（`packages/plugins/@nocobase/plugin-hris-confirmation/dist/server/`）：

1. **`employees.userId` 唯一约束（主防线：平台模型声明）**——`unique-binding.js`
   在集合定义/插件加载时挂 sequelize `beforeSync` 钩子，给模型属性打
   `unique:true`。平台字段同步（`fields:create/apply/destroy` 触发的表同步）因此
   把该唯一索引当作模型索引**持续维护**，不再删除（终验 REV-003：连续低代码加
   两个字段后索引全程在库）。对非空 `userId` 唯一；PostgreSQL 唯一索引天然允许
   多个 `NULL`，因此“多个未绑定账号的员工”仍合法。
2. **启动迁移兜底**（`migrations.js`，幂等）：面向非应用路径（备份恢复、直连
   SQL）重申唯一索引（按列匹配，名可为 `employees_userId_uk` 或
   `employees_user_id_key`）；迁移前先检测重复绑定，发现重复则跳过建索引并记录
   日志，**绝不删除数据**。
3. **员工关系守卫触发器**（`hris_employee_relation_guard`，`BEFORE INSERT OR UPDATE ON employees`）：
   - 禁止自己管理自己（`managerId = id`）；
   - 存在 `PENDING` 转正申请时，冻结该员工的 `managerId` / `userId` 变更（REV-004）；
   - 拒绝把已绑定账号绑给第二名员工（终验 REV-003 增补：`userId` 变更且已有其他
     员工持有该账号时报错回滚——在唯一索引之外给所有 DML 写路径一道可读的防线）。

触发器覆盖**所有**写路径（CRUD、关系接口、批量更新），不依赖前端隐藏字段。业务写入重复绑定 /
自管理 / 冻结变更时，返回可解释的 4xx/500 错误并整体回滚。

## 种子数据口径

- 部门：技术部（TECH）、产品部（PROD）、市场部（MKT）。
- 员工 20 人：试用 `PROBATION` 8 人、在职 `REGULAR` 10 人、离职 `LEFT` 2 人；两名
  主管（王建国＝技术部经理、林芳＝产品部经理）包含在 20 人内。
- 主管与直属下属为**一层**关系：技术部 E002–E008 直属王建国；产品部 E010–E015 直属
  林芳；市场部 E017–E020 直属罗成（市场经理，无演示账号，属已识别的边界）。
- 员工编号 `employeeNo`、部门 `code`、账号绑定 `user` 唯一；`manager` 禁止指向自己。

## 重建方式

```bash
python scripts/build_s2.py
```

脚本幂等：已存在的集合/角色/账号/员工会跳过；重复执行不产生重复工号、账号或部门。
