/**
 * REV-003 根因修复：把 employees."userId" 的唯一性声明进平台模型。
 *
 * 根因（NocoBase 2.2.37 实测 + database/lib/sync-runner.js 源码）：
 * 每次字段同步（fields:create / fields:apply / 集合变更触发）末尾的
 * SyncRunner.handleUniqueIndex 会删除“模型属性未声明 unique”的单列唯一索引，
 * 并为声明了 unique 的属性补建 `<表>_<列>_uk` 索引。插件迁移用 SQL 建的
 * employees_user_id_key 不在模型声明里，所以任何一次普通字段配置都会把它删掉
 * ——靠“启动重建 + 脚本补建”只能覆盖两条路径（REV-003 遗留问题）。
 *
 * 平台自身持续维护该约束的唯一受支持路径：让模型属性本身带 unique:true。
 * belongsTo 字段 options 里的 unique 不会被平台应用到 FK 属性
 * （BelongsToField.bind 只调 addIndex([foreignKey]) 建普通索引），因此由本
 * 模块把声明补进模型：
 *   collection.model.rawAttributes.userId.unique = true; refreshAttributes()
 *
 * 挂载时机（实测 2.2.37 的两个坑）：
 * - 插件 load() 时业务集合往往尚未定义（数据源后加载）；
 * - afterDefineCollection 事件触发时集合尚未注册进 db.getCollection，且
 *   字段还未绑定（rawAttributes.userId 尚不存在）。
 * 因此在 afterDefineCollection 时向模型挂 sequelize beforeSync 钩子：任何
 * 同步执行前先把 unique 声明补进属性（此时字段早已绑定）。声明一旦生效即
 * 稳定——sequelize 6.37 的 belongsTo 重绑走 mergeDefaults（既有属性优先），
 * 不会被后续关系绑定覆盖。此后任何字段同步都会保留/补建 userId 唯一索引
 * （SyncRunner 自己创建维护，无重启、无脚本补建）。
 *
 * 若库中已存在重复绑定：同步补建索引会失败（该次字段操作报错回滚），
 * 关系守卫触发器同时拒绝新的重复绑定（见 migrations.js），受控动作对
 * 多重绑定返回 409——异常状态不会被静默当作正常可用。
 */

const HOOK_FLAG = '__hrisUniqueBindingHook';

function applyUniqueToCollection(collection, log, where) {
  const model = collection && collection.model;
  if (!model || !model.rawAttributes) {
    return false;
  }
  const attr = model.rawAttributes.userId;
  if (!attr) {
    return false;
  }
  if (attr.unique !== true) {
    attr.unique = true;
    model.refreshAttributes();
    log.info(`hris unique-binding: declared employees.userId unique in platform model (${where})`);
  }
  return true;
}

function attach(db, log, collection, where) {
  const model = collection && collection.model;
  if (!model) {
    return;
  }
  // 集合被重新定义（新模型实例）时会再次走到这里；同一实例只挂一次钩子。
  if (!model[HOOK_FLAG]) {
    model[HOOK_FLAG] = true;
    model.addHook('beforeSync', async () => {
      // 字段此时必然已绑定；幂等补声明，确保 handleUniqueIndex 看得到。
      applyUniqueToCollection(collection, log, 'beforeSync');
    });
    log.info(`hris unique-binding: beforeSync hook attached (${where})`);
  }
  // 属性已就绪则立即声明（不等同步）。
  applyUniqueToCollection(collection, log, where);
}

/**
 * @param {import('@nocobase/database').Database} db
 * @param {any} log
 */
function ensureUniqueUserIdBinding(db, log) {
  // 集合已定义（插件晚于数据源加载时）立即挂载；
  const existing = db.getCollection('employees');
  if (existing) {
    attach(db, log, existing, 'plugin-load');
  }
  // 集合尚未定义或之后被重新定义时，等 employees 定义后挂载（幂等）。
  // 注意：事件触发时集合可能尚未注册进 db.getCollection，直接用事件传入的
  // collection 对象，不回查。
  db.on('afterDefineCollection', (collection) => {
    if (collection && collection.name === 'employees') {
      attach(db, log, collection, 'afterDefineCollection');
    }
  });
}

module.exports = { ensureUniqueUserIdBinding, applyUniqueToCollection };
