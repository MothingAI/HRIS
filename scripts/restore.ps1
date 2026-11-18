# Restore a backup into an ISOLATED fresh postgres volume, verify, then clean up.
# Does NOT touch the running instance. Use -Keep to leave the container for inspection.
#
# Safety (REV-006 / REV-008):
# - Each run creates UNIQUE container/volume names (run-id suffix) tagged with ownership
#   labels, so a later run never deletes a previous -Keep copy.
# - Only resources created by THIS run are removed (and only when not -Keep).
# - The postgres image is pinned to an exact digest (PostgreSQL 16.3).
#
# Verification (REV-011): relative to a backup-time manifest, not fixed counts.
# - If <dump>.manifest.json exists (backup.ps1 >= REV-011 emits it): recompute the same
#   manifest SQL on the restored DB and require tables / key-table counts / status
#   distributions / unique-index / guard-trigger / plugin to MATCH the backup snapshot.
#   (A backup taken after approvals and HR/tool-added employees restores to exactly
#   that state — no hardcoded E002=PROBATION or fixed totals.)
# - Structural invariants are ALWAYS enforced, manifest or not: core tables exist,
#   employees."userId" unique index present (matched by column, name-agnostic),
#   relation-guard trigger present, hris plugin registered exactly once.
#   A corrupted / missing-table / lost-constraint backup therefore fails here.
# - Old backups without a manifest: structural checks only + an explicit WARNING.
#
# Usage: powershell -NoProfile -File .\scripts\restore.ps1 [-BackupFile <path>] [-Keep] [-Manifest <path>]
param(
    [string]$BackupFile = '',
    [switch]$Keep,
    [string]$Manifest = ''
)
# 显式指定 -Manifest 时文件必须存在（防拼错路径后静默降级为"无清单"弱校验）
$ManifestExplicit = [bool]$Manifest
$ErrorActionPreference = 'Stop'

$RootDir = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $RootDir '.env'
$BackupDir = Join-Path $RootDir 'backups'

# Unique names so a previous -Keep copy is never clobbered.
$RunId = (Get-Date -Format 'yyyyMMddHHmmss') + '-' + ([guid]::NewGuid().ToString('N').Substring(0, 8))
$Container = "hris-restore-db-$RunId"
$Volume = "hris_restore_dbdata_$RunId"
$Image = 'registry.cn-shanghai.aliyuncs.com/nocobase/postgres:16@sha256:dc0dc485abfdd0901b10af592ccc3c25ed821936a962969b99d3b488e24edd39'

if (-not $BackupFile) {
    $BackupFile = Get-ChildItem -Path $BackupDir -Filter 'hris-backup-*.dump' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime | Select-Object -Last 1 -ExpandProperty FullName
}
if (-not $BackupFile -or -not (Test-Path -LiteralPath $BackupFile)) {
    Write-Error "No backup file found. Run backup.ps1 first, or pass -BackupFile."
    exit 1
}
if (-not $Manifest) { $Manifest = "$BackupFile.manifest.json" }
if ($ManifestExplicit -and -not (Test-Path -LiteralPath $Manifest)) {
    Write-Error "Manifest not found: $Manifest (explicitly specified; refusing to fall back to no-manifest mode)"
    exit 1
}
$hasManifest = (Test-Path -LiteralPath $Manifest)
$m = $null
if ($hasManifest) {
    Write-Host "Manifest: $Manifest"
    $m = Get-Content -LiteralPath $Manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    # REV-011: pg_restore 之前先校验 dump 完整性（截断/篡改在这里提前失败，
    # 不等到恢复中途才报错）。清单无 sha256 的旧备份跳过本检查。
    if ($m.PSObject.Properties['dump_sha256'] -and $m.dump_sha256) {
        $actual = (Get-FileHash -LiteralPath $BackupFile -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne $m.dump_sha256) {
            Write-Error "dump sha256 mismatch: manifest=$($m.dump_sha256) actual=$actual (truncated or tampered?)"
            exit 1
        }
        Write-Host "dump sha256 verified against manifest."
    } else {
        Write-Warning "Manifest has no dump_sha256 (pre-shared-snapshot backup?); file integrity NOT pre-checked."
    }
} else {
    Write-Warning "No manifest next to backup (pre-REV-011 backup?). Structural checks only; business-content equality is NOT verified."
}

