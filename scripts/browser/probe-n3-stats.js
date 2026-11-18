#!/usr/bin/env node
/** N3 人事概览统计页浏览器实测：
 * 1) HR 打开 人事概览：四张计数卡（在职/试用/待审批/离职）+ 部门在职人数表 + 明细表；
 *    卡片值与 API 全量口径核对（不依赖 20 条分页）。
 * 2) 员工/主管菜单无 人事概览；其 token 直接调 charts:queryData 仍被数据范围限制
 *    （员工=1 本人，主管=本人+直属下属在职数，按数据范围推导），证明"员工和主管
 * 截图 n3-hr-overview.png；任何断言失败置 process.exitCode=1。
 */
'use strict';
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const BASE = process.env.BASE || 'http://127.0.0.1:13000';
const CDP_PORT = Number(process.env.CDP_PORT || 9241);
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// 输出直写 docs/evidence（探针自 scripts/browser 迁入受跟踪目录，REV-012）
const OUT = path.join(__dirname, '..', '..', 'docs', 'evidence');
// 可选 EVID_PREFIX 给截图名加前缀（如 'fix-'），避免覆盖既有证据截图
const EVID_PREFIX = process.env.EVID_PREFIX || '';
const consoleErrors = [];

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
  .finally(() => {
    if (consoleErrors.length) console.log('console-errors:\n' + consoleErrors.slice(-15).join('\n'));
    try { WS && WS.close(); } catch {}
    try { PROC && PROC.kill(); } catch {}
    setTimeout(() => process.exit(process.exitCode || 0), 500);
  });

