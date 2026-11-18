#!/usr/bin/env node
/**
 * REV-009 验证：dist/client/index.js 能被真实 RequireJS 加载并实例化。
 *
 * 复刻 NocoBase 2.2.37 前端（@nocobase/app dist/client/assets/index-*.js）的
 * 插件加载语义：
 *   1) requirejs.config({ paths: { [packageName]: url } }) 后
 *      requirejs([packageName], cb, errback) 按包名加载 dist/client/index.js；
 *   2) 模块必须走 AMD 分支 define('<包名>', ['@nocobase/client'], factory)；
 *   3) 成功回调取 (mod && mod.default) || mod 作为插件组件，无 default 视为
 *      加载错误（"[nocobase load plugin error]: ... no `export.default`"）；
 *   4) PluginManager.add: new Component(pluginData, app) 后 await afterAdd()，
 *      随后 beforeLoad() / load()。客户端 Plugin 基类构造签名为
 *      constructor(options, app)。
 *
 * 本脚本在 Node 的 vm 沙箱中执行「浏览器真实拿到的字节」（先经 HTTP 拉取），
 * 沙箱内只提供 define（来自真实 requirejs 库），不提供 module/exports，
 * 强制走 AMD 分支；@nocobase/client 用同形状的桩模块预注册（页面中该模块
 * 由主 bundle 注册，plugin-ui-layout 等官方插件同样依赖它）。
 *
 * 用法：
 *   node scripts/verify_client_plugin.js            # 默认 http://127.0.0.1:13000
 *   NB_BASE=http://127.0.0.1:13001 node scripts/verify_client_plugin.js
 * 依赖：.tools/amd-check/node_modules/requirejs（npmmirror 安装的原生库）。
 */
'use strict';

const path = require('path');
const vm = require('vm');

const requirejs = require(
  path.join(__dirname, '..', '.tools', 'amd-check', 'node_modules', 'requirejs'),
);

const BASE = (process.env.NB_BASE || 'http://127.0.0.1:13000').replace(/\/+$/, '');
const PKG = '@nocobase/plugin-hris-confirmation';
const URL = `${BASE}/static/plugins/${PKG}/dist/client/index.js`;

let pass = 0;
let fail = 0;

function check(name, ok, detail = '') {
  const tag = ok ? 'PASS' : 'FAIL';
  console.log(`[${tag}] ${name}${detail ? ` :: ${detail}` : ''}`);
  ok ? pass++ : fail++;
}

async function main() {
  // ---- 1) HTTP：浏览器拿到的就是这些字节 --------------------------------
  const res = await fetch(URL);
  const body = await res.text();
  check('HTTP 200', res.status === 200, `status=${res.status}`);
  check('Content-Type 为 JavaScript', /javascript/i.test(res.headers.get('content-type') || ''),
    res.headers.get('content-type') || '(none)');
  check('非 HTML/空文件', body.length > 100 && !/^\s*<!doctype/i.test(body), `${body.length} bytes`);

  // ---- 2) 沙箱内执行 AMD 分支（无 module/exports，仅 define） -----------
  if (typeof requirejs.define !== 'function' || !requirejs.define.amd) {
    throw new Error('requirejs 库未暴露 AMD define（.tools/amd-check 未就绪？）');
  }

  // 与页面一致：先注册 @nocobase/client，再执行插件脚本（命名 define 立即注册，
  // 不触发脚本加载）。桩 Plugin 基类与 @nocobase/client 真实基类同形：
  // constructor(options, app) + 空异步 afterAdd/beforeLoad/load。
  class StubClientPlugin {
    constructor(options, app) { this.options = options; this.app = app; }
    async afterAdd() {}
    async beforeLoad() {}
    async load() {}
  }
  requirejs.define('@nocobase/client', [], function () {
    return { Plugin: StubClientPlugin };
  });

  const sandbox = { define: requirejs.define, console };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(body, sandbox, { filename: URL });

  // ---- 3) 真实 requirejs 解析依赖并实例化 ------------------------------
  const mod = await new Promise((resolve, reject) => {
    requirejs(
      [PKG],
      (m) => resolve(m),
      (err) => reject(err),
    );
  });

  const Ctor = (mod && mod.default) || mod;
  check('模块已加载且有 default 导出', typeof Ctor === 'function',
    `typeof default = ${typeof Ctor}`);

  const pluginData = { name: 'hrisConfirmation', packageName: PKG, options: {} };
  const fakeApp = { marker: 'hris-fake-app' };
  const inst = new Ctor(pluginData, fakeApp); // PluginManager.add 的实例化方式
  check('new Component(pluginData, app) 成功', inst instanceof StubClientPlugin);
  check('options 正确注入', inst.options === pluginData);
  check('app 正确注入', inst.app === fakeApp);
  await inst.afterAdd();
  await inst.beforeLoad();
  await inst.load();
  check('afterAdd / beforeLoad / load 生命周期可调用', true);

  console.log(`== verify_client_plugin: ${pass} passed, ${fail} failed ==`);
  process.exit(fail ? 1 : 0);
}

main().catch((err) => {
  console.error(`[ERROR] ${err && err.stack ? err.stack.split('\n')[0] : err}`);
  process.exit(1);
});
