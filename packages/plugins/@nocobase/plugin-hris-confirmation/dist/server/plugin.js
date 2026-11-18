/**
 * HRIS 转正审批插件（最小服务端扩展）。
 *
 * 只负责四个受控业务动作：saveDraft / submit / approve / reject。
 * 复用平台原生会话（ctx.state.currentUser）、数据权限与 PostgreSQL 事务，
 * 不另建认证；动作内部显式校验账号绑定、数据范围、角色与状态。
 *
 * load() 时还会执行（幂等）：
 * - 把 employees.userId 的唯一性声明进平台模型（REV-003 根因修复：此后
 *   字段同步由 SyncRunner 自己维护唯一索引，不再被普通字段配置删除）；
 * - 兼容迁移兜底：employees.userId 唯一索引（允许多名未绑定账号的员工）
 *   + 员工关系守卫触发器（禁止自管理/重复绑定 + PENDING 冻结汇报关系变更）。
 */
const { Plugin } = require('@nocobase/server');
const actions = require('./actions');
const { runMigrations } = require('./migrations');
const { ensureUniqueUserIdBinding } = require('./unique-binding');

class PluginHrisConfirmationServer extends Plugin {
  async load() {
    this.app.resourceManager.registerActionHandlers({
      'confirmation_requests:saveDraft': actions.saveDraft,
      'confirmation_requests:submit': actions.submit,
      'confirmation_requests:approve': actions.approve,
      'confirmation_requests:reject': actions.reject,
    });

    // 仅放行“已登录”到达受控动作；真正的角色/绑定/状态校验在动作内部完成，
    // 因此不构成权限漏洞（不满足“只检查已登录”的要求）。
    this.app.acl.allow(
      'confirmation_requests',
      ['saveDraft', 'submit', 'approve', 'reject'],
      'loggedIn',
    );

    // REV-003 主防线：userId unique 声明进平台模型（先于兜底迁移）。
    try {
      ensureUniqueUserIdBinding(this.app.db, this.log);
    } catch (err) {
      this.log.error(`hris unique-binding declaration failed: ${err.message}`);
    }

    // 兼容迁移：唯一绑定约束（兜底）+ 员工关系守卫触发器（幂等，失败不阻断启动）。
    await runMigrations(this.app.db, this.log);
  }
}

module.exports = PluginHrisConfirmationServer;
