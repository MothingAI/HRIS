# @nocobase/plugin-hris-confirmation

HRIS 转正申请的最小服务端插件。复用 NocoBase 平台会话、数据权限与 PostgreSQL，
只补充免费原生能力无法安全提供的受控业务动作。

## 提供的动作（挂载到 `confirmation_requests` 集合）

| 动作 | 调用 | 语义 |
|---|---|---|
| `saveDraft` | `POST /api/confirmation_requests:saveDraft` | 新建本人草稿或编辑本人 DRAFT；`employeeId` 由账号绑定确定 |
| `submit` | `POST /api/confirmation_requests:submit` | 本人 DRAFT → PENDING；服务端解析直属主管账号为 `approver` |
| `approve` | `POST /api/confirmation_requests:approve` | 仅被指定审批人；PENDING → APPROVED + 员工 REGULAR |
| `reject` | `POST /api/confirmation_requests:reject` | 仅被指定审批人；PENDING → REJECTED（意见必填），员工仍 PROBATION |

## 关键保证

- `employeeId` / `approverId` / `status` / `version` 不接受客户端赋值。
- 事务：申请、员工、审批轨迹多写同一数据库事务，任一步失败整体回滚。
- 并发：同一员工的并发提交、同一申请的并发决定用 `SELECT ... FOR UPDATE` 行锁串行化。
- 授权：动作内部显式校验账号绑定、数据范围与状态，不依赖“仅已登录”。

## 目录结构

```
server.js            # 编译产物入口（指向 dist/server/index.js）
client.js            # 编译产物入口（指向 dist/client/index.js）
client.d.ts          # 客户端类型声明
dist/server/index.js # 导出插件类
dist/server/plugin.js# 插件类（注册动作 + ACL 放行）
dist/server/actions.js# 四个受控动作实现
dist/client/index.js # UMD 客户端入口（RequireJS 可加载；本插件无前端逻辑，
                    #  仅注册空模块，页面/按钮由平台 schema 机制渲染，见 REV-001/009）
```

## 客户端入口（REV-009）

平台 `pm:listEnabled` 会把 `dist/client/index.js` 下发给浏览器。该文件为合法
UMD 模块（无业务逻辑，前端完全由 `scripts/build_pages_v2.py` 生成的页面
schema + 平台内置 `useCustomizeRequestActionProps` 动作接线驱动）。验收：
`node scripts/verify_browser_page.js`（RequireJS 注册表 + dist 200 + 页面无报错）。

加载路径：`packages/plugins/@nocobase/plugin-hris-confirmation/` 以只读方式挂载到
容器的 `storage/plugins/`，由 `scripts/build_s3.py` 在 `applicationPlugins` 注册，
应用重启后由插件管理器自动符号链接并加载。
