# 演示账号（S2，N3 增补）

本文件只记录用户名、角色、员工绑定和密码获取方式，不包含真实密钥。

## 密码

所有业务演示账号使用同一本地演示密码，值在 `.env` 的 `DEMO_PASSWORD`（本地文件，
不入库）。演示环境请从 `.env` 读取；切勿使用真实或生产口令。

## 账号清单

| 用途 | 邮箱 | 角色 | 绑定员工 | 说明 |
|---|---|---|---|---|
| 平台管理员 | `admin@example.com` | `root` | — | 密码见 `.env` 的 `INIT_ROOT_PASSWORD` |
| HR | `hr@example.com` | `hr` | —（独立账号） | 全公司只读，维护基本档案 |
| 主管 A | `manager.a@example.com` | `manager` | E001 王建国（技术部经理） | 审批 E002–E008 的申请 |
| 主管 B | `manager.b@example.com` | `manager` | E009 林芳（产品部经理） | 审批 E010–E015 的申请 |
| 员工 A | `employee.a@example.com` | `employee` | E002 李思远（正式，直属王建国） | 我的档案含低代码新增“紧急联系人”列 |
| 员工 B | `employee.b@example.com` | `employee` | E011 郑雪（正式，直属林芳） | 已走完驳回→再申请→通过全链路 |
| 员工 C | `employee.c@example.com` | `employee` | E003 张敏（试用，直属王建国） | 早期（N3/N4/N5）演示账号，现为普通种子账号保留 |
| 现场演示身份 | `demo01@example.com`… | `employee` | DEMO-01…（技术部，直属王建国） | [prep_demo.py](../../scripts/prep_demo.py) 生成与顺延（REV-010 第二轮）：所有权=工具清单 `prepDemoRegistry`（仅 root 可访问，登记实际创建的员工/账号/申请 ID；工号/邮箱等可编辑字段不作凭据）。默认=预填待办（幂等顺延、历史保留）；`--blank`=空白全流程（只清清单在途单，收尾打印本轮演示账号）。两模式互斥，二选一 |

> 均为虚构姓名与保留域名 `example.com`，不使用真实个人信息。

## 角色与权限

- **员工**：只能看本人档案与本人转正申请，无更新/删除权限。
- **主管**：看本人 + 直属下属档案，看本人及被指定审批的申请；不能看其他主管的数据。
- **HR**：看全部员工与申请；可维护基本档案（工号/姓名/部门/岗位/主管/账号绑定/日期），
  但**不能直接修改任职状态**（状态只能通过转正审批流程流转）。

详细数据范围与字段白名单见 [permissions.md](../../config/permissions.md)。

## 员工与账号绑定

员工档案的 `user` 字段（唯一）指向账号；主管本人也有正确绑定（王建国 ↔
`manager.a@example.com`，林芳 ↔ `manager.b@example.com`）。绑定由
[build_s2.py](../../scripts/build_s2.py) 的种子逻辑完成。
