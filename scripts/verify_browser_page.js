#!/usr/bin/env node
/**
 * REV-009 浏览器验收：真实 Chrome（CDP 无头）登录后页面不再因插件加载报错。
 *
 * 步骤（全部走真实浏览器）：
 *   1) 启动 Chrome --headless=new --remote-debugging-port，打开 /signin；
 *   2) 页面内 fetch /api/auth:signIn 拿 token，写入 localStorage
 *      NOCOBASE_TOKEN（NocoBase 2.x 客户端 APIClient 的存储键）；
 *   3) 导航 /admin，等待渲染；采集全部 console error / 未捕获异常 /
 *      网络失败，并核对 @nocobase/plugin-hris-confirmation 的 dist/client
 *      请求返回 200；
 *   4) 在页面里探测 RequireJS 注册表，确认模块已加载且有 default 导出。
 *
 * 用法（从仓库根目录）：
 *   node scripts/verify_browser_page.js                     # 默认 employee.a
 *   NB_EMAIL=hr@example.com node scripts/verify_browser_page.js
 * 环境变量：NB_BASE（默认 http://127.0.0.1:13000）、NB_EMAIL、NB_PASSWORD、
 *   CHROME_PATH（默认 Chrome，缺省回退 Edge）。
 */
'use strict';

const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

// ---- 配置 -----------------------------------------------------------------

function loadEnv() {
  const env = {};
  const envFile = path.join(__dirname, '..', '.env');
  if (fs.existsSync(envFile)) {
    for (const line of fs.readFileSync(envFile, 'utf8').split(/\r?\n/)) {
      const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
      if (m) env[m[1]] = m[2];
    }
  }
  return { ...env, ...Object.fromEntries(Object.entries(process.env).filter(([, v]) => v)) };
}

const ENV = loadEnv();
const BASE = (ENV.NB_BASE || 'http://127.0.0.1:13000').replace(/\/+$/, '');
const EMAIL = ENV.NB_EMAIL || 'employee.a@example.com';
const PASSWORD = ENV.NB_PASSWORD || ENV.DEMO_PASSWORD;
const CHROME = ENV.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const CDP_PORT = Number(ENV.CDP_PORT || 9223);
const PKG = '@nocobase/plugin-hris-confirmation';

let pass = 0;
let fail = 0;
const check = (name, ok, detail = '') => {
  console.log(`[${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ` :: ${detail}` : ''}`);
  ok ? pass++ : fail++;
};

// ---- CDP 客户端 -----------------------------------------------------------

class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.listeners = [];
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
      } else if (msg.method) {
        for (const l of this.listeners) l(msg);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  on(listener) { this.listeners.push(listener); }
  async close() { this.ws.close(); }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(fn, timeoutMs, label) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const v = await fn();
    if (v) return v;
    await sleep(300);
  }
  throw new Error(`timeout waiting for ${label}`);
}

// ---- 主流程 ---------------------------------------------------------------

