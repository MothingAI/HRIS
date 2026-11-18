# Idempotent full init: model -> plugin -> restart -> field metadata -> UI pages.
# 幂等：已存在的集合/角色/账号/员工/插件/页面会跳过，不清数据。
# N4 起串联完整依赖顺序（详见 docs/operations/REPRODUCE.md §2）：
#   build_s2（集合/角色/账号/ACL/种子）
#   build_s3（注册 hris-confirmation 插件 + 收紧默认 CRUD）
#   重启应用并等待就绪（插件受控动作在启动时加载）
#   build_ui（路由骨架 + 角色菜单）
#   demo_lowcode_field（创建 confirmation_requests.trialHarvest —— 「我的转正」表单
#                       引用该字段，属最终交付模型的一部分；幂等，已存在则跳过）
#   fix_field_meta（字段中文标题/uiSchema/枚举，页面列头依赖它；严格终验，
#                   曾在空库复现中拦住 trialHarvest 缺失的依赖顺序问题）
#   build_pages_v2（页面 schema + 受控按钮接线 + 人事概览统计）
#   -WithDemo 时：demo_lowcode_employee_field（低代码员工字段进表单/列）
#                 prep_demo（可重复演示的 PENDING 申请，专用演示账号 employee.c）
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\seed.ps1
#   powershell ... -File .\scripts\seed.ps1 -WithDemo          # 追加演示字段+演示申请
#   powershell ... -File .\scripts\seed.ps1 -RebuildPages      # 强制按最终形态重建页面 schema
# 指向隔离实例（REV-007：三个目标变量必须【同时】显式并在第一步校验同源，
# 防止 API 打一个实例、SQL/重启落到另一个实例）：
#   $env:NB_BASE='http://127.0.0.1:13002'
#   $env:NB_APP_CONTAINER='hris-n4-app-1'
#   $env:NB_DB_CONTAINER='hris-n4-postgres-1'
#   powershell ... -File .\scripts\seed.ps1 -WithDemo
param(
    [switch]$WithDemo,
    [switch]$RebuildPages
)
$ErrorActionPreference = 'Stop'

$RootDir = Split-Path -Parent $PSScriptRoot
$Base = if ($env:NB_BASE) { $env:NB_BASE } else { 'http://127.0.0.1:13000' }
$AppContainer = if ($env:NB_APP_CONTAINER) { $env:NB_APP_CONTAINER } else { 'hris-app-1' }

# REV-007：任何写入之前校验 API/app/DB 同一目标。NB_* 只要显式设置了一个，
# 三个就必须全部显式（部分设置直接拒绝）；全部缺省 = 主实例（也要一致可连）。
$HasBase = [bool]$env:NB_BASE
$HasApp = [bool]$env:NB_APP_CONTAINER
$HasDb = [bool]$env:NB_DB_CONTAINER
if ($HasBase -or $HasApp -or $HasDb) {
    if (-not ($HasBase -and $HasApp -and $HasDb)) {
        Write-Error ("NB_BASE / NB_APP_CONTAINER / NB_DB_CONTAINER 必须同时显式设置" +
            "并指向同一隔离项目（当前：BASE=$HasBase APP=$HasApp DB=$HasDb）")
        exit 1
    }
}
python scripts/target_binding.py --check
if ($LASTEXITCODE -ne 0) { Write-Error "target binding check failed"; exit $LASTEXITCODE }

function Wait-Ready {
    param([string]$Url, [int]$TimeoutSec = 300)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "$Url/api/app:getInfo" -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return $true }
        } catch { Start-Sleep -Seconds 5 }
    }
    Write-Error "app not ready at $Url within ${TimeoutSec}s"; return $false
}

Push-Location $RootDir
try {
    if (-not (Wait-Ready -Url $Base)) { exit 1 }

    Write-Host "== S2: collections / roles / accounts / seed / ACL =="
    python scripts/build_s2.py
    if ($LASTEXITCODE -ne 0) { Write-Error "build_s2.py failed"; exit $LASTEXITCODE }

    Write-Host "== S3: register hris-confirmation plugin + tighten default CRUD =="
    python scripts/build_s3.py
    if ($LASTEXITCODE -ne 0) { Write-Error "build_s3.py failed"; exit $LASTEXITCODE }

    Write-Host "== Restart app ($AppContainer) so plugin actions load =="
    docker restart $AppContainer | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Error "docker restart failed"; exit 1 }
    if (-not (Wait-Ready -Url $Base)) { exit 1 }

    Write-Host "== UI: routes + role menus =="
    python scripts/build_ui.py
    if ($LASTEXITCODE -ne 0) { Write-Error "build_ui.py failed"; exit $LASTEXITCODE }

    Write-Host "== Model: ensure trialHarvest field (referenced by 我的转正 form) =="
    python scripts/demo_lowcode_field.py
    if ($LASTEXITCODE -ne 0) { Write-Error "demo_lowcode_field.py failed"; exit $LASTEXITCODE }

    Write-Host "== UI: field metadata (titles/uiSchema/enums) =="
    python scripts/fix_field_meta.py
    if ($LASTEXITCODE -ne 0) { Write-Error "fix_field_meta.py failed"; exit $LASTEXITCODE }

    Write-Host "== UI: page schemas + controlled actions + HR overview =="
    if ($RebuildPages) {
        foreach ($p in @('员工管理','部门管理','我的档案','我的转正','待我审批','人事概览')) {
            python scripts/build_pages_v2.py --rebuild $p
            if ($LASTEXITCODE -ne 0) { Write-Error "build_pages_v2 --rebuild $p failed"; exit $LASTEXITCODE }
        }
    } else {
        python scripts/build_pages_v2.py
        if ($LASTEXITCODE -ne 0) { Write-Error "build_pages_v2.py failed"; exit $LASTEXITCODE }
    }

    if ($WithDemo) {
        Write-Host "== Demo: lowcode employee field (emergencyContact) into form/table =="
        python scripts/demo_lowcode_employee_field.py
        if ($LASTEXITCODE -ne 0) { Write-Error "demo_lowcode_employee_field.py failed"; exit $LASTEXITCODE }
        Write-Host "== Demo: repeatable PENDING request via dedicated demo account =="
        python scripts/prep_demo.py
        if ($LASTEXITCODE -ne 0) { Write-Error "prep_demo.py failed"; exit $LASTEXITCODE }
    }

    Write-Host "Seed done (idempotent). Base=$Base Container=$AppContainer"
} finally {
    Pop-Location
}
