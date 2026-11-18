# 验证报告

本文汇总截至2026-11-18已执行的验证。业务测试通过真实登录会话访问服务端API，浏览器验证使用Chrome。下列结果来自既有日志，本次文档整理未重跑全部套件。

## 当前结果

| 套件 | 已记录结果 | 覆盖 |
|---|---|---|
| verify_permissions.py | 9/9 | 员工/主管/HR数据范围、字段白名单、默认写入口 |
| verify_reports.py | 13/13 | 在职/试用/部门人数及审批前后相对变化 |
| verify_regression.py | 29/29 | 角色撤销、审批资格、账号绑定、关系冻结、驳回意见 |
| verify_business.py | 25/25 | 草稿、提交、决定、重复请求、并发、故障回滚、目标门禁 |
| verify_unique_binding.py | 首跑18/18、重跑22/22 | 连续建字段/修改字段/删字段后约束保留，重复绑定及并发竞争 |

四个API套件共 **76项**，唯一绑定专项单独计数。证据：[套件](../evidence/rev-round2-suites.txt)、[唯一绑定](../evidence/rev-round2-unique-binding.txt)。

## 关键验证

- **服务端授权**：员工只看本人，主管仅看本人及直属下属；非审批人、撤销主管角色及伪造字段请求被拒绝。菜单隐藏不作为授权依据。
- **并发与事务**：两个竞争决定返回一个成功、一个冲突；注入 BEFORE INSERT 触发器阻断审批轨迹写入后，申请、员工与轨迹整体回滚。故障注入只在隔离库执行。
- **动态字段与约束**：连续低代码字段同步后，账号唯一绑定仍存在；HR表单填写保存、重开回显、旧记录打开和员工档案新列均有浏览器证据。
- **统计范围**：HR概览与API口径一致；员工/主管直接调用charts仍受数据范围限制；审批后试用与待审批减少1，在职人数不变。
- **演示重复性**：工具清单登记实际ID，三类工号/邮箱/手工申请碰撞得到保留或跳过；默认预填与blank空白两种路径输出账号，终态历史保留。
- **备份恢复**：40秒、105轮并发写入期间，共享快照dump与清单恢复一致；篡改清单、截断dump及显式缺失清单均被拒绝。日志包含早期测试夹具错误，修正后的最终负测才计入结果。
- **空库复现**：独立Compose项目从新卷完成全链seed，再执行API与浏览器验证，证明无需原实例残留配置。

证据入口见[完整索引](../evidence/README.md)。

## 浏览器验证

已记录草稿保存与编辑、提交、驳回必填意见、查看意见与轨迹、重新申请、通过及转正的完整链路。统计页和动态字段在主实例与隔离实例中有重复验证。

- [审批过程截图](../evidence/n2-04-mgr-traj-submitted.png)
- [通过轨迹](../evidence/n2-13-traj-approved.png)
- [统计日志](../evidence/rev-fix-stats-console.txt)
- [字段日志](../evidence/rev-fix-lowcode-console.txt)

截图中的人员状态、待办与统计是当次实例快照，不作为当前运行库固定数值。

## 运行验证

使用[运行说明](../operations/REPRODUCE.md)第6节创建隔离实例，在设置全部NB_*变量后执行：

```powershell
python scripts/verify_permissions.py
python scripts/verify_reports.py
python scripts/verify_regression.py
$env:HRIS_ALLOW_FAULT_INJECT='1'
python scripts/verify_business.py
Remove-Item Env:HRIS_ALLOW_FAULT_INJECT
python scripts/verify_unique_binding.py
```

业务/回归/报表/唯一绑定套件在任何写入前检查目标。缺省、主项目、跨项目、非回环目标被拒绝；seed的同源检查允许合法初始化主实例。浏览器运行方式见运行说明。

## 证据日期与审查来源

2026-11-12的四套历史计数为9/12/29/24；2026-11-18后为9/13/29/25，不能将旧74项记录写成当前76项。

此前Codex独立完成空库初始化、76项API与唯一绑定专项，复核统计/字段浏览器；审批全链沿用11-12独立运行。最后四项修复结合代码与Claude隔离专项证据关闭，独立补做只读主实例门禁检查，没有声称每轮重跑全套。功能结论见[验收摘要](ACCEPTANCE.md)。

## 未覆盖

未执行生产容量压测、超过两个竞争请求的负载测试、跨大版本升级恢复、移动端适配、多级会签及高可用故障切换。