# Read .env for DB credentials.
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

# Verification runs as TWO probes so a damaged backup gets a precise failure:
# - Probe 1 (catalog only — never references business tables): public table list,
#   employees."userId" unique index (by column), relation-guard trigger.
# - Probe 2 (business; only after core tables confirmed present): key-table counts,
#   status distributions, plugin registration. Same shape as backup.ps1's manifest.
$Probe1Sql = @'
SELECT json_build_object(
  'tables', (SELECT coalesce(jsonb_agg(tablename ORDER BY tablename), '[]'::jsonb)
             FROM pg_tables WHERE schemaname = 'public'),
  'userid_unique_index', (SELECT count(*) FROM pg_indexes
                          WHERE tablename = 'employees'
                            AND indexdef LIKE 'CREATE UNIQUE%("userId")%'),
  'relation_guard_trigger', (SELECT count(*) FROM information_schema.triggers
                             WHERE event_object_table = 'employees'
                               AND trigger_name = 'hris_employee_relation_guard')
);
'@
$Probe2Sql = @'
SELECT json_build_object(
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
  'hris_plugin', (SELECT count(*) FROM "applicationPlugins"
                  WHERE "packageName" = '@nocobase/plugin-hris-confirmation')
);
'@

$CORE_TABLES = @('employees', 'departments', 'confirmation_requests', 'approval_actions',
                 'users', 'roles', 'collections', 'fields', 'applicationPlugins')

$createdContainer = $false
$createdVolume = $false

