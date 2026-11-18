# 验证证据索引

本目录保留真实测试日志和页面截图，旧失败与后续修正可能同时存在。当前结果以验证汇总及日志最后一次有效执行为准，不将不同日期、实例的人数视为固定预期。

## 当前验证

| 内容 | 证据 |
|---|---|
| 四套 API：9 + 13 + 29 + 25 = 76 | [第二轮套件](rev-round2-suites.txt) |
| 唯一绑定：首跑18、重跑22 | [专项](rev-round2-unique-binding.txt) |
| 主实例写入门禁与隔离目标 | [目标校验](rev007-target-gate.txt) |
| 演示工具归属与碰撞保护 | [过程](rev010-prep-ownership.txt)、[最终断言](rev010-prep-ownership-part5.txt)；中间过程 part2–4 同目录保留 |
| 主实例既有演示单识别 | [复核](rev010-main-prep-verify.txt) |
| 共享快照备份与并发恢复 | [备份专项](rev011-backup-snapshot.txt)，含错误夹具及修正后结果 |
| 空库按文档重建 | [复现日志](rev012-docs-verbatim-run.txt) |
| 统计页面与字段浏览器验证 | [统计](rev-fix-stats-console.txt)、[字段](rev-fix-lowcode-console.txt) |

## 页面与流程

| 内容 | 截图 |
|---|---|
| 员工管理 | [列表](n1-employee-admin-hr.png) |
| 保存、编辑、提交 | [草稿](n2-01-draft-saved.png)、[编辑](n2-02-draft-edited.png)、[提交](n2-03-submitted.png) |
| 审批详情与轨迹 | [详情](n2-04-mgr-traj-submitted.png)、[通过轨迹](n2-13-traj-approved.png) |
| 驳回与重新申请 | [必填校验](n2-05-reject-comment-required.png)、[驳回](n2-06-rejected.png)、[重新申请](n2-09-reapplied.png) |
| 统计 | [人事概览](fix-n3-hr-overview.png) |
| 低代码字段 | [填写](fix-n3-lowcode-2-filled.png)、[回显](fix-n3-lowcode-4-reopen.png)、[员工侧](fix-n3-lowcode-6-employee-view.png) |

历史 n1/n2/n3/n4 前缀代表对应阶段，计数及状态以日志日期为准。当前汇总见[验证报告](../quality/VERIFICATION.md)。
