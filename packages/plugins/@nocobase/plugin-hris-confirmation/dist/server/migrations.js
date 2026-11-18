/**
 * HRIS 兼容迁移（幂等，应用每次启动在插件 load() 时执行）。
 *
 * 只做“加约束、加守卫”，不删除、不修改任何用户业务数据：
 * 1) employees.userId 唯一索引（PostgreSQL 唯一索引天然允许多个 NULL，
 *    即允许多名未绑定账号的员工）；
 * 2) 员工关系守卫触发器：禁止自己管理自己；禁止把账号绑定到已绑定其他
 *    员工的 user（可读报错，兜住索引缺失窗口；并发竞争仍由唯一索引兜底）；
 *    存在 PENDING 转正申请时冻结 managerId / userId 变更（覆盖普通 CRUD、
 *    批量更新与关系写入，因为它们最终都落在 employees 行的写上）。
 *
 * REV-003 根因修复的主防线在 unique-binding.js：把 userId 唯一性声明进
 * 平台模型属性，字段同步（SyncRunner.handleUniqueIndex）自己维护该索引，
 * 不再依赖“启动重建 + 脚本补建”。本迁移的索引重建降级为兜底（覆盖
 * 恢复旧备份/直连改库等应用外路径）。唯一索引建立前先检测重复绑定；
 * 发现重复时只报告、不建索引、不删数据，由实施方人工处理——同时触发器
 * 阻止产生新的重复绑定、受控动作对多重绑定返回 409，异常状态不会被
 * 静默当作正常可用。
 */

async function tableExists(db, table) {
  const [rows] = await db.sequelize.query(
    `SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = '${table}'`,
  );
  return Array.isArray(rows) && rows.length > 0;
}

const RELATION_GUARD_SQL = `
CREATE OR REPLACE FUNCTION hris_employee_relation_guard() RETURNS trigger AS $$
BEGIN
  IF NEW."managerId" IS NOT NULL AND NEW."managerId" = NEW."id" THEN
    RAISE EXCEPTION 'HRIS: employee cannot manage self (id=%)', NEW."id";
  END IF;
  IF NEW."userId" IS NOT NULL
     AND (TG_OP = 'INSERT' OR NEW."userId" IS DISTINCT FROM OLD."userId")
     AND EXISTS (SELECT 1 FROM employees e WHERE e."userId" = NEW."userId" AND e.id <> NEW."id") THEN
    RAISE EXCEPTION 'HRIS: user already bound to another employee (userId=%)', NEW."userId";
  END IF;
  IF (TG_OP = 'UPDATE') AND
     (NEW."managerId" IS DISTINCT FROM OLD."managerId" OR NEW."userId" IS DISTINCT FROM OLD."userId") AND
     EXISTS (SELECT 1 FROM confirmation_requests WHERE "employeeId" = NEW."id" AND status = 'PENDING') THEN
    RAISE EXCEPTION 'HRIS: pending confirmation request freezes relationship change (employeeId=%)', NEW."id";
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
`;

/**
 * 执行迁移。返回结果对象供日志记录（不抛错，避免影响应用启动）。
 * @param {import('@nocobase/database').Database} db
 * @param {any} log
 */
async function runMigrations(db, log) {
  const result = { skipped: false, duplicateUserIds: [], index: 'ok', trigger: 'ok' };

  if (!db || !db.sequelize) {
    log.warn('hris migration skipped: database not ready');
    result.skipped = true;
    return result;
  }

  try {
    const hasEmployees = await tableExists(db, 'employees');
    const hasRequests = await tableExists(db, 'confirmation_requests');
    if (!hasEmployees || !hasRequests) {
      log.info('hris migration skipped: business collections not ready yet');
      result.skipped = true;
      return result;
    }

    // 1) 账号绑定唯一约束（兜底）：主防线是 unique-binding.js 把 userId
    //    unique 声明进平台模型，字段同步会自己维护索引；这里的幂等重建
    //    覆盖应用外路径（恢复不含该索引的旧备份、直连改库等）。先检测
    //    重复，重复则报告、不建索引、不删数据（触发器已阻止新增重复，
    //    存量重复由实施方人工处理）。不 DROP 平台的 FK 普通索引
    //    employees_user_id（同步总会重建它，互删无意义）。
    const [dups] = await db.sequelize.query(
      `SELECT "userId", count(*) AS n FROM employees WHERE "userId" IS NOT NULL
       GROUP BY "userId" HAVING count(*) > 1`,
    );
    if (Array.isArray(dups) && dups.length > 0) {
      result.duplicateUserIds = dups.map((d) => d.userId);
      result.index = 'skipped-duplicates';
      log.warn(
        `hris migration: duplicate userId bindings detected (${result.duplicateUserIds.join(', ')}); ` +
        'unique index NOT created, no data deleted',
      );
    } else {
      const [uniqIdx] = await db.sequelize.query(
        `SELECT 1 FROM pg_index x
         JOIN pg_class c ON c.oid = x.indexrelid
         JOIN pg_class t ON t.oid = x.indrelid
         WHERE t.relname = 'employees' AND c.relname LIKE '%user%' AND x.indisunique LIMIT 1`,
      );
      if (Array.isArray(uniqIdx) && uniqIdx.length > 0) {
        result.index = 'ok-exists';
        log.info('hris migration: unique index on employees.userId already present');
      } else {
        await db.sequelize.query(
          'CREATE UNIQUE INDEX IF NOT EXISTS employees_user_id_key ON employees ("userId")',
        );
        log.info('hris migration: ensured unique index on employees.userId');
      }
    }

    // 2) 员工关系守卫触发器（幂等）。
    await db.sequelize.query(RELATION_GUARD_SQL);
    await db.sequelize.query(
      'DROP TRIGGER IF EXISTS hris_employee_relation_guard ON employees',
    );
    await db.sequelize.query(
      'CREATE TRIGGER hris_employee_relation_guard BEFORE INSERT OR UPDATE ON employees ' +
      'FOR EACH ROW EXECUTE FUNCTION hris_employee_relation_guard()',
    );
    log.info('hris migration: ensured employee relation guard trigger');
  } catch (err) {
    result.skipped = true;
    result.error = err.message;
    log.error(`hris migration failed: ${err.message}`);
  }

  return result;
}

module.exports = { runMigrations, RELATION_GUARD_SQL };
