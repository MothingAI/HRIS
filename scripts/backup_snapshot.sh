#!/bin/sh
# REV-011: pg_dump 与业务清单共享同一个导出快照，消除"dump 用快照 A、
# 清单 SQL 用时刻 B"的间隙——备份期间正常发生的员工新增/审批不再导致
# dump 与清单计数不一致（那会让完全有效的备份在恢复校验时误判失败）。
#
# 流程（全部在数据库容器内执行）：
#   1. 后台 psql 会话开启 REPEATABLE READ 事务并 pg_export_snapshot()，
#      用 fifo 保持会话打开（快照存活期管理）；
#   2. pg_dump --snapshot=<id>（与导出快照完全同一数据视图）；
#   3. 清单 SQL 经 SET TRANSACTION SNAPSHOT '<id>' 在同一视图上执行；
#   4. 释放后台会话，输出 /tmp/hris.dump 与 /tmp/hris.manifest.json。
#
# 由 backup.ps1 调用（docker cp 本脚本与清单 SQL 进容器后 docker exec）。
# 环境变量：PGUSER / PGDATABASE / PGPASSWORD（由 docker exec -e 注入）。
set -e

: "${PGUSER:?PGUSER required}"
: "${PGDATABASE:?PGDATABASE required}"

DUMP=/tmp/hris.dump
MANIFEST=/tmp/hris.manifest.json
SQL=/tmp/hris_manifest.sql
FIFO=/tmp/hris_hold.fifo
SNAPFILE=/tmp/hris_snap.txt

# 只清上次可能残留的输出与辅助文件；不清 $SQL（本脚本的外部输入）
rm -f "$DUMP" "$MANIFEST" "$FIFO" "$SNAPFILE"
[ -f "$SQL" ] || { echo "MANIFEST-SQL-MISSING"; exit 1; }

release_holder() {
    echo COMMIT > "$FIFO" 2>/dev/null || true
    wait "$HOLDER" 2>/dev/null || true
    rm -f "$FIFO" "$SNAPFILE"
}

mkfifo "$FIFO"
# -q 抑制 BEGIN/COMMIT 命令标签，保证 SNAPFILE 首行就是快照 ID
( echo "BEGIN ISOLATION LEVEL REPEATABLE READ;"; \
  echo "SELECT pg_export_snapshot();"; \
  cat "$FIFO" ) | psql -q -U "$PGUSER" -d "$PGDATABASE" -t -A > "$SNAPFILE" &
HOLDER=$!

i=0
while [ ! -s "$SNAPFILE" ] && [ "$i" -lt 100 ]; do sleep 0.1; i=$((i+1)); done
SNAP=$(grep -E '^[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+$' "$SNAPFILE" | head -n 1)
if [ -z "$SNAP" ]; then
    echo "SNAPSHOT-EXPORT-FAILED"
    release_holder
    exit 1
fi
echo "SNAPSHOT=$SNAP"

pg_dump -U "$PGUSER" -d "$PGDATABASE" -Fc --snapshot="$SNAP" -f "$DUMP"

{
    echo "BEGIN ISOLATION LEVEL REPEATABLE READ;"
    echo "SET TRANSACTION SNAPSHOT '$SNAP';"
    cat "$SQL"
    echo "COMMIT;"
} | psql -q -U "$PGUSER" -d "$PGDATABASE" -t -A > "$MANIFEST"

release_holder

# 清单必须是合法 JSON 对象（单行 -t -A 输出）
if ! head -n 1 "$MANIFEST" | grep -q '^{'; then
    echo "MANIFEST-INVALID"
    exit 1
fi
echo "BACKUP-INCONTAINER-OK bytes=$(wc -c < "$DUMP") manifest_bytes=$(wc -c < "$MANIFEST")"
