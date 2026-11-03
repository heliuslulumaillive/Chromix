#!/usr/bin/env node
// Native custom-UA / structured UA-CH acceptance. CDP is used only to close
// the exact owned browser normally; collection is ordinary loopback page JS.
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import http from 'node:http';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {browserEnvironment, ownedWindowsProcesses} from './verify_fingerprint_modes_runtime.mjs';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
async function deadline(promise, ms, label) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(label)), ms);
  })]); } finally { clearTimeout(timer); }
}
async function digest(filename) {
  const h = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(filename)) h.update(chunk);
  return h.digest('hex');
}
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.8059.39 Safari/537.36 Edg/156.2.1234.5';
const REALMS = ['window', 'iframe', 'dedicatedWorker', 'sharedWorker', 'serviceWorker'];
const HIGH_KEYS = ['architecture', 'bitness', 'model', 'platformVersion', 'uaFullVersion', 'fullVersionList', 'wow64', 'formFactors'];
const CH_NAMES = ['Sec-CH-UA', 'Sec-CH-UA-Mobile', 'Sec-CH-UA-Platform', 'Sec-CH-UA-Arch', 'Sec-CH-UA-Bitness', 'Sec-CH-UA-Model', 'Sec-CH-UA-Platform-Version', 'Sec-CH-UA-Full-Version', 'Sec-CH-UA-Full-Version-List', 'Sec-CH-UA-WoW64', 'Sec-CH-UA-Form-Factors'];
export function scenarios() {
  const seed = ['--fingerprint=10026'];
  const fields = ['--fingerprint-brand=edge', '--fingerprint-brand-version=156.2.1234.5', '--fingerprint-platform=windows', '--fingerprint-platform-version=19.0.0', '--uxr-ua-platform=Windows', '--uxr-ua-arch=arm', '--uxr-ua-bitness=64', '--uxr-ua-model=Owned-UA-CH-Model', '--uxr-ua-wow64=true'];
  const custom = [`--user-agent=${UA}`];
  const normal = ['--disable-features=UACHOverrideBlank'];
  const blank = ['--enable-features=UACHOverrideBlank'];
  const make = (id, flags, kind, reference = null) => ({id, flags, kind, reference});
  return [
    make('native_reference', [...normal, ...seed, '--fingerprint-identity-mode=native'], 'native'),
    make('structured_reference', [...normal, ...seed, ...fields], 'structured'),
    make('custom_structured', [...normal, ...seed, ...fields, ...custom], 'match', 'structured_reference'),
    make('custom_no_seed', [...normal, ...fields, ...custom], 'match', 'structured_reference'),
    make('partial_reference', [...normal, ...seed, '--fingerprint-platform-version=19.0.0'], 'partial'),
    make('custom_partial', [...normal, ...seed, '--fingerprint-platform-version=19.0.0', ...custom], 'match', 'partial_reference'),
    make('raw_only_seed', [...normal, ...seed, ...custom], 'legacy'),
    make('raw_only_no_seed', [...normal, ...custom], 'legacy'),
    make('raw_only_blank', [...blank, ...custom], 'blank'),
    make('custom_structured_blank', [...blank, ...seed, ...fields, ...custom], 'match', 'structured_reference'),
    make('unknown_and_os_only', [...normal, ...seed, '--uxr-ua-owned-probe=ignored', '--uxr-ua-os=Owned-UA-CH-OS', ...custom], 'legacy'),
    make('native_identity', [...normal, ...seed, ...fields, ...custom, '--fingerprint-identity-mode=native'], 'match-native', 'native_reference'),
    make('reduced_system_info', [...normal, '--enable-features=ReducedSystemInfo', ...seed, ...fields, ...custom], 'reduced'),
    make('native_platform_reference', [...normal, ...seed, '--fingerprint-platform=windows'], 'native-platform'),
    make('custom_native_platform', [...normal, ...seed, '--fingerprint-platform=windows', ...custom], 'match', 'native_platform_reference'),
  ];
}

