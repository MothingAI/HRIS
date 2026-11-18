# 运行与复现

适用版本：NocoBase 2.2.37-full + PostgreSQL 16.3。以下命令从仓库根目录执行，Windows使用PowerShell。

## 1. 环境与首次启动

需要Docker Desktop（Engine已启动、Compose v2可用）、Python 3和PowerShell；浏览器探针需要Node.js与Chrome。Docker、Python须能从当前终端调用。

```powershell
# 已有.env时不要覆盖
Copy-Item .env.example .env
# 编辑并替换APP_KEY、POSTGRES_PASSWORD、INIT_ROOT_PASSWORD、DEMO_PASSWORD
python -m pip install requests
powershell -NoProfile -File .\scripts\start.ps1
docker compose --env-file .env -f deploy/compose.yaml logs -f app
```

看到应用启动完成后Ctrl+C退出日志跟随，继续初始化。平台地址为[http://127.0.0.1:13000](http://127.0.0.1:13000)，仅监听本机；数据库不向宿主机暴露端口。管理员凭据取自本地.env，只有首次安装时自动创建。

## 2. 初始化

```powershell
powershell -NoProfile -File .\scripts\seed.ps1 -WithDemo
```

脚本顺序为：模型/账号/角色/ACL → 注册插件 → 重启并等待就绪 → 页面路由 → trialHarvest字段 → 字段元数据 → 页面/受控按钮/统计 → 可选员工扩展字段和演示申请。任一步失败则停止；省略-WithDemo可不生成可选演示内容。

正常初始化可重跑，但不是数据库重置；既有审批历史保留。NB_BASE、NB_APP_CONTAINER、NB_DB_CONTAINER若显式设置，必须三者齐全并属于同一个Compose项目。平台插件动作需重启生效，已纳入seed。

业务账号见[账号说明](../demo/DEMO-ACCOUNTS.md)。

## 3. 日常操作

```powershell
powershell -NoProfile -File .\scripts\stop.ps1
powershell -NoProfile -File .\scripts\start.ps1
docker compose --env-file .env -f deploy/compose.yaml ps
docker compose --env-file .env -f deploy/compose.yaml logs --tail 80 app
```

stop保留数据卷。主实例不要执行down -v。查看日志时无需输出.env或docker compose config的解析结果。

| 现象 | 排查 |
|---|---|
| 应用尚未就绪 | 查看app与postgres日志；首次初始化需等待 |
| 登录失败 | 核对首次初始化的管理员配置；修改.env不会自动修改已有账号密码 |
| 端口冲突 | 检查13000端口；测试环境使用独立APP_HOST_PORT |
| 找不到docker | 将Docker Desktop的CLI目录加入当前终端PATH |
| 镜像拉取失败 | 当前Compose使用阿里云NocoBase镜像源，检查网络及镜像地址 |
| 插件或页面加载失败 | 核对插件挂载，查看app日志；插件更新后重启应用，页面按seed链初始化 |

## 4. 备份与恢复

```powershell
powershell -NoProfile -File .\scripts\backup.ps1
# 恢复最近备份，或用-BackupFile明确指定文件
powershell -NoProfile -File .\scripts\restore.ps1
```

备份在本地backups/产出dump和同名.manifest.json。dump与清单使用同一PostgreSQL导出快照，避免备份期间普通业务写入导致计数不一致。清单带dump_sha256。

restore在本次新建的隔离数据库/卷中恢复，验证摘要、核心表、约束、守卫和业务快照，结束后清理自己创建的资源，不覆盖主库。-Keep可保留本次副本。显式-Manifest指定的文件不存在则报错；旧备份无清单只做结构验证并给出警告。

已记录共享快照备份期间105轮并发写入后恢复一致，以及错误清单/截断dump/缺失清单的负测，详见[备份证据](../evidence/rev011-backup-snapshot.txt)。仅承诺当前版本的本地恢复，备份不提交Git。

## 5. 可重复操作演示

以下模式二选一：

```powershell
# 完整草稿→编辑→提交→审批
python scripts/prep_demo.py --blank
# 或：预填待办，跳过员工提交，从主管审批开始
python scripts/prep_demo.py
```

使用脚本实际输出的账号与统计，不固定某个demo编号或人数。工具归属由root专用prepDemoRegistry登记实际ID确认；工号/邮箱碰撞时跳号，不接管普通账号。blank只清登记的在途申请，手工记录与终态历史保留。旧--reset已移除。

详见[演示流程](../demo/DEMO-SCRIPT.md)与[低代码扩展](../demo/LOWCODE-DEMO.md)。

## 6. 空库隔离复现与验证

以下命令创建独立项目hris-check，使用独立端口、容器与数据卷，不改主实例。

```powershell
$env:APP_HOST_PORT='13002'
$env:NB_BASE='http://127.0.0.1:13002'
$env:NB_APP_CONTAINER='hris-check-app-1'
$env:NB_DB_CONTAINER='hris-check-postgres-1'
docker compose -p hris-check --env-file .env -f deploy/compose.yaml up -d
# 等应用就绪后执行
powershell -NoProfile -File .\scripts\seed.ps1 -WithDemo

python scripts/verify_permissions.py
python scripts/verify_reports.py
python scripts/verify_regression.py
$env:HRIS_ALLOW_FAULT_INJECT='1'
python scripts/verify_business.py
Remove-Item Env:HRIS_ALLOW_FAULT_INJECT
python scripts/verify_unique_binding.py

# 浏览器探针，Chrome与Node.js已安装时执行
$env:BASE='http://127.0.0.1:13002'
$env:EVID_PREFIX='local-'
$env:CDP_PORT='9263'
node scripts/browser/probe-n3-stats.js
$env:CDP_PORT='9264'
node scripts/browser/probe-n3-lowcode.js
```

检查每条命令的退出码与日志，不用最后一个命令的成功覆盖中途失败。写入套件要求显式同源隔离目标，回环主机且非主项目hris，否则在写入前拒绝。seed允许合法主库初始化。

确认仅清理本次hris-check环境后：

```powershell
docker compose -p hris-check --env-file .env -f deploy/compose.yaml down -v
# 清除本终端测试目标，后续操作回到默认主实例
'APP_HOST_PORT','NB_BASE','NB_APP_CONTAINER','NB_DB_CONTAINER','BASE','EVID_PREFIX','CDP_PORT' |
    ForEach-Object { Remove-Item -LiteralPath "Env:$_" -ErrorAction SilentlyContinue }
```

空库全链与套件已在历史隔离环境中实际验证，本节是当前整理后的运行路径，本次文档整理未重新执行。历史计数与当前结果见[验证报告](../quality/VERIFICATION.md)，兼容细节见[已知边界](../quality/LIMITATIONS.md)。
