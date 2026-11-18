# 转正审批实现（S3）

日期：2026-11-10。实施人：Claude Code。适用范围：NocoBase 2.2.37-full + PostgreSQL 16。

本文记录受控转正审批闭环的**服务端实现**：为何需要自研插件、插件结构、加载机制、
四个受控动作的语义与不变量、以及如何与平台原生会话 / ACL / 事务集成。

## 1. 为什么不用原生审批工作流

NocoBase 社区版不免费提供审批级工作流（`plugin-workflow-approval` 为商业付费，
专业版起，见 [VERSIONS.md](../operations/VERSIONS.md)）。免费的基础工作流引擎不具备本设计要求的
事务原子性与数据库行锁语义。因此按设计 ADR-003 采用**最小 HRIS 服务端插件**：

- 只实现 `saveDraft` / `submit` / `approve` / `reject` 四个受控动作；
- 复用平台原生会话（`ctx.state.currentUser`）、ACL 与同一 PostgreSQL 事务；
- 不修改平台核心，不另建认证，不另建密码表。

## 2. 插件结构

```
packages/plugins/@nocobase/plugin-hris-confirmation/
├── package.json              # name=@nocobase/plugin-hris-confirmation, main=dist/server/index.js
├── server.js                 # module.exports = require('./dist/server/index.js')
├── README.md                 # 动作与保证说明
└── dist/server/
    ├── index.js              # 导出插件类
    ├── plugin.js             # 注册动作 + acl.allow('loggedIn')
    └── actions.js            # 四个受控动作实现
```

## 3. 加载机制（已实测）

1. Compose 把宿主机 `packages/plugins` 以只读方式挂载到容器
   `storage/plugins`（见 [compose.yaml](../../deploy/compose.yaml)），并通过
   `NODE_MODULES_PATH=/app/nocobase/node_modules`、
   `PLUGIN_STORAGE_PATH=/app/nocobase/storage/plugins`、
   `PLUGIN_PACKAGE_PREFIX=@nocobase/plugin-,...` 让插件管理器识别。
2. [build_s3.py](../../scripts/build_s3.py) 在 `applicationPlugins` 集合注册插件记录
   （`name=hris-confirmation`、`packageName=@nocobase/plugin-hris-confirmation`、
   `enabled/installed=true`），幂等。
3. 应用启动时 `syncPluginSymlinks()` 把 `node_modules/<packageName>` 符号链接到
   `storage/plugins/<packageName>`；`PluginManagerRepository.init()` 按注册表
   `require(packageName)` 并实例化插件类。
4. **插件动作需应用重启后生效**（注册仅写库，加载发生在启动阶段）。

## 4. 受控动作语义

挂载在 `confirmation_requests` 集合上：

| 动作 | 调用 | 前置条件 | 结果 |
|---|---|---|---|
| `saveDraft` | `POST /api/confirmation_requests:saveDraft` | 账号已绑定员工且员工 `PROBATION` | 新建 DRAFT（`employeeId` 由绑定确定）或编辑本人 DRAFT |
| `submit` | `POST /api/confirmation_requests:submit` | 本人 DRAFT、直属主管有可用账号、无其他 PENDING | PENDING；服务端解析 `approverId`/`submittedAt`，追加 SUBMIT |
| `approve` | `POST /api/confirmation_requests:approve` | 当前用户 == `approverId`、状态 PENDING、员工 `PROBATION`、非本人 | APPROVED + 员工 REGULAR + APPROVE 轨迹 |
| `reject` | `POST /api/confirmation_requests:reject` | 同上；审批意见必填 | REJECTED + REJECT 轨迹；员工仍 PROBATION |

## 5. 关键不变量（全部服务端强制，不接受客户端赋值）

### 5.1 受保护字段

`actions.js` 定义 `PROTECTED_FIELDS`：`id / employee / employeeId / approver /
approverId / status / version / submittedAt / decidedAt / decisionComment /
createdAt / updatedAt / createdBy / createdById / updatedBy / updatedById`。

`saveDraft` 只透传请求体中**除受保护字段之外**的字段（这同时实现了“管理员新增
选填字段后表单立即可用”——新字段不属于受保护集合，会被 `saveDraft` 原样保存，见
[LOWCODE-DEMO.md](../demo/LOWCODE-DEMO.md)）。`submit`/`approve`/`reject` 完全不读取客户端对
`employeeId`/`approverId`/`status`/`version` 的赋值。

### 5.2 事务 + 行锁（并发与一致性）

- **提交**：`db.sequelize.transaction` 内，先 `SELECT ... FOR UPDATE` 行锁员工
  （串行化同一员工的并发提交），再行锁申请，再计数 PENDING（因员工行已锁，计数
  可靠）→ 解析主管 → 更新申请 + 追加 SUBMIT，任一步失败整体回滚。
- **通过 / 驳回**：事务内先 `FOR UPDATE` 行锁申请（串行化同一申请的并发决定），
  校验 PENDING + `approverId == 本人`，再行锁员工（校验非本人 + 仍 PROBATION），
  更新申请（通过时同步员工 `REGULAR`）+ 追加轨迹，任一步失败整体回滚。
- **version**：`(version||0)+1` 随每次状态迁移递增，提供可观测的并发控制痕迹。

### 5.3 授权（动作内部显式校验，不依赖“仅已登录”）

插件 `load()` 中 `this.app.acl.allow('confirmation_requests', [四个动作], 'loggedIn')`
仅负责放行“已登录用户到达动作处理器”，**真正的授权在动作内部完成**：
`resolveEmployee(db, user.id)`（账号↔员工绑定）、`request.employeeId === employee.id`
（只能操作本人申请）、`request.approverId === user.id`（只能被指定审批人处理）、
`employee.userId !== user.id`（禁止自批）。

### 5.4 默认 CRUD 不绕过受控动作

[build_s3.py](../../scripts/build_s3.py) 的 `remove_employee_create_on_requests()` 删除员工角色在
`confirmation_requests` 上的默认 `create` 动作，使员工不能通过原生 `:create` 伪造
`status`/`approverId`/`employeeId`。`build_s2.py` 也不再授予该 `create`（写操作全部
走受控动作）。HR/主管对 `confirmation_requests` 只有 `view`，无 `update`/`create`。

## 6. 与平台 ACL 重载的兼容性（实测结论）

`build_s3.py` 每次运行都会触发 ACL 重载（更新 resource 以 `writeToACL`）。实测确认：
ACL 重载**不会**清除插件在 `load()` 阶段注册的 `acl.allow('loggedIn')` 运行时规则，
重载后四个受控动作仍可正常调用（[verify_business.py](../../scripts/verify_business.py) 在 `seed.ps1` 之后重跑
仍 24/24 通过）。

## 7. 验证

完整集成验证（正向 / 驳回再申请 / 重复提交 / 越权 / 伪造 / 并发 / 事务回滚）见
[VERIFICATION.md](../quality/VERIFICATION.md)，命令与断言见 [verify_business.py](../../scripts/verify_business.py)（25 条）与
[verify_permissions.py](../../scripts/verify_permissions.py)（9 条）。
