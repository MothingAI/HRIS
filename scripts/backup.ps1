# Backup the HRIS demo database (pg_dump, full schema + data) into backups/.
# The dump may contain demo-account password hashes; backups/ is git-ignored, do not commit.
#
# REV-011 (2026-11-18 第二轮): dump 与清单共享同一导出快照。
# 旧缺陷：pg_dump 用自己的快照，随后另起 psql 会话查询清单——两次快照间隙里
# 正常的员工新增/审批会让"dump 24 人 / 清单 25 人"，恢复校验对完全有效的备份
# 误判失败。现在 backup_snapshot.sh 在数据库容器内先 pg_export_snapshot() 并用
# fifo 保持会话打开，pg_dump --snapshot=<id> 与清单 SQL（SET TRANSACTION SNAPSHOT）
# 落在完全相同的数据视图上；备份期间的持续写入不再造成 dump/清单不一致。
# 清单还记录 dump_sha256，restore.ps1 在 pg_restore 之前先校验文件完整性。
#
# Usage: powershell -NoProfile -File .\scripts\backup.ps1
#        powershell -NoProfile -File .\scripts\backup.ps1 -Container hris-fix-postgres-1
#   -Container  source DB container (default hris-postgres-1 = main instance)
#   -OutputDir  output directory (default backups/)
param(
    [string]$Container = 'hris-postgres-1',
    [string]$OutputDir = ''
)
$ErrorActionPreference = 'Stop'

$RootDir = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $RootDir '.env'
if (-not $OutputDir) { $OutputDir = Join-Path $RootDir 'backups' }

if (-not (Test-Path -LiteralPath $EnvFile)) {
    Write-Error "Missing .env."
    exit 1
}

# Read .env for DB credentials (do not print secrets).
$envVars = @{}
foreach ($raw in (Get-Content -LiteralPath $EnvFile)) {
    if ([string]::IsNullOrWhiteSpace($raw)) { continue }
    $line = $raw.Trim()
    if ($line.StartsWith('#')) { continue }
    $i = $line.IndexOf('=')
    if ($i -gt 0) { $envVars[$line.Substring(0, $i)] = $line.Substring($i + 1) }
}
$DbUser = $envVars['POSTGRES_USER']; if (-not $DbUser) { $DbUser = 'nocobase' }
$DbName = $envVars['POSTGRES_DB']; if (-not $DbName) { $DbName = 'nocobase' }
$DbPass = $envVars['POSTGRES_PASSWORD']

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$OutFile = Join-Path $OutputDir "hris-backup-$Stamp.dump"

# Manifest SQL (REV-011). Executed INSIDE the exported snapshot by backup_snapshot.sh.
# Keep in sync with restore.ps1 $ManifestSql.
$ManifestSql = @'
SELECT json_build_object(
  'tables', (SELECT coalesce(jsonb_agg(tablename ORDER BY tablename), '[]'::jsonb)
             FROM pg_tables WHERE schemaname = 'public'),
  'key_counts', json_build_object(
    'employees',              (SELECT count(*) FROM employees),
    'departments',            (SELECT count(*) FROM departments),
    'confirmation_requests',  (SELECT count(*) FROM confirmation_requests),
    'approval_actions',       (SELECT count(*) FROM approval_actions),
    'users',                  (SELECT count(*) FROM users),
    'roles',                  (SELECT count(*) FROM roles),
    'collections',            (SELECT count(*) FROM collections),
    'fields',                 (SELECT count(*) FROM fields),
    'applicationPlugins',     (SELECT count(*) FROM "applicationPlugins")
  ),
  'employees_by_status', (SELECT coalesce(jsonb_object_agg(status, c), '{}'::jsonb)
                          FROM (SELECT status, count(*) c FROM employees GROUP BY status) t),
  'requests_by_status',  (SELECT coalesce(jsonb_object_agg(status, c), '{}'::jsonb)
                          FROM (SELECT status, count(*) c FROM confirmation_requests GROUP BY status) t),
  'userid_unique_index', (SELECT count(*) FROM pg_indexes
                          WHERE tablename = 'employees'
                            AND indexdef LIKE 'CREATE UNIQUE%("userId")%'),
  'relation_guard_trigger', (SELECT count(*) FROM information_schema.triggers
                             WHERE event_object_table = 'employees'
                               AND trigger_name = 'hris_employee_relation_guard'),
  'hris_plugin', (SELECT count(*) FROM "applicationPlugins"
                  WHERE "packageName" = '@nocobase/plugin-hris-confirmation')
);
'@