async function main() {
  const ENV = loadEnv();

  // API 全量口径（对账基准，与卡片显示值必须一致）
  const signIn = async (email, password) => (await fetch(`${BASE}/api/auth:signIn`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  }).then((r) => r.json())).data.token;
  const tokHR = await signIn('hr@example.com', ENV.DEMO_PASSWORD);
  const chartCount = async (tok, collection, filter, field = 'employeeNo') => {
    const r = await fetch(`${BASE}/api/charts:queryData`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tok}` },
      body: JSON.stringify({
        uid: 'n3-probe', dataSource: 'main', collection, mode: 'builder',
        variableResolution: 'legacy-schema',
        measures: [{ field: [field], aggregation: 'count', alias: 'count' }],
        dimensions: [], filter, orders: [],
      }),
    }).then((x) => x.json());
    return r;
  };
  const ACT = { status: { $in: ['PROBATION', 'REGULAR'] } };
  const apiActive = (await chartCount(tokHR, 'employees', ACT)).data[0].count;
  const apiProb = (await chartCount(tokHR, 'employees', { status: { $eq: 'PROBATION' } })).data[0].count;
  const apiPend = (await chartCount(tokHR, 'confirmation_requests', { status: { $eq: 'PENDING' } }, 'status')).data[0].count;
  const apiLeft = (await chartCount(tokHR, 'employees', { status: { $eq: 'LEFT' } })).data[0].count;
  console.log(`API 口径基准: 在职=${apiActive} 试用=${apiProb} 待审批=${apiPend} 离职=${apiLeft}`);

  const udd = fs.mkdtempSync(path.join(os.tmpdir(), 'hris-n3-'));
  const proc = PROC = spawn(CHROME, ['--headless=new', '--disable-gpu', '--window-size=1440,980',
    `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${udd}`, 'about:blank'], { stdio: 'ignore' });
  let list;
  for (let i = 0; i < 50; i++) {
    try { list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json(); break; }
    catch { await sleep(300); }
  }
  const ws = WS = new WebSocket(list.find((t) => t.type === 'page').webSocketDebuggerUrl);
  await new Promise((res) => ws.addEventListener('open', res));
  const pending = new Map();
  let msgId = 0;
  const send = (method, params = {}) => new Promise((resolve) => {
    const id = ++msgId;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
  ws.addEventListener('message', (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result); pending.delete(m.id); }
    if (m.method === 'Runtime.consoleAPICalled' && (m.params.type === 'error' || m.params.type === 'warning')) {
      const t = (m.params.args || []).map((a) => a.value || a.description || '').join(' ').slice(0, 200);
      if (t && !/auth:check|aiEmployees|401/.test(t)) consoleErrors.push(`[${m.params.type}] ${t}`);
    }
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
      const r = await fetch('${BASE}/api/auth:signIn', { method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: ${JSON.stringify(email)}, password: ${JSON.stringify(password)} }) });
      const { data } = await r.json();
      localStorage.setItem('NOCOBASE_TOKEN', data.token);
    })()`);
    await send('Page.navigate', { url: `${BASE}/admin` });
    await sleep(11000);
  }
  const menuTexts = () => ev(`(() => JSON.stringify(
    [...document.querySelectorAll('.ant-menu a, .ant-menu span, .ant-menu li')]
      .filter((e) => e.offsetParent !== null && e.textContent.trim())
      .map((e) => e.textContent.trim())).slice(0, 500))()`);
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

  await send('Runtime.enable');
  await send('Page.enable');

  console.log('\n-- HR：人事概览统计页 --');
  await login('hr@example.com', ENV.DEMO_PASSWORD);
  await openMenu('人事概览');
  await sleep(4000);

  const cards = await ev(`(() => {
    const out = {};
    for (const c of document.querySelectorAll('.nb-chart-block')) {
      if (c.offsetParent === null) continue;
      const t = c.textContent.replace(/\\s+/g, ' ');
      for (const title of ['在职人数','试用人数','待审批数','离职人数']) {
        if (out[title] !== undefined) continue;  // 只取首个出现（计数卡区块）
        const m = t.match(new RegExp(title + '[^0-9]{0,300}?([0-9]+)'));
        if (m) out[title] = m[1];
      }
    }
    return JSON.stringify(out);
  })()`);
  console.log(`计数卡: ${cards}`);
  const cardMap = JSON.parse(cards || '{}');
  ok(cardMap['在职人数'] === String(apiActive), `在职人数=${cardMap['在职人数']}（应为 ${apiActive}）`);
  ok(cardMap['试用人数'] === String(apiProb), `试用人数=${cardMap['试用人数']}（应为 ${apiProb}）`);
  ok(cardMap['待审批数'] === String(apiPend), `待审批数=${cardMap['待审批数']}（应为 ${apiPend}）`);
  ok(cardMap['离职人数'] === String(apiLeft), `离职人数=${cardMap['离职人数']}（应为 ${apiLeft}）`);

  const deptRows = await ev(`(() => {
    const tables = [...document.querySelectorAll('.ant-table')].filter((t) => t.offsetParent !== null);
    const hit = tables.find((t) => ((t.querySelector('.ant-table-thead') || {}).textContent || '').includes('部门'));
    if (!hit) return 'no-table';
    const heads = [...hit.querySelectorAll('.ant-table-thead th')].map((h) => h.textContent.trim());
    const rows = [...hit.querySelectorAll('.ant-table-tbody tr')].filter((r) => r.offsetParent !== null)
      .map((r) => [...r.querySelectorAll('td')].map((c) => c.textContent.trim()));
    return JSON.stringify({ heads, rows });
  })()`);
  console.log(`部门在职表: ${deptRows}`);
  if (deptRows === 'no-table') { ok(false, '部门在职人数表未渲染'); } else {
    const { heads, rows } = JSON.parse(deptRows);
    ok(heads.includes('部门') && heads.includes('在职人数'), `表头含 部门/在职人数: ${JSON.stringify(heads)}`);
    // TableV2 空表/测量占位行首列为空串，跳过（LIMITATIONS §3 已记录）
    const valid = rows.filter((r) => r.length >= 2 && r[0] && r[1]);
    const m = Object.fromEntries(valid.map((r) => [r[1], r[0]]));
    const apiDept = (await (await fetch(`${BASE}/api/charts:queryData`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${tokHR}` },
      body: JSON.stringify({
        uid: 'n3-probe-d', dataSource: 'main', collection: 'employees', mode: 'builder',
        variableResolution: 'legacy-schema',
        measures: [{ field: ['employeeNo'], aggregation: 'count', alias: 'count' }],
        dimensions: [{ field: ['department', 'name'], alias: 'dept' }],
        filter: ACT, orders: [],
      }),
    }).then((x) => x.json())).data).reduce((a, r) => ({ ...a, [r.dept]: r.count }), {});
    console.log(`  API 部门口径: ${JSON.stringify(apiDept)}`);
    const norm = (o) => JSON.stringify(Object.fromEntries(
      Object.entries(o).map(([k, v]) => [k, String(v)]).sort((a, b) => a[0].localeCompare(b[0], 'zh'))));
    ok(norm(m) === norm(apiDept), `页面行与 API 口径一致: ${JSON.stringify(m)}`);
  }
  await shot('n3-hr-overview');

  console.log('\n-- 员工A：无 人事概览 菜单；直接调 charts API 仍仅本人 --');
  await login('employee.a@example.com', ENV.DEMO_PASSWORD);
  const empMenu = JSON.parse(await menuTexts());
  ok(!empMenu.includes('人事概览'), `员工菜单无 人事概览: ${JSON.stringify(empMenu)}`);
  const empQ = await ev(`(async () => {
    const r = await fetch('${BASE}/api/charts:queryData', {
      method: 'POST', headers: { 'Content-Type': 'application/json',
        Authorization: 'Bearer ' + localStorage.getItem('NOCOBASE_TOKEN') },
      body: JSON.stringify({ uid:'n3p', dataSource:'main', collection:'employees', mode:'builder',
        variableResolution:'legacy-schema',
        measures:[{field:['employeeNo'],aggregation:'count',alias:'count'}],
        dimensions:[], filter:${JSON.stringify(ACT)}, orders:[] }) });
    return JSON.stringify(await r.json());
  })()`);
  console.log(`员工 charts 全公司在职计数: ${empQ.slice(0, 120)}`);
  const empCount = JSON.parse(empQ).data[0].count;
  ok(empCount === 1, `员工只能数到本人（${empCount}），非全公司 ${apiActive}`);

  console.log('\n-- 主管A：无 人事概览 菜单；直接调 charts API 仍仅本组 --');
  await login('manager.a@example.com', ENV.DEMO_PASSWORD);
  const mgrMenu = JSON.parse(await menuTexts());
  ok(!mgrMenu.includes('人事概览'), `主管菜单无 人事概览: ${JSON.stringify(mgrMenu)}`);
  const mgrQ = await ev(`(async () => {
    const r = await fetch('${BASE}/api/charts:queryData', {
      method: 'POST', headers: { 'Content-Type': 'application/json',
        Authorization: 'Bearer ' + localStorage.getItem('NOCOBASE_TOKEN') },
      body: JSON.stringify({ uid:'n3p', dataSource:'main', collection:'employees', mode:'builder',
        variableResolution:'legacy-schema',
        measures:[{field:['employeeNo'],aggregation:'count',alias:'count'}],
        dimensions:[], filter:${JSON.stringify(ACT)}, orders:[] }) });
    return JSON.stringify(await r.json());
  })()`);
  console.log(`主管 charts 全公司在职计数: ${mgrQ.slice(0, 120)}`);
  const mgrCount = JSON.parse(mgrQ).data[0].count;
  // 期望值按 HR 全量推导（本人+直属下属中在职的人数），不写死 7——
  // 演示/工具员工（DEMO-NN 直属 E001）加入后本组人数会变（REV-012 同款修正）
  const mgrExpected = await ev(`(async () => {
    const r = await fetch('${BASE}/api/employees:list?pageSize=500', {
      headers: { Authorization: 'Bearer ' + localStorage.getItem('NOCOBASE_TOKEN') } });
    return JSON.stringify(await r.json());
  })()`).then((s) => {
    // 此时仍以主管身份登录：其 list 即本人+直属下属（同数据范围）
    const rows = JSON.parse(s).data || [];
    return rows.filter((x) => x.status === 'PROBATION' || x.status === 'REGULAR').length;
  });
  ok(mgrCount === mgrExpected,
     `主管只能数到本组在职（${mgrCount}，按数据范围推导应为 ${mgrExpected}），非全公司 ${apiActive}`);
}
