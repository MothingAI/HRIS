/**
 * @nocobase/plugin-hris-confirmation 客户端入口（AMD/UMD）。
 *
 * NocoBase 2.x 插件管理器对每个已启用插件都会经 RequireJS 按包名加载
 * `dist/client/index.js`（见 @nocobase/server plugin-manager 的
 * PLUGIN_CLIENT_ENTRY_FILES 与 pm:listEnabled），缺少该文件会以 404 阻断
 * 页面启动（REV-009）。本插件目前没有前端业务逻辑，因此按平台自带
 * plugin-ui-layout 的最小合法形态提供：继承 @nocobase/client 的 Plugin
 * 基类的空生命周期插件（基类已提供空的 afterAdd / beforeLoad / load）。
 *
 * 受控动作（saveDraft/submit/approve/reject）全部在后端
 * dist/server/actions.js 实现；对应 UI 按钮接线见 scripts/build_ui.py。
 */
(function (root, factory) {
  if (typeof exports === 'object' && typeof module === 'object') {
    // CommonJS（仅元数据用途；浏览器不走此分支）
    module.exports = factory(require('@nocobase/client'));
  } else if (typeof define === 'function' && define.amd) {
    // AMD：NocoBase 前端经 RequireJS 加载，模块名必须等于包名
    define('@nocobase/plugin-hris-confirmation', ['@nocobase/client'], factory);
  } else if (typeof exports === 'object') {
    exports['@nocobase/plugin-hris-confirmation'] = factory(require('@nocobase/client'));
  } else {
    root['@nocobase/plugin-hris-confirmation'] = factory(root['@nocobase/client']);
  }
})(typeof self !== 'undefined' ? self : this, function (client) {
  'use strict';

  var Plugin = client.Plugin;

  // 空生命周期插件：PluginManager.add 会以 new Component(options, app)
  // 实例化并依次调用 afterAdd() / beforeLoad() / load()，全部继承基类空实现。
  class PluginHrisConfirmationClient extends Plugin {}

  var exports = {};
  Object.defineProperty(exports, '__esModule', { value: true });
  Object.defineProperty(exports, 'default', {
    enumerable: true,
    get: function () {
      return PluginHrisConfirmationClient;
    },
  });
  return exports;
});