// This function's source is embedded unchanged in each owned realm.
async function navigatorProbe(echoURL) {
  if (!navigator.userAgentData) throw new Error('userAgentData unavailable');
  const keys = ['architecture', 'bitness', 'model', 'platformVersion', 'uaFullVersion', 'fullVersionList', 'wow64', 'formFactors'];
  const low = navigator.userAgentData.toJSON();
  const high = await navigator.userAgentData.getHighEntropyValues(keys);
  await fetch(echoURL + '?prime=1', {cache: 'no-store'});
  const headers = await (await fetch(echoURL + '?afterAcceptCH=1', {cache: 'no-store'})).json();
  return {ua: navigator.userAgent, platform: navigator.platform, low, high, headers, secureContext: isSecureContext};
}

async function pageProbe(base) {
  const timed = (task, label) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(label + ' deadline')), 12000);
    task.then(value => { clearTimeout(timer); resolve(value); }, error => { clearTimeout(timer); reject(error); });
  });
  const capture = async task => { try { return await task(); } catch (error) { return {error: String(error.stack || error)}; } };
  const result = {window: await capture(() => navigatorProbe(base + 'echo/window'))};
  result.iframe = await capture(async () => {
    const frame = document.createElement('iframe');
    const loaded = new Promise((resolve, reject) => { frame.onload = resolve; frame.onerror = reject; });
    frame.src = base + 'frame'; document.body.append(frame);
    await timed(loaded, 'iframe load');
    try { return await timed(frame.contentWindow.navigatorProbe(base + 'echo/iframe'), 'iframe probe'); }
    finally { frame.remove(); }
  });
  result.dedicatedWorker = await capture(async () => {
    const worker = new Worker(base + 'dedicated.js');
    try { return await timed(new Promise((resolve, reject) => {
      worker.onmessage = e => e.data.error ? reject(new Error(e.data.error)) : resolve(e.data);
      worker.onerror = e => reject(new Error(e.message)); worker.postMessage(base + 'echo/dedicatedWorker');
    }), 'dedicated worker'); } finally { worker.terminate(); }
  });
  result.sharedWorker = await capture(async () => {
    const worker = new SharedWorker(base + 'shared.js', {name: 'owned-ua-ch'});
    try { return await timed(new Promise((resolve, reject) => {
      worker.port.onmessage = e => e.data.error ? reject(new Error(e.data.error)) : resolve(e.data);
      worker.onerror = e => reject(new Error(e.message)); worker.port.start(); worker.port.postMessage(base + 'echo/sharedWorker');
    }), 'shared worker'); } finally { worker.port.close(); }
  });
  result.serviceWorker = await capture(async () => {
    const registration = await navigator.serviceWorker.register(base + 'service.js', {scope: base});
    try {
      let worker = registration.active || registration.installing || registration.waiting;
      if (!worker) throw new Error('service worker missing');
      if (worker.state !== 'activated') await timed(new Promise((resolve, reject) => {
        worker.addEventListener('statechange', () => { if (worker.state === 'activated') resolve(); else if (worker.state === 'redundant') reject(new Error('service worker redundant')); });
      }), 'service worker activation');
      worker = registration.active || worker;
      const channel = new MessageChannel();
      try { return await timed(new Promise((resolve, reject) => {
        channel.port1.onmessage = e => e.data.error ? reject(new Error(e.data.error)) : resolve(e.data);
        worker.postMessage(base + 'echo/serviceWorker', [channel.port2]);
      }), 'service worker probe'); } finally { channel.port1.close(); }
    } finally { await registration.unregister(); }
  });
  return result;
}

