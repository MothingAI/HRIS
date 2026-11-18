#!/usr/bin/env node
/** N3.4 低代码加字段浏览器实测（动态字段演示在浏览器成立）：
 * 1) HR 员工管理：编辑抽屉出现"紧急联系人"输入框 → 填值保存（原生 update，
 *    平台 ACL 白名单已含新字段）；重新打开回显；
 * 2) 旧记录（从未填写）编辑抽屉正常打开、值为空；
 * 3) 员工A 我的档案：表格出现"紧急联系人"列并显示本人值。
 * 前置：python scripts/demo_lowcode_employee_field.py（字段+表单/列 schema）。
 */
'use strict';
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const BASE = process.env.BASE || 'http://127.0.0.1:13000';
const CDP_PORT = Number(process.env.CDP_PORT || 9244);
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// 输出直写 docs/evidence（探针自 scripts/browser 迁入受跟踪目录，REV-012）
const OUT = path.join(__dirname, '..', '..', 'docs', 'evidence');
// 可选 EVID_PREFIX 给截图名加前缀（如 'fix-'），避免覆盖既有证据截图
const EVID_PREFIX = process.env.EVID_PREFIX || '';
const CONTACT = '王芳 13800001234';

function loadEnv() {
  const env = {};
  for (const line of fs.readFileSync(path.join(OUT, '..', '..', '.env'), 'utf8').split(/\r?\n/)) {
    const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (m) env[m[1]] = m[2];
  }
  return env;
}
let PROC = null, WS = null;
main()
  .catch((e) => { console.error('FATAL: ' + e.message); process.exitCode = 1; })
  .finally(() => { try { WS && WS.close(); } catch {} try { PROC && PROC.kill(); } catch {} setTimeout(() => process.exit(process.exitCode || 0), 500); });

