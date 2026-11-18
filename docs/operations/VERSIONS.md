# 版本锁定（S0）

核验日期：2026-11-10 18:24 +08:00。实施人：Claude Code。

## 锁定版本

| 组件 | 版本 / 标签 | 镜像源 | 说明 |
|---|---|---|---|
| NocoBase 平台 | `2.2.37-full` | `registry.cn-shanghai.aliyuncs.com/nocobase/nocobase:2.2.37-full` | 2.2 稳定线最新（2026-10-31 发布）；`-full` 含数据库客户端与 LibreOffice，官方推荐用于 PostgreSQL |
| PostgreSQL | `16`（`16.3`） | `registry.cn-shanghai.aliyuncs.com/nocobase/postgres:16@sha256:dc0dc485abfdd0901b10af592ccc3c25ed821936a962969b99d3b488e24edd39` | NocoBase 官方阿里云镜像；实际运行版本 PostgreSQL **16.3**；`wal_level=logical`；digest 优先于 `:16` 浮动标签 |

- 不采用 `latest`：NocoBase 补丁频繁、不支持降级，固定精确标签保证可复现。
- PostgreSQL 使用“`:16` 浮动主版本标签 + 已核验镜像 digest”锁定（REV-008）：digest 优先于标签，
  部署（[compose.yaml](../../deploy/compose.yaml)）与恢复（[restore.ps1](../../scripts/restore.ps1)）使用同一 digest，保证原库/恢复库/演示
  环境为同一镜像。实际版本号 `16.3` 通过运行容器内 `SELECT version()` 核验。
- 已实测：两个标签的 `docker manifest inspect` 在阿里云源可解析。

## 版本来源

1. WebSearch（2026-11-10）确认 NocoBase 2.2 稳定线最新为 **v2.2.37**（2026-10-31）；NocoBase 3 尚在 beta（`v3-develop` 分支）。
2. 官方 Docker 安装示例（`docs/docs/en/get-started/installation/docker.mdx`，GitHub）推荐 `nocobase/nocobase:2.2.37-full` + PostgreSQL 16。
3. `docker manifest inspect registry.cn-shanghai.aliyuncs.com/nocobase/nocobase:2.2.37-full` 实测返回 OCI index 清单，标签存在。

## 许可结论

- NocoBase 2.0（2026-02-15）起，开源协议由 AGPL-3.0 改为 **Apache-2.0**（PR #8682），社区版可免费商业使用。
- 大量原商业插件转免费开源，但**审批工作流插件仍为商业插件**；本系统使用自定义审批插件。

## 初始化凭据

本仓库通过.env的INIT_ROOT_*配置创建管理员，不依赖上游默认口令。业务账号密码必须由DEMO_PASSWORD提供，初始化脚本没有硬编码回退值。

## 后续锁定说明

- 实际运行后如需更新版本，必须重新核验 PostgreSQL 兼容性与配置导出格式，不静默升级。
- 演示实例的版本绑定与备份恢复范围见 [REPRODUCE.md](REPRODUCE.md)（S4 交付）。