const quote = text => JSON.stringify(text);
function serializeBrands(rows) { return rows.map(row => `${quote(row.brand)};v=${quote(row.version)}`).join(', '); }
export function verifyResults(rows) {
  const checks = [];
  const check = (scenario, name, pass, actual, expected) => checks.push({scenario, name, passed: !!pass,
    ...(!pass ? {actual: actual ?? null, expected: expected ?? null} : {})});
  const matrix = scenarios();
  check('matrix', 'complete', rows.length === matrix.length, rows.length, matrix.length);
  for (const scenario of matrix) {
    const row = rows.find(item => item.id === scenario.id);
    check(scenario.id, 'collected', !!row?.probe && !row?.error, row?.error, 'collected without probe error');
    check(scenario.id, 'normal_owned_browser_exit', row?.cleanup?.normalExit, row?.cleanup, 'normal exit with no known owned processes');
    check(scenario.id, 'no_emulation_commands', same(row?.cdp || [], ['Browser.close']), row?.cdp, ['Browser.close']);
    if (!row?.probe) continue;
    const reference = scenario.reference ? rows.find(item => item.id === scenario.reference)?.probe : null;
    for (const realm of REALMS) {
      const got = row.probe[realm];
      check(scenario.id, `${realm}.available`, !!got?.high && !got?.error, got?.error, 'available');
      if (!got?.high) continue;
      check(scenario.id, `${realm}.secure_context`, got.secureContext, got.secureContext, true);
      check(scenario.id, `${realm}.same_low_as_window`, same(got.low, row.probe.window?.low), got.low, row.probe.window?.low);
      check(scenario.id, `${realm}.same_high_as_window`, same(got.high, row.probe.window?.high), got.high, row.probe.window?.high);
      check(scenario.id, `${realm}.request_user_agent`, got.headers['user-agent'] === got.ua, got.headers['user-agent'], got.ua);
      if (['match', 'legacy', 'blank', 'reduced'].includes(scenario.kind)) check(scenario.id, `${realm}.explicit_ua_preserved`, got.ua === UA, got.ua, UA);
      if (scenario.kind === 'match' || scenario.kind === 'match-native') {
        const expected = reference?.[realm];
        check(scenario.id, `${realm}.metadata_matches_structured_reference`, !!expected && same(got.high, expected.high) && same(got.low, expected.low), {low: got.low, high: got.high}, expected && {low: expected.low, high: expected.high});
        check(scenario.id, `${realm}.ch_headers_match_reference`, !!expected && same(Object.fromEntries(CH_NAMES.map(k => [k, got.headers[k.toLowerCase()] ?? null])), Object.fromEntries(CH_NAMES.map(k => [k, expected.headers[k.toLowerCase()] ?? null]))), got.headers, expected?.headers);
        if (scenario.kind === 'match-native') check(scenario.id, `${realm}.native_ua_wins`, got.ua === expected?.ua && got.ua !== UA, got.ua, expected?.ua);
      }
      if (scenario.kind === 'structured') {
        for (const [key, value] of Object.entries({architecture: 'arm', bitness: '64', model: 'Owned-UA-CH-Model', platformVersion: '19.0.0', uaFullVersion: '156.2.1234.5', wow64: true})) {
          check(scenario.id, `${realm}.${key}.explicit`, got.high[key] === value, got.high[key], value);
        }
        check(scenario.id, `${realm}.brand_full_version.explicit`, got.high.fullVersionList?.some(b => b.brand === 'Microsoft Edge' && b.version === '156.2.1234.5'), got.high.fullVersionList, 'Microsoft Edge 156.2.1234.5');
        check(scenario.id, `${realm}.platform.explicit`, got.low.platform === 'Windows', got.low.platform, 'Windows');
      }
      if (scenario.kind === 'partial') check(scenario.id, `${realm}.single_field_applied`, got.high.platformVersion === '19.0.0', got.high.platformVersion, '19.0.0');
      if (['legacy', 'blank', 'reduced'].includes(scenario.kind)) {
        for (const key of HIGH_KEYS) {
          const expected = key === 'wow64' ? false : key === 'fullVersionList' || key === 'formFactors' ? [] : '';
          check(scenario.id, `${realm}.${key}.remains_low_entropy_only`, same(got.high[key], expected), got.high[key], expected);
        }
        if (scenario.kind === 'blank') check(scenario.id, `${realm}.blank_feature_preserved`, same(got.low, {brands: [], mobile: false, platform: ''}), got.low, {brands: [], mobile: false, platform: ''});
      }
      // This Chromium build does not add UA-CH to worker-initiated fetch, even
      // in the unmodified native reference. Keep that separate from worker JS
      // metadata; the requested header-coherence gate applies to documents.
      if (realm.endsWith('Worker')) {
        const ch = Object.keys(got.headers).filter(name => name.startsWith('sec-ch-ua'));
        check(scenario.id, `${realm}.native_fetch_without_client_hints_preserved`, ch.length === 0, ch, []);
        continue;
      }
      // These are request headers observed at the loopback server after a real
      // Accept-CH response, not emulated headers or copied navigator values.
      const expectedHeaders = {
        'sec-ch-ua': serializeBrands(got.low.brands), 'sec-ch-ua-mobile': got.low.mobile ? '?1' : '?0',
        'sec-ch-ua-platform': quote(got.low.platform), 'sec-ch-ua-arch': quote(got.high.architecture),
        'sec-ch-ua-bitness': quote(got.high.bitness), 'sec-ch-ua-model': quote(got.high.model),
        'sec-ch-ua-platform-version': quote(got.high.platformVersion), 'sec-ch-ua-full-version': quote(got.high.uaFullVersion),
        'sec-ch-ua-full-version-list': serializeBrands(got.high.fullVersionList), 'sec-ch-ua-wow64': got.high.wow64 ? '?1' : '?0',
        'sec-ch-ua-form-factors': got.high.formFactors.map(quote).join(', '),
      };
      for (const [header, expected] of Object.entries(expectedHeaders)) {
        // Chromium omits empty brand-list headers for blank metadata. Other
        // empty string fields retain their explicit structured-header quotes.
        const actual = got.headers[header];
        check(scenario.id, `${realm}.header.${header}`, actual === expected || (expected === '' && actual === undefined), actual, expected);
      }
    }
  }
  return {checks, failed: checks.filter(row => !row.passed)};
}