async function main() {
  const ENV = loadEnv();
  const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'hris-n3l-'));
  const proc = PROC = spawn(CHROME, ['--headless=new', '--disable-gpu', '--window-size=1440,980',
    `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${udd}`, 'about:blank'], { stdio: 'ignore' });
  let list;
  for (let i = 0; i < 50; i++) {
    try { list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json(); break; }
    catch { await sleep(300); }
  }
  const ws = WS = new WebSocket(list.find((t) => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((res) => ws.addEventListener('open', res));
  const pending = new Map(); let msgId = 0;
  const send = (method, params = {}) => new Promise((resolve) => {
    const id = ++msgId; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params }));
  });
  ws.addEventListener('message', (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
  });
  const ev = (expression) => send('Runtime.evaluate', { awaitPromise: true, returnByValue: true, expression }).then((r) => {
    if (r.exceptionDetails) throw new Error('eval failed: ' + (r.exceptionDetails.exception?.description || '').slice(0, 300));
    return r.result.value;
  });
  const CLICK = (text) => `(async () => {
    const cands = [...document.querySelectorAll('button,span,a')].filter((e) => e.childElementCount === 0 && e.textContent.trim() === ${JSON.stringify(text)} && e.offsetParent !== null);
    if (!cands.length) return 'not-found';
    cands[cands.length - 1].click(); return 'clicked';
  })()`;
  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(OUT, `${EVID_PREFIX}${name}.png`), Buffer.from(r.data, 'base64'));
    console.log(`  shot: ${name}.png`);
  };
  async function login(email, password) {
    await send('Page.navigate', { url: `${BASE}/signin` });
    await sleep(3500);
    await ev(`(async () => {
      localStorage.clear();
      const r = await fetch('${BASE}/api/auth:signIn', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: ${JSON.stringify(email)}, password: ${JSON.stringify(password)} }) });
      const { data } = await r.json();
      localStorage.setItem('NOCOBASE_TOKEN', data.token);
    })()`);
    await send('Page.navigate', { url: `${BASE}/admin` });
    await sleep(11000);
  }
  const openMenu = async (title) => {
    let r = 'not-found';
    for (let i = 0; i < 6 && r !== 'clicked'; i++) { r = await ev(CLICK(title)); if (r !== 'clicked') await sleep(3000); }
    if (r !== 'clicked') throw new Error(`菜单未找到: ${title}`);
    await sleep(6000);
  };
  const ok = (cond, label) => {
    console.log(`${cond ? '  ok:' : '  FAIL:'} ${label}`);
    if (!cond) process.exitCode = 1;
  };
  // 打开指定工号行的编辑抽屉（员工管理页，行内"编辑"按钮）。就绪条件 =
  // 抽屉已渲染该工号且"紧急联系人"字段带输入框（Formily 表单）。关闭上一个
  // 抽屉会触发区块刷新，首次点击可能被吞——未就绪则重新点击，最多 3 轮。
  const openEdit = async (empNo) => {
    for (let attempt = 0; attempt < 3; attempt++) {
      const clicked = await ev(`(() => {
        const tables = [...document.querySelectorAll('.ant-table')].filter((t) => t.offsetParent !== null);
        const hit = tables.find((t) => t.textContent.includes('${empNo}'));
        if (!hit) return 'no-row';
        const row = [...hit.querySelectorAll('.ant-table-tbody tr')].find((r) => r.textContent.includes('${empNo}'));
        if (!row) return 'no-row';
        const btn = [...row.querySelectorAll('button,a')].find((b) => b.offsetParent !== null && b.textContent.trim() === '编辑');
        if (!btn) return 'no-edit-btn';
        btn.click(); return 'clicked';
      })()`);
      if (clicked !== 'clicked') return clicked;
      for (let i = 0; i < 6; i++) {
        await sleep(1000);
        const ready = await ev(`(() => {
          const d = [...document.querySelectorAll('.ant-drawer-content')]
            .filter((x) => x.offsetParent !== null).pop();
          if (!d) return false;
          // 工号是 input.value，不在 textContent 里——就绪判定读输入框值
          const isRow = [...d.querySelectorAll('input')].some((ii) => ii.value === '${empNo}');
          const item = [...d.querySelectorAll('.ant-formily-item, .ant-form-item')]
            .find((ii) => ii.textContent.includes('紧急联系人'));
          return !!(isRow && item && item.querySelector('input,textarea'));
        })()`);
        if (ready) return 'clicked';
      }
    }
    return 'drawer-not-ready';
  };
  const contactValue = () => ev(`(() => {
    const drawer = [...document.querySelectorAll('.ant-drawer-content')].filter((d) => d.offsetParent !== null).pop();
    if (!drawer) return 'no-drawer';
    const item = [...drawer.querySelectorAll('.ant-formily-item, .ant-form-item')]
      .find((i) => i.textContent.includes('紧急联系人'));
    if (!item) return 'no-field';
    const input = item.querySelector('input,textarea');
    return input ? (input.value === '' ? '(empty)' : input.value) : 'no-input';
  })()`);
  const setContact = (val) => ev(`(() => {
    const drawer = [...document.querySelectorAll('.ant-drawer-content')].filter((d) => d.offsetParent !== null).pop();
    if (!drawer) return 'no-drawer';
    const item = [...drawer.querySelectorAll('.ant-formily-item, .ant-form-item')]
      .find((i) => i.textContent.includes('紧急联系人'));
    if (!item) return 'no-field';
    const input = item.querySelector('input,textarea');
    if (!input) return 'no-input';
    const proto = input.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(input, ${JSON.stringify(val)});
    input.dispatchEvent(new Event('input', { bubbles: true }));
    return 'set';
  })()`);
  const submitDrawer = async () => {
    const r = await ev(`(() => {
      const drawer = [...document.querySelectorAll('.ant-drawer-content')].filter((d) => d.offsetParent !== null).pop();
      if (!drawer) return 'no-drawer';
      const btn = [...drawer.querySelectorAll('button')].find((b) => b.offsetParent !== null && b.textContent.trim() === '提交' && !b.disabled);
      if (!btn) return 'no-submit';
      btn.click(); return 'clicked';
    })()`);
    await sleep(5000);
    return r;
  };
  const closeDrawer = async () => {
    for (let i = 0; i < 6; i++) {
      await ev(`(() => {
        const x = [...document.querySelectorAll('.ant-drawer-close')].filter((b) => b.offsetParent !== null).pop();
        if (x) x.click(); return 'closed';
      })()`);
      await sleep(1500);
      const gone = await ev(`(() => [...document.querySelectorAll('.ant-drawer-content')]
        .every((d) => d.offsetParent === null))()`);
      if (gone) return;
    }
  };

  await send('Runtime.enable'); await send('Page.enable');

  console.log('\n-- HR：编辑李思远(E002) 填紧急联系人并保存 --');
  await login('hr@example.com', ENV.DEMO_PASSWORD);
  await openMenu('员工管理');
  await sleep(2500);
  let r = await openEdit('E002');
  ok(r === 'clicked', `打开 E002 编辑抽屉: ${r}`);
  let cv = await contactValue();
  ok(typeof cv === 'string' && cv !== 'no-field' && cv !== 'no-input', `编辑表单出现 紧急联系人 输入框: ${cv}`);
  await shot('n3-lowcode-1-edit-empty');
  r = await setContact(CONTACT);
  ok(r === 'set', `填入联系人: ${r}`);
  await shot('n3-lowcode-2-filled');
  r = await submitDrawer();
  ok(r === 'clicked', `点击提交: ${r}`);
  await shot('n3-lowcode-3-saved');

  console.log('\n-- HR：重新打开 E002 回显 --');
  r = await openEdit('E002');
  ok(r === 'clicked', `重新打开: ${r}`);
  cv = await contactValue();
  ok(cv === CONTACT, `保存后回显: ${cv}`);
  await shot('n3-lowcode-4-reopen');
  await closeDrawer();

  console.log('\n-- HR：旧记录 E004（从未填写）正常打开 --');
  r = await openEdit('E004');
  ok(r === 'clicked', `打开 E004: ${r}`);
  cv = await contactValue();
  ok(cv === '(empty)', `旧记录值为空且可打开: ${cv}`);
  await shot('n3-lowcode-5-old-record');
  await closeDrawer();

  console.log('\n-- 员工A：我的档案出现紧急联系人列与本人值 --');
  await login('employee.a@example.com', ENV.DEMO_PASSWORD);
  await openMenu('我的档案');
  await sleep(3000);
  const col = await ev(`(() => {
    const tables = [...document.querySelectorAll('.ant-table')].filter((t) => t.offsetParent !== null);
    const hit = tables[0];
    if (!hit) return 'no-table';
    const heads = [...hit.querySelectorAll('.ant-table-thead th')].map((h) => h.textContent.trim());
    const row = [...hit.querySelectorAll('.ant-table-tbody tr')].find((r) => r.textContent.includes('李思远')) || {};
    return JSON.stringify({ heads, row: row.textContent.replace(/\\s+/g, ' ').slice(0, 150) });
  })()`);
  console.log(`我的档案: ${col}`);
  if (col === 'no-table') { ok(false, '我的档案表格未渲染'); } else {
    const { heads, row } = JSON.parse(col);
    ok(heads.includes('紧急联系人'), `表头含 紧急联系人: ${JSON.stringify(heads)}`);
    ok((row || '').includes(CONTACT), `李思远行显示本人值`);
  }
  await shot('n3-lowcode-6-employee-view');

  // API 复核（服务端确实落库，不依赖 UI）
  const tok = (await fetch(`${BASE}/api/auth:signIn`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: ENV.INIT_ROOT_EMAIL, password: ENV.INIT_ROOT_PASSWORD }),
  }).then((x) => x.json())).data.token;
  const saved = (await fetch(`${BASE}/api/employees:list?filter=${encodeURIComponent(JSON.stringify({ employeeNo: 'E002' }))}`,
    { headers: { Authorization: `Bearer ${tok}` } }).then((x) => x.json())).data[0];
  console.log(`API 复核 E002.emergencyContact = ${saved && saved.emergencyContact}`);
  ok(saved && saved.emergencyContact === CONTACT, '后端字段持久化（非仅 UI）');
}