try {
    Write-Host "== Create isolated volume $Volume (ownership label run=$RunId) =="
    docker volume create --label "hris.restore.run=$RunId" $Volume | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Error "docker volume create failed"; exit $LASTEXITCODE }
    $createdVolume = $true

    Write-Host "== Start isolated postgres $Container =="
    docker run -d --name $Container --label "hris.restore.run=$RunId" `
        -e POSTGRES_USER=$DbUser -e POSTGRES_PASSWORD=$DbPass -e POSTGRES_DB=$DbName `
        -v "${Volume}:/var/lib/postgresql/data" $Image
    if ($LASTEXITCODE -ne 0) { Write-Error "docker run failed"; exit $LASTEXITCODE }
    $createdContainer = $true

    Write-Host "== Wait for postgres =="
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        docker exec -e PGPASSWORD=$DbPass $Container pg_isready -U $DbUser -d $DbName | Out-Null
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) { Write-Error "postgres not ready"; exit 1 }

    Write-Host "== Restore backup =="
    docker cp $BackupFile "${Container}:/tmp/hris.dump"
    if ($LASTEXITCODE -ne 0) { Write-Error "docker cp failed"; exit $LASTEXITCODE }
    docker exec -e PGPASSWORD=$DbPass $Container pg_restore -U $DbUser -d $DbName --no-owner --exit-on-error /tmp/hris.dump
    if ($LASTEXITCODE -ne 0) { Write-Error "pg_restore failed (backup corrupted?)"; exit $LASTEXITCODE }

    Write-Host "== Probe 1: catalog structure (tables / unique index / guard trigger) =="
    $probe1Json = ($Probe1Sql | docker exec -i -e PGPASSWORD=$DbPass $Container psql -U $DbUser -d $DbName -t -A | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $probe1Json.StartsWith('{')) {
        Write-Error "catalog probe failed on restored DB (backup damaged?)"
        exit 1
    }
    $p1 = $probe1Json | ConvertFrom-Json
    $restoredTables = @($p1.tables)

    $failures = @()

    # ---- Structural invariants (always, manifest or not) ----
    foreach ($t in $CORE_TABLES) {
        if ($restoredTables -notcontains $t) { $failures += "core table missing: $t" }
    }
    if ([int]$p1.userid_unique_index -lt 1) {
        $failures += "employees.`"userId`" unique index missing (lost constraint)"
    }
    if ([int]$p1.relation_guard_trigger -lt 1) {
        $failures += "hris_employee_relation_guard trigger missing on employees"
    }

    # ---- Business probe (only meaningful when core tables exist) ----
    $r = $null
    if (-not $failures) {
        Write-Host "== Probe 2: business counts / distributions / plugin =="
        $probe2Json = ($Probe2Sql | docker exec -i -e PGPASSWORD=$DbPass $Container psql -U $DbUser -d $DbName -t -A | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $probe2Json.StartsWith('{')) {
            Write-Error "business probe failed on restored DB (backup damaged?)"
            exit 1
        }
        $r = $probe2Json | ConvertFrom-Json
        if ([int]$r.hris_plugin -ne 1) {
            $failures += "hris-confirmation plugin registrations = $($r.hris_plugin), expected 1"
        }
    }

    # ---- Manifest comparison (backup-time snapshot vs restored DB) ----
    if ($hasManifest) {
        $manifestTables = @($m.tables)
        if ($manifestTables.Count -ne $restoredTables.Count) {
            $failures += "table count: backup=$($manifestTables.Count) restored=$($restoredTables.Count)"
        }
        $diffTables = Compare-Object $manifestTables $restoredTables
        if ($diffTables) {
            $failures += ("table set differs: " + (($diffTables | ForEach-Object { "$($_.SideIndicator) $($_.InputObject)" }) -join ', '))
        }
        if ($r) {
            foreach ($p in $m.key_counts.PSObject.Properties) {
                $name = $p.Name; $expected = [int]$p.Value; $actual = [int]$r.key_counts.$name
                if ($expected -ne $actual) { $failures += "key count $name : backup=$expected restored=$actual" }
            }
            foreach ($dist in @('employees_by_status', 'requests_by_status')) {
                $mDist = $m.$dist; $rDist = $r.$dist
                foreach ($p in $mDist.PSObject.Properties) {
                    $expected = [int]$p.Value; $actual = [int]$rDist.($p.Name)
                    if ($expected -ne $actual) { $failures += "$dist $($p.Name): backup=$expected restored=$actual" }
                }
                foreach ($p in $rDist.PSObject.Properties) {
                    if ($null -eq $mDist.($p.Name)) { $failures += "$dist has extra status $($p.Name)= $($p.Value) not in backup" }
                }
            }
        }
        if ([int]$m.userid_unique_index -ne [int]$p1.userid_unique_index) {
            $failures += "userid_unique_index: backup=$($m.userid_unique_index) restored=$($p1.userid_unique_index)"
        }
        Write-Host ("Restored snapshot: tables={0} employees={1} requests={2} userid_uniq={3} guard={4} plugin={5}" -f `
            $restoredTables.Count, $(if ($r) { $r.key_counts.employees } else { '?' }),
            $(if ($r) { $r.key_counts.confirmation_requests } else { '?' }),
            $p1.userid_unique_index, $p1.relation_guard_trigger, $(if ($r) { $r.hris_plugin } else { '?' }))
    }

    if ($failures.Count -gt 0) {
        Write-Error ("Restore verification FAILED: " + ($failures -join '; '))
        exit 1
    }

    Write-Host "Restore verified against backup-time manifest + structural invariants (isolated; running instance untouched)."
} finally {
    if ($Keep) {
        Write-Host "Keeping isolated instance: docker exec -it $Container psql -U $DbUser -d $DbName"
    } else {
        Write-Host "Cleaning up THIS run's isolated container + volume..."
        $ErrorActionPreference = 'Continue'
        if ($createdContainer) { docker rm -f $Container 2>$null | Out-Null }
        if ($createdVolume)    { docker volume rm $Volume 2>$null | Out-Null }
        $ErrorActionPreference = 'Stop'
    }
}