async function closeOwned(child, profile, record) {
  let before = [], remaining = [], error = null, forced = false, closeSent = false;
  try {
    before = await ownedWindowsProcesses(child.pid);
    const [port, address] = (await fsp.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).trim().split(/\r?\n/);
    if (!/^\d+$/.test(port) || !address?.startsWith('/devtools/browser/')) throw new Error('Invalid owned debugging endpoint');
    const socket = new WebSocket(`ws://127.0.0.1:${port}${address}`);
    await deadline(new Promise((resolve, reject) => { socket.addEventListener('open', resolve, {once: true}); socket.addEventListener('error', reject, {once: true}); }), 10000, 'Owned browser close connection');
    record.cdp.push('Browser.close'); closeSent = true;
    socket.send(JSON.stringify({id: 1, method: 'Browser.close'}));
    for (let i = 0; i < 100 && child.exitCode === null; i++) await sleep(100);
    socket.close();
  } catch (e) { error = String(e?.message || e); }
  if (child.exitCode === null) {
    forced = true;
    const killer = spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {windowsHide: true, env: browserEnvironment(process.env), stdio: 'ignore'});
    await deadline(new Promise(resolve => { killer.once('close', resolve); killer.once('error', resolve); }), 10000, 'Owned forced cleanup').catch(() => {});
  }
  if (before.length) {
    await sleep(250);
    try { remaining = await ownedWindowsProcesses(child.pid, before); } catch (e) { error ||= String(e?.message || e); }
  }
  return {pid: child.pid, closeSent, forced, exitCode: child.exitCode, knownOwnedBefore: before, knownOwnedRemaining: remaining,
    error, normalExit: closeSent && !forced && child.exitCode === 0 && before.length > 0 && !remaining.length && !error};
}

