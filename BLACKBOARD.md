# 项目共享黑板

唯一活动交接入口；实际执行的验证与计划必须区分。

## 当前状态

| 项目 | 状态 |
|---|---|
| 系统 | HRIS员工与转正管理系统，NocoBase 2.2.37 + PostgreSQL 16.3 |
| 功能验收 | ACCEPTED；N1–N5已完成，REV-001–013均CLOSED |
| 验收依据 | [摘要](docs/quality/ACCEPTANCE.md)、[验证报告](docs/quality/VERIFICATION.md) |
| 文档整理 | ACCEPTED：用户已确认并授权提交推送 |
| Git | IN_PROGRESS：发布当前整理版本至MothingAI/HRIS |
| 入口 | http://127.0.0.1:13000 |
| 历史 | 本地 .tools/project-history/release-20261118/；仅归档，不是活动黑板 |

## 协作与范围

- Codex负责设计、审查；用户已授权本轮直接整理目录、文档和提交准备。
- Claude Code负责已有业务实现与验证；本轮无需再次交接实现。
- 不扩展业务功能，不覆盖现有修改，不删除用户文件。
- 凭据、dump、运行数据不入库；不声称未执行测试通过。
- 时间采用实际+08:00；历史原文保留于本地归档。

## 本轮交接

- 将公开文档分为design、operations、demo、quality、evidence。
- 公开入口采用业务系统介绍，个人说明、早期提示词与过程记录保留本地。
- 保留scripts位置和插件手写dist源码，避免破坏初始化链。
- 用户先查看整体结构和README，再决定Git提交与推送。

### 2026-11-18T14:38:30+08:00 · Codex · 文档与仓库整理完成 · READY_FOR_USER_REVIEW

- 公开文档分组与README重写完成；历史118个文件保留于本地归档，不作为当前提交内容。原验收ACCEPTED不变。
- 本轮检查：138个本地链接有效；候选文件当前本地密钥精确值命中0，指定措辞命中0；git diff --check退出0。检查针对当前工作树，不改写已有Git历史。
- 独立只读访问getInfo和admin均200；Chrome实际登录和插件加载检查9/9、exit0，无业务数据写入。未重跑完整API套件。
- 移除build_s2的硬编码密码回退，保留已配置值、缺失即停止；通过AST提取表达式独立核对提供/缺失配置两个分支，不执行seed写入。
- 未暂存、提交或推送，远程仓库尚未配置。下一步：用户查看README及目录，确认提交范围并提供远程地址后再继续。

### 2026-11-18T14:43:50+08:00 · Codex · Git发布开始 · IN_PROGRESS

- 用户已确认目录和README并明确授权推送至 https://github.com/MothingAI/HRIS.git 。
- 远程为空；以当前整理后的工作树创建独立首次提交，旧开发历史和归档继续保留本地，不推送旧历史。
- 只纳入环境模板、协作文档、业务配置、部署、插件、运行验证脚本和当前文档证据，不纳入.env、.tools、backups或依赖。
