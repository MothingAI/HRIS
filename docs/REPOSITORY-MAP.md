# 仓库地图

## 目录职责

| 路径 | 内容 |
|---|---|
| deploy/compose.yaml | 本地应用与数据库部署 |
| config/ | 数据模型、角色权限与页面配置说明 |
| packages/plugins/@nocobase/plugin-hris-confirmation/ | 客户端入口及服务端审批扩展；dist/server 为手写源码 |
| scripts/ | 初始化、运行维护、演示准备和验证 |
| docs/design/ | 架构、审批实现和报表定义 |
| docs/operations/ | 部署、复现和版本来源 |
| docs/demo/ | 账号、操作演示及字段扩展 |
| docs/quality/ | 验证汇总、验收场景、审查标准和限制 |
| docs/evidence/ | 测试日志及真实页面截图 |
| BLACKBOARD.md | 唯一活动交接入口 |

`.env`、`.tools/`、`backups/`、依赖与运行数据保持本地并由 .gitignore 排除。

## 脚本索引

| 用途 | 文件 |
|---|---|
| 启停 | start.ps1、stop.ps1 |
| 完整初始化 | seed.ps1 |
| 模型、账号、权限 | build_s2.py |
| 插件注册及受控写入权限 | build_s3.py |
| 页面与字段元数据 | build_ui.py、fix_field_meta.py、build_pages_v2.py |
| 演示与动态字段 | prep_demo.py、demo_lowcode_field.py、demo_lowcode_employee_field.py |
| 备份、恢复 | backup.ps1、backup_snapshot.sh、restore.ps1 |
| API 验证 | verify_permissions.py、verify_reports.py、verify_regression.py、verify_business.py |
| 约束与备份专项 | verify_unique_binding.py、probe_backup_concurrent.py |
| 浏览器探针 | browser/、verify_browser_page.js、verify_client_plugin.js |
| 公共辅助 | nb.py、verify_common.py、target_binding.py |
| 历史兼容工具 | fix_page_tabs.py、verify-business.ps1、verify-permissions.ps1；日常运行优先使用当前运行说明 |

脚本保留现有位置，避免破坏 seed、导入与浏览器输出路径。

## 文档整理

原 docs 根目录按用途分为 design、operations、demo、quality。旧 RUNBOOK 的有效启停和故障排查内容并入运行说明；历次验证由当前汇总与原始证据承接。

早期计划、修复提示词、长篇审查过程和旧黑板保存在本地 `.tools/project-history/release-20261118/`，不纳入当前提交。原文保留供本地追溯，本轮未删除内容。当前功能结论见[验收摘要](quality/ACCEPTANCE.md)。