export async function main(argv = process.argv.slice(2)) {
  const args = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--self-test') args.selfTest = true;
    else { if (!argv[i].startsWith('--') || !argv[i + 1]) throw new Error('Invalid arguments'); args[argv[i].slice(2)] = argv[++i]; }
  }
  if (args.selfTest) {
    if (!verifyResults([]).failed.length || scenarios().length !== 15) throw new Error('Empty matrix falsely passes');
    if (!same(browserEnvironment({PATH: 'ok', GH_TOKEN: 'drop', CREDENTIAL: 'drop'}), {PATH: 'ok'})) throw new Error('Secret environment filter failed');
    console.log(JSON.stringify({status: 'passed', selfTests: 3, browsersStarted: 0})); return 0;
  }
  if (!args.browser || !args.out || !args.lock) throw new Error('Required: --browser <exe> --out <new-directory> --lock <shared-browser.lock>');
  if (process.platform !== 'win32' || Number(process.versions.node.split('.')[0]) < 24) throw new Error('Windows and Node >= 24 required');
  const browser = path.resolve(args.browser), out = path.resolve(args.out), lockPath = path.resolve(args.lock);
  await fsp.mkdir(out, {recursive: false});
  const lock = await fsp.open(lockPath, 'wx');
  await lock.writeFile(JSON.stringify({pid: process.pid, owner: 'custom-ua-metadata-acceptance', out, started: new Date().toISOString()})); await lock.close();
  const token = crypto.randomBytes(12).toString('hex'), pending = new Map();
  const probeSource = 'const navigatorProbe=' + navigatorProbe.toString() + ';';
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (!url.pathname.startsWith('/' + token + '/')) { res.statusCode = 404; res.end(); return; }
    const segments = url.pathname.split('/'), id = segments[2], route = segments[3];
    res.setHeader('Cache-Control', 'no-store'); res.setHeader('Accept-CH', CH_NAMES.join(', '));
    res.setHeader('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; connect-src 'self'; worker-src 'self'; frame-src 'self'");
    if (route === 'echo') { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(Object.fromEntries(Object.entries(req.headers).filter(([k]) => k === 'user-agent' || k.startsWith('sec-ch-ua'))))); return; }
    if (route === 'result') {
      let body = ''; for await (const part of req) { body += part; if (body.length > 200000) { req.destroy(); return; } }
      try { const value = JSON.parse(body); pending.get(id)?.(value); res.end('ok'); } catch { res.statusCode = 400; res.end('bad JSON'); } return;
    }
    if (route?.endsWith('.js')) {
      res.setHeader('Content-Type', 'text/javascript');
      const worker = route === 'dedicated.js' ? 'onmessage=async e=>{try{postMessage(await navigatorProbe(e.data));}catch(error){postMessage({error:String(error.stack||error)});}close();};'
        : route === 'shared.js' ? 'onconnect=e=>{const port=e.ports[0];port.onmessage=async event=>{try{port.postMessage(await navigatorProbe(event.data));}catch(error){port.postMessage({error:String(error.stack||error)});}port.close();close();};port.start();};'
          : "self.addEventListener('install',e=>e.waitUntil(self.skipWaiting()));self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));self.addEventListener('message',e=>e.waitUntil((async()=>{try{e.ports[0].postMessage(await navigatorProbe(e.data));}catch(error){e.ports[0].postMessage({error:String(error.stack||error)});}})()));";
      res.end(probeSource + worker); return;
    }
    res.setHeader('Content-Type', 'text/html;charset=utf-8');
    const base = '/' + token + '/' + id + '/';
    const script = route === 'frame' ? 'window.navigatorProbe=navigatorProbe;' : '(' + pageProbe.toString() + ')(' + JSON.stringify(base) + ').then(data=>fetch(' + JSON.stringify(base + 'result') + ',{method:"POST",body:JSON.stringify(data)})).catch(error=>fetch(' + JSON.stringify(base + 'result') + ',{method:"POST",body:JSON.stringify({error:String(error.stack||error)})}));';
    res.end('<!doctype html><meta charset="utf-8"><title>Owned UA-CH acceptance</title><body><script>' + probeSource + script + '</script>');
  });
  const report = {schemaVersion: 1, status: 'failed', started: new Date().toISOString(), browser, out,
    browserSha256: await digest(browser), chromeDllSha256: await digest(path.join(path.dirname(browser), 'chrome.dll')),
    harnessSha256: await digest(fileURLToPath(import.meta.url)), processHelperSha256: await digest(path.join(path.dirname(fileURLToPath(import.meta.url)), 'verify_fingerprint_modes_runtime.mjs')),
    node: {version: process.version, path: process.execPath, sha256: await digest(process.execPath)}, transport: 'Native startup flags + loopback page, iframe and worker JS; CDP Browser.close only',
    credentialsEnvironmentRemoved: true, productionProfilesOpened: false, uaEmulation: false, realmNames: REALMS, expectedScenarios: scenarios().length,
    scope: ['Windows headless browser', 'Explicit structured fields are not inferred by parsing arbitrary UA', 'Worker navigator metadata is validated; worker fetch does not emit UA-CH in the native baseline. Real Accept-CH header assertions cover Window and iframe.', 'No network account, external fingerprint site, profile migration or kernel binding edits'], scenarios: []};
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = 'http://127.0.0.1:' + server.address().port;
    for (const scenario of scenarios()) {
      const folder = path.join(out, scenario.id), profile = path.join(folder, 'profile'); await fsp.mkdir(profile, {recursive: true});
      const resultPromise = new Promise(resolve => pending.set(scenario.id, resolve));
      const flags = ['--headless=new', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0', '--no-first-run', '--no-default-browser-check', '--disable-background-networking', '--disable-component-update', '--disable-sync', '--disable-extensions', '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1', `--user-data-dir=${profile}`, ...scenario.flags, `${origin}/${token}/${scenario.id}/`];
      const row = {id: scenario.id, kind: scenario.kind, args: flags, cdp: [], profile}; report.scenarios.push(row);
      await fsp.writeFile(path.join(folder, 'actual-args.json'), JSON.stringify({executable: browser, args: flags}, null, 2));
      const child = spawn(browser, flags, {windowsHide: true, env: browserEnvironment(process.env), stdio: ['ignore', 'pipe', 'pipe']});
      const stdout = fs.createWriteStream(path.join(folder, 'browser.stdout.log')), stderr = fs.createWriteStream(path.join(folder, 'browser.stderr.log'));
      child.stdout.pipe(stdout); child.stderr.pipe(stderr); const closed = new Promise(resolve => child.once('close', resolve));
      child.on('error', error => { row.spawnError = String(error.message); });
      try { row.probe = await deadline(resultPromise, 60000, 'Owned page collection deadline'); if (row.probe.error) row.error = row.probe.error; }
      catch (error) { row.error = String(error?.message || error); }
      finally {
        row.cleanup = await closeOwned(child, profile, row);
        await deadline(closed, 5000, 'Owned browser output did not close').catch(error => { row.error ||= error.message; });
        stdout.end(); stderr.end(); pending.delete(scenario.id);
      }
      await fsp.writeFile(path.join(folder, 'result.json'), JSON.stringify(row, null, 2));
      console.log(JSON.stringify({scenario: row.id, collected: !!row.probe && !row.error, normalExit: row.cleanup.normalExit}));
      if (!row.cleanup.normalExit) break;
    }
    const validation = verifyResults(report.scenarios); report.checks = validation.checks; report.failed = validation.failed;
    report.status = report.failed.length ? 'failed' : 'passed'; report.checkCount = report.checks.length; report.failedCount = report.failed.length;
  } catch (error) { report.fatalError = String(error?.stack || error); }
  finally {
    report.finished = new Date().toISOString();
    report.runtimeUnchanged = (await digest(browser)) === report.browserSha256 && (await digest(path.join(path.dirname(browser), 'chrome.dll'))) === report.chromeDllSha256;
    if (!report.runtimeUnchanged) report.status = 'failed';
    await fsp.writeFile(path.join(out, 'acceptance.json'), JSON.stringify(report, null, 2));
    await new Promise(resolve => server.close(resolve)); await fsp.unlink(lockPath);
  }
  console.log(JSON.stringify({status: report.status, scenarios: report.scenarios.length, checks: report.checkCount, failed: report.failedCount, report: path.join(out, 'acceptance.json')}));
  return report.status === 'passed' ? 0 : 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  process.exitCode = await main().catch(error => { console.error(error.stack || error); return 1; });