async function main() {
  if (!PASSWORD) throw new Error('需要 NB_PASSWORD / DEMO_PASSWORD');
  const browser = fs.existsSync(CHROME) ? CHROME : (fs.existsSync(EDGE) ? EDGE : null);
  if (!browser) throw new Error('未找到 Chrome/Edge');
  console.log(`browser: ${browser}`);
  console.log(`target  : ${BASE}  account: ${EMAIL}`);

  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'hris-cdp-'));
  const proc = spawn(browser, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${userDataDir}`,
    `${BASE}/signin`, '--window-size=1440,900',
  ], { stdio: 'ignore' });
  try {
    const version = await waitFor(async () => {
      try { return (await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/version`)).json()); }
      catch { return null; }
    }, 15000, 'CDP endpoint');
    check('Chrome 无头实例 + CDP 就绪', true, version.Browser.split(' ').slice(0, 2).join(' '));

    // 找到页面 target（初始 URL 即 /signin）
    const pages = await waitFor(async () => {
      const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
      return list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl) || null;
    }, 15000, 'page target');

    const ws = new WebSocket(pages.webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      ws.addEventListener('open', res);
      ws.addEventListener('error', () => rej(new Error('websocket error')));
    });
    const cdp = new Cdp(ws);

    // 事件采集
    const consoleErrors = [];   // {text, url}
    const pageErrors = [];      // 未捕获异常
    const netFailed = [];       // 网络失败
    const pluginResponses = []; // 我们插件 URL 的响应码
    let loadEventCount = 0;

    cdp.on((msg) => {
      const p = msg.params || {};
      if (msg.method === 'Runtime.exceptionThrown') {
        pageErrors.push(p.exceptionDetails?.exception?.description || p.exceptionDetails?.text || '?');
      } else if (msg.method === 'Runtime.consoleAPICalled' && ['error', 'assert'].includes(p.type)) {
        consoleErrors.push((p.args || []).map((a) => a.value ?? a.description ?? a.type).join(' '));
      } else if (msg.method === 'Log.entryAdded' && p.entry?.level === 'error') {
        consoleErrors.push(`${p.entry.text} ${p.entry.url || ''}`);
      } else if (msg.method === 'Network.loadingFailed') {
        netFailed.push(`${p.errorText} ${p.blockedReason || ''}`);
      } else if (msg.method === 'Network.responseReceived') {
        const u = p.response?.url || '';
        if (u.includes(`${PKG}/dist/client/index.js`)) pluginResponses.push(p.response.status);
      } else if (msg.method === 'Page.loadEventFired') {
        loadEventCount++;
      }
    });
    await cdp.send('Runtime.enable');
    await cdp.send('Page.enable');
    await cdp.send('Log.enable');
    await cdp.send('Network.enable');
    await waitFor(() => loadEventCount >= 1 ? true : null, 20000, 'signin page load');

    // 登录（页面同源 fetch），token 写入客户端实际读取的 localStorage 键
    const login = await cdp.send('Runtime.evaluate', {
      awaitPromise: true, returnByValue: true,
      expression: `(async () => {
        const r = await fetch('${BASE}/api/auth:signIn', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: ${JSON.stringify(EMAIL)}, password: ${JSON.stringify(PASSWORD)} }),
        });
        if (!r.ok) return 'HTTP ' + r.status;
        const { data } = await r.json();
        localStorage.setItem('NOCOBASE_TOKEN', data.token);
        return 'ok:' + (data.user && data.user.nickname);
      })()`,
    });
    check('登录成功并写入 NOCOBASE_TOKEN', login.result.value.startsWith('ok:'),
      login.result.value);

    // 打开 /admin，等待应用渲染 + 插件加载
    loadEventCount = 0;
    await cdp.send('Page.navigate', { url: `${BASE}/admin` });
    await waitFor(() => loadEventCount >= 1 ? true : null, 30000, '/admin load');

    let lastProbe = null;
    const probes = await waitFor(async () => {
      const r = await cdp.send('Runtime.evaluate', {
        returnByValue: true,
        expression: `(() => {
          const root = document.querySelector('#root');
          return JSON.stringify({
            rendered: !!(root && root.childElementCount),
            path: location.pathname,
            title: document.title,
            requirejsGlobal: typeof window.requirejs,
            windowApp: typeof window.app,
            pluginModule: (() => {
              // 注册表在 require 函数对象上（require.s.contexts._.defined），
              // 不是 window.requirejs 配置函数本身（probe-shot.js 实测验证过的判据）
              const rq = window.requirejs && (window.requirejs.require || window.requirejs.requirejs);
              if (!rq) return 'no-requirejs';
              const def = rq.s && rq.s.contexts && rq.s.contexts._ && rq.s.contexts._.defined || {};
              const m = def[${JSON.stringify(PKG)}];
              return m ? (typeof (m.default || m)) : 'not-loaded';
            })(),
          });
        })()`,
      });
      try { lastProbe = JSON.parse(r.result.value); } catch { lastProbe = { parse: r.result.value }; }
      return lastProbe.rendered && lastProbe.pluginModule !== 'no-requirejs'
        && lastProbe.pluginModule !== 'not-loaded' ? lastProbe : null;
    }, 45000, 'app render + plugin module registered').catch((err) => {
      console.log(`[DIAG] 超时前最后一次探测: ${JSON.stringify(lastProbe)}`);
      console.log(`[DIAG] console errors: ${JSON.stringify(consoleErrors.slice(0, 5))}`);
      console.log(`[DIAG] page errors: ${JSON.stringify(pageErrors.slice(0, 3))}`);
      console.log(`[DIAG] net failed: ${JSON.stringify(netFailed.slice(0, 3))}`);
      console.log(`[DIAG] plugin responses: ${JSON.stringify(pluginResponses)}`);
      throw err;
    });

    check('/admin 已渲染（#root 有内容）', probes.rendered);
    check('未被踢回登录页', probes.path !== '/signin', probes.path);
    check('RequireJS 注册表含本插件模块', probes.pluginModule === 'function',
      `typeof default = ${probes.pluginModule}`);
    check('插件 dist/client 请求返回 200', pluginResponses.includes(200),
      pluginResponses.join(',') || '(none)');

    // 平台插件管理器已启用本插件（window.app 在 2.2.37 不暴露，改用 pm:listEnabled 契约）
    const pmList = await cdp.send('Runtime.evaluate', {
      awaitPromise: true, returnByValue: true,
      expression: `(async () => {
        const r = await fetch('${BASE}/api/pm:listEnabled', {
          headers: { Authorization: 'Bearer ' + localStorage.getItem('NOCOBASE_TOKEN') },
        });
        const { data } = await r.json();
        const hit = (data || []).find((p) => p.name === '${PKG}' || p.packageName === '${PKG}'
          || p.name === 'hris-confirmation');
        return hit ? JSON.stringify({ name: hit.name || hit.packageName,
          url: (hit.url || (hit.distUrls && hit.distUrls.client) || '') }) : 'not-enabled';
      })()`,
    });
    const pmInfo = JSON.parse(pmList.result.value || '"?"');
    check('pm:listEnabled 包含本插件（客户端入口已下发）',
      pmList.result.value !== 'not-enabled',
      typeof pmInfo === 'object' ? JSON.stringify(pmInfo) : pmList.result.value);

    // 给异步初始化留一点余量后再统计错误
    await sleep(5000);
    const bad = [...consoleErrors, ...pageErrors].filter((t) =>
      /Script error|hris-confirmation|load plugin error/i.test(t));
    check('无 Script error / 本插件相关报错', bad.length === 0, bad.slice(0, 3).join(' | ') || '(clean)');
    check('无网络加载失败', netFailed.length === 0, netFailed.slice(0, 3).join(' | ') || '(clean)');

    // 其余错误只做提示（与 REV-009 无关的 console error 不计失败，但要如实打印）
    const others = [...consoleErrors, ...pageErrors].filter((t) =>
      !/Script error|hris-confirmation|load plugin error/i.test(t));
    if (others.length) console.log(`[INFO] 其他 console 错误（${others.length} 条，前 3 条）:`);
    others.slice(0, 3).forEach((t) => console.log('  ' + t.slice(0, 200)));

    console.log(`== verify_browser_page: ${pass} passed, ${fail} failed ==`);
    await cdp.close();
    process.exit(fail ? 1 : 0);
  } finally {
    proc.kill();
    try { fs.rmSync(userDataDir, { recursive: true, force: true }); } catch {}
  }
}

main().catch((err) => {
  console.error(`[ERROR] ${err.message || err}`);
  process.exit(1);
});
