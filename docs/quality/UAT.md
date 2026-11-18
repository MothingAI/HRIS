# 验收场景

下列场景均有既有API或浏览器验证依据。本次整理不补造执行结果。人数、账号状态与待办数量从实际实例读取，截图为当时快照。

| 场景 | 操作与预期 | 证据 |
|---|---|---|
| HR维护主数据 | 筛选员工，编辑基本档案，部门与状态正常显示 | [列表](../evidence/n1-employee-admin-hr.png) |
| 草稿编辑与提交 | 同一草稿可反复编辑；提交后PENDING且本人不可再改 | [保存](../evidence/n2-01-draft-saved.png)、[编辑](../evidence/n2-02-draft-edited.png)、[提交](../evidence/n2-03-submitted.png) |
| 主管通过 | 指定主管决定，申请APPROVED，员工转正式并留轨迹 | [审批轨迹](../evidence/n2-13-traj-approved.png) |
| 驳回意见 | 空意见服务端拒绝，合法意见保存且员工可见 | [必填校验](../evidence/n2-05-reject-comment-required.png)、[驳回](../evidence/n2-06-rejected.png) |
| 重新申请 | 驳回后新建申请，旧结果保留只读 | [重新申请](../evidence/n2-09-reapplied.png) |
| 读越权隔离 | 员工仅本人，主管仅本人及直属下属，无法读其他范围 | [权限套件9项](../evidence/rev-round2-suites.txt) |
| 统计越权隔离 | 员工/主管无概览菜单，直接charts请求仍受数据范围过滤 | [统计浏览器日志](../evidence/rev-fix-stats-console.txt) |
| 状态与绑定保护 | HR通用编辑不能改任职状态；重复账号绑定、自管理、在途改关系被拒绝 | [回归29项](../evidence/rev-round2-suites.txt)、[唯一绑定专项](../evidence/rev-round2-unique-binding.txt) |
| 重复、并发与回滚 | 重复提交冲突；竞争决定仅一个成功；隔离故障注入整体回滚 | [业务25项](../evidence/rev-round2-suites.txt) |
| 低代码扩展 | 新字段填值保存重开回显、旧记录可打开、员工档案显示新列 | [浏览器日志](../evidence/rev-fix-lowcode-console.txt) |

## 运行交付场景

| 场景 | 预期与既有证据 |
|---|---|
| 空库复现 | 新项目全链seed并执行套件：[复现日志](../evidence/rev012-docs-verbatim-run.txt) |
| 测试写入门禁 | 主实例、缺省、错配及外部目标被拒绝：[目标负测](../evidence/rev007-target-gate.txt) |
| 备份恢复 | 同一快照dump与清单恢复一致，损坏及错误清单拒绝：[备份日志](../evidence/rev011-backup-snapshot.txt) |
| 演示归属 | 不接管占用账号、不删手工单、不改终态历史，blank给出可登录身份：[最终断言](../evidence/rev010-prep-ownership-part5.txt) |

未覆盖生产容量、高负载、移动端、多级会签和跨版本升级。完整命令见[运行说明](../operations/REPRODUCE.md)，结论见[验收摘要](ACCEPTANCE.md)。