# Stage inputs inside the container: snapshot backup script + manifest SQL.
# Normalize CRLF -> LF so the script runs under /bin/sh regardless of checkout EOL.
$TempDir = Join-Path $env:TEMP ("hris-backup-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
try {
    $shFile = Join-Path $TempDir 'hris_backup.sh'
    # 必须显式按 UTF-8 读取：PS5.1 的 Get-Content 对无 BOM 文件按 ANSI/GBK 解码，
    # 中文注释会乱码并破坏行结构（曾把判定行吞进注释导致 MANIFEST-INVALID 误报）。
    # CRLF -> LF 规范化，保证 /bin/sh 可执行（与 checkout EOL 无关）。
    $shRaw = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'backup_snapshot.sh'),
        (New-Object System.Text.UTF8Encoding($false)))
    [IO.File]::WriteAllText($shFile, $shRaw.Replace("`r`n", "`n"),
        (New-Object System.Text.UTF8Encoding($false)))
    $sqlFile = Join-Path $TempDir 'hris_manifest.sql'
    [IO.File]::WriteAllText($sqlFile, $ManifestSql.Replace("`r`n", "`n"),
        (New-Object System.Text.UTF8Encoding($false)))

    docker cp $shFile "${Container}:/tmp/hris_backup.sh"
    if ($LASTEXITCODE -ne 0) { Write-Error "docker cp (script) failed (container=$Container)"; exit $LASTEXITCODE }
    docker cp $sqlFile "${Container}:/tmp/hris_manifest.sql"
    if ($LASTEXITCODE -ne 0) { Write-Error "docker cp (manifest sql) failed"; exit $LASTEXITCODE }

    Write-Host "== Shared-snapshot backup in $Container (dump + manifest, one PG snapshot) =="
    $shOut = docker exec -e PGPASSWORD=$DbPass -e PGUSER=$DbUser -e PGDATABASE=$DbName $Container sh /tmp/hris_backup.sh 2>&1 | Out-String
    Write-Host $shOut.Trim()
    if ($LASTEXITCODE -ne 0 -or $shOut -notmatch 'BACKUP-INCONTAINER-OK') {
        Write-Error "in-container backup failed (container=$Container)"
        exit 1
    }
    $SnapshotId = if ($shOut -match 'SNAPSHOT=(\S+)') { $Matches[1] } else { '' }

    docker cp "${Container}:/tmp/hris.dump" $OutFile
    if ($LASTEXITCODE -ne 0) { Write-Error "docker cp (dump) failed"; exit $LASTEXITCODE }
    $RawManifestFile = Join-Path $TempDir 'hris.manifest.json'
    docker cp "${Container}:/tmp/hris.manifest.json" $RawManifestFile
    if ($LASTEXITCODE -ne 0) { Write-Error "docker cp (manifest) failed"; exit $LASTEXITCODE }
    # 必须在 finally 删除 TempDir 之前读出内容
    $manifestJson = (Get-Content -LiteralPath $RawManifestFile -Raw -Encoding UTF8).Trim()
    if (-not $manifestJson.StartsWith('{')) {
        Write-Error "manifest produced in container is not JSON (container=$Container)"
        exit 1
    }
    docker exec $Container sh -c 'rm -f /tmp/hris_backup.sh /tmp/hris_manifest.sql /tmp/hris.dump /tmp/hris.manifest.json'
    if ($LASTEXITCODE -ne 0) { Write-Error "container temp cleanup failed"; exit $LASTEXITCODE }
} finally {
    Remove-Item -LiteralPath $TempDir -Recurse -Force -ErrorAction SilentlyContinue
}

# Manifest = shared-snapshot SQL result + file metadata (incl. sha256 for restore).
$manifest = $manifestJson | ConvertFrom-Json
$manifest | Add-Member -NotePropertyName dump_file -NotePropertyValue (Split-Path -Leaf $OutFile)
$manifest | Add-Member -NotePropertyName dump_bytes -NotePropertyValue (Get-Item -LiteralPath $OutFile).Length
$manifest | Add-Member -NotePropertyName dump_sha256 -NotePropertyValue (Get-FileHash -LiteralPath $OutFile -Algorithm SHA256).Hash.ToLower()
$manifest | Add-Member -NotePropertyName snapshot_id -NotePropertyValue $SnapshotId
$manifest | Add-Member -NotePropertyName snapshot_mode -NotePropertyValue 'shared: pg_dump --snapshot + SET TRANSACTION SNAPSHOT (REV-011)'
$manifest | Add-Member -NotePropertyName created_at -NotePropertyValue (Get-Date).ToString('o')
$manifest | Add-Member -NotePropertyName source_container -NotePropertyValue $Container
$manifest | Add-Member -NotePropertyName source_db -NotePropertyValue $DbName
$ManifestFile = "$OutFile.manifest.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ManifestFile -Encoding UTF8

Write-Host "Backup written: $OutFile"
Write-Host "Manifest written: $ManifestFile (snapshot=$SnapshotId)"
Write-Host ("Snapshot: tables={0} employees={1} requests={2} userid_uniq={3} guard={4} plugin={5}" -f `
    $manifest.tables.Count, $manifest.key_counts.employees, $manifest.key_counts.confirmation_requests,
    $manifest.userid_unique_index, $manifest.relation_guard_trigger, $manifest.hris_plugin)
Write-Host "Restore with: powershell -NoProfile -File .\scripts\restore.ps1 -BackupFile `"$OutFile`""
