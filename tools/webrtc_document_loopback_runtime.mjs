// Actual compiled-browser acceptance. No Playwright defaults, JS API overrides,
// account data, real camera/microphone, or system networking changes.
import {spawn} from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import {fileURLToPath} from 'node:url';

import {controlledSocks, stunCanary} from './webrtc_network_fixtures.mjs';
import {browserEnvironment, connectCDP, evaluate, stopOwned} from './webrtc_runtime_controller.mjs';

const option = name =>
    process.argv.find(a => a.startsWith('--' + name + '='))?.slice(name.length + 3);
const browser = path.resolve(option('browser') ?? ''),
      reportPath = path.resolve(option('report') ?? '');
const helper = path.resolve(option('turn-helper') ?? '');
if (!option('browser') || !option('report') || !option('turn-helper'))
  throw Error(
      'Required: --browser=<chrome.exe> --report=<new.json> --turn-helper=<owned-turn.exe>');
const toolsDir = path.dirname(fileURLToPath(import.meta.url)), work = reportPath + '.work';
await fs.mkdir(work, {recursive: false});
const hash = b => crypto.createHash('sha256').update(b).digest('hex');
const delay = ms => new Promise(r => setTimeout(r, ms));
const source = await fs.readFile(path.join(toolsDir, 'webrtc_document_loopback_probe.js'), 'utf8');
const dll = path.join(path.dirname(browser), 'chrome.dll');
const files = [
  'webrtc_document_loopback_runtime.mjs', 'webrtc_document_loopback_probe.js',
  'webrtc_runtime_controller.mjs', 'webrtc_network_fixtures.mjs'
];
const report = {
  schemaVersion: 1,
  kind: 'document-webrtc-loopback-runtime',
  status: 'running',
  nodeVersion: process.version,
  startedUtc: new Date().toISOString(),
  browser,
  exeSHA256: hash(await fs.readFile(browser)),
  dllSHA256: hash(await fs.readFile(dll)),
  harness: {},
  turnHelperSHA256: hash(await fs.readFile(helper)),
  noJavaScriptOverrides: true,
  realMediaCaptured: false,
  runs: [],
  cases: [],
  checks: []
};
for (const name of files)
  report.harness[name] = hash(await fs.readFile(path.join(toolsDir, name)));
const save = () => fs.writeFile(reportPath, JSON.stringify(report, null, 2));
const check = (id, pass, details) =>
    report.checks.push({id, pass: !!pass, ...(details ? {details} : {})});
const localIPs =
    Object.values(os.networkInterfaces()).flat().filter(Boolean).map(x => x.address.split('%')[0]);
const server = http.createServer((req, res) => {
  res.setHeader('Content-Type', 'text/html');
  res.end(
      '<!doctype html><title>Owned loopback acceptance</title><body><script>' + source +
      '</script>');
});
await new Promise(r => server.listen(0, '127.0.0.1', r));
const origin = 'http://127.0.0.1:' + server.address().port;
const canary = await stunCanary(), rejectProxy = await controlledSocks();
const proxy = option('proxy') === 'controlled' ? rejectProxy.url :
                                                 (option('proxy') ?? 'socks5://127.0.0.1:40060');
const proxyURL = new URL(proxy);
if (proxyURL.protocol !== 'socks5:' || proxyURL.username || proxyURL.password)
  throw Error('Acceptance requires an unauthenticated SOCKS5 endpoint');
report.proxy = proxy;
report.canaryTargets = canary.targets;
const turnLog = await fs.open(path.join(work, 'turn.stderr.log'), 'wx');
const turnChild = spawn(
    helper, [],
    {windowsHide: true, stdio: ['pipe', 'pipe', turnLog.fd], env: browserEnvironment(process.env)});
let readyResolve, statsResolve;
const ready = new Promise(r => readyResolve = r);
const lines = readline.createInterface({input: turnChild.stdout});
lines.on('line', line => {
  try {
    const data = JSON.parse(line);
    if (data.ready)
      readyResolve(data);
    if (data.stats && statsResolve) {
      statsResolve(data);
      statsResolve = null;
    }
  } catch {
  }
});
const turn = await Promise.race(
    [ready, delay(10000).then(() => {throw Error('Owned TURN helper unavailable')})]);
const turnStats = () => new Promise(resolve => {
  statsResolve = resolve;
  turnChild.stdin.write('stats\n')
});
const turnHost = '203.0.113.17';
const turnTargets = new Map([[turnHost + ':' + turn.port, {host: '127.0.0.1', port: turn.port}]]);
const turnProxy = await controlledSocks(turnTargets);
const turnServers = [{
  urls: `turn:${turnHost}:${turn.port}?transport=tcp`,
  username: 'owned',
  credential: 'owned-fixture-only'
}];
const openRuns = [];
let namespaceCanary = null;

async function start(id, {route = proxy, feature = true, native = false, displayIP = null} = {}) {
  const folder = path.join(work, id);
  await fs.mkdir(folder);
  const profile = path.join(folder, 'profile');
  const args = [
    ...(process.argv.includes('--headed') ? [] : ['--headless=new']),
    '--user-data-dir=' + profile,
    '--remote-debugging-address=127.0.0.1',
    '--remote-debugging-port=0',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-sync',
    '--disable-quic',
    '--enable-logging=stderr',
    '--autoplay-policy=no-user-gesture-required',
    '--fingerprint=314159',
    '--fingerprint-pixel-noise=native',
    '--fingerprint-audio-mode=native',
    '--webrtc-ip-handling-policy=' + (native ? 'default' : 'disable_non_proxied_udp'),
    ...(displayIP ? ['--fingerprint-webrtc-ip=' + displayIP] : []),
    ...(route ? ['--proxy-server=' + route] : ['--no-proxy-server']),
    ...(!feature ? ['--disable-features=ShardXWebRtcDocumentLoopback'] : []),
    '--log-net-log=' + path.join(folder, 'netlog.json'),
    '--net-log-capture-mode=Default',
    'about:blank'
  ];
  const log = await fs.open(path.join(folder, 'stderr.log'), 'wx');
  const child = spawn(browser, args, {
    windowsHide: true,
    stdio: ['ignore', 'ignore', log.fd],
    env: browserEnvironment(process.env)
  });
  const row = {id, args, feature, native, profile, rootPID: child.pid};
  report.runs.push(row);
  let cdp;
  try {
    let endpoint;
    for (let i = 0; i < 200; i++) {
      try {
        const [port, ws] = (await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8'))
                               .trim()
                               .split(/\r?\n/);
        endpoint = `ws://127.0.0.1:${port}${ws}`;
        break;
      } catch {
      }
      if (child.exitCode !== null)
        throw Error('Browser exited ' + child.exitCode);
      await delay(100);
    }
    if (!endpoint)
      throw Error('Browser CDP was not ready');
    cdp = await connectCDP(endpoint, []);
    row.version = await cdp.send('Browser.getVersion');
    const owned = {
      id,
      child,
      cdp,
      row,
      async close() {
        row.cleanup = await stopOwned(child, cdp);
        await log.close();
        check(id + '/normal-close', row.cleanup.normalExit);
        await save();
      }
    };
    openRuns.push(owned);
    return owned;
  } catch (error) {
    row.error = String(error.stack);
    row.cleanup = await stopOwned(child, cdp);
    await log.close();
    throw error;
  }
}

async function page(b, url = origin) {
  const {targetId} = await b.cdp.send('Target.createTarget', {url});
  const {sessionId} = await b.cdp.send('Target.attachToTarget', {targetId, flatten: true});
  await waitPage(b, sessionId);
  return {targetId, sessionId};
}
async function waitPage(b, session) {
  for (let i = 0; i < 120; i++) {
    try {
      if (await evaluate(b.cdp, session, 'typeof rtcPair===\'function\''))
        return;
    } catch {
    }
    await delay(100);
  }
  throw Error('Owned fixture document not ready');
}
const clean = value => JSON.parse(JSON.stringify(
    value, (key, value) => key === 'credential' ? '<owned-test-credential>' : value));
async function runCase(b, id, fn, spec = {}, extraChecks = null) {
  const row = {id, run: b.id, started: Date.now(), status: 'running', spec: clean(spec)};
  report.cases.push(row);
  const before = canary.events.length, turnBefore = await turnStats();
  let p;
  try {
    p = await page(b);
    row.observed = clean(await evaluate(
        b.cdp, p.sessionId,
        `${fn}(${JSON.stringify(spec)}).catch(e=>({probeError:String(e.stack)}))`, true));
    row.status = row.observed.probeError ? 'error' : 'observed';
    check(id + '/observed', row.status === 'observed');
    if (extraChecks)
      await extraChecks(row.observed, row, p);
  } catch (error) {
    row.status = 'error';
    row.error = String(error.stack);
    check(id + '/observed', false, row.error);
  } finally {
    if (p)
      await b.cdp.send('Target.closeTarget', {targetId: p.targetId}).catch(() => {});
    await delay(300);
    row.finished = Date.now();
    row.canary = canary.events.slice(before);
    const after = await turnStats();
    row.turnDelta =
        Object.fromEntries(['accepted', 'readBytes', 'writtenBytes', 'authRequests'].map(
            k => [k, after[k] - turnBefore[k]]));
    if (id !== 'native-canary')
      check(id + '/no-real-UDP-canary', row.canary.length === 0);
    await save();
    console.log(JSON.stringify({
      case: id,
      status: row.status,
      connected: row.observed?.connected,
      bytes: row.observed?.ackBytes,
      canary: row.canary.length
    }));
  }
  return row;
}
function transfer(id, observed, size) {
  check(id + '/connected', observed.connected);
  check(id + '/payload', observed.ackBytes === size && observed.binaryErrors === 0);
  check(id + '/candidate-inputs', observed.addErrors?.length === 0);
}
function closed(id, observed) {
  check(id + '/not-connected', observed.connected === false && observed.ackBytes === 0);
}

function internalHostCandidate(candidate) {
  const tokens = (candidate.candidate ?? '').trim().split(/\s+/);
  // Chromium's address getter uses HostAsURIString(): IPv6 has brackets,
  // while the native SDP candidate token and getStats address do not.
  return candidate.type === 'host' && candidate.protocol === 'udp' &&
      tokens.length >= 8 && tokens[2] === 'udp' && tokens[6] === 'typ' &&
      tokens[7] === 'host' &&
      ((tokens[4] === '127.0.0.1' && candidate.address === '127.0.0.1') ||
       (tokens[4] === '::1' && ['::1', '[::1]'].includes(candidate.address)));
}

async function cross(b1, b2, id, config = {}) {
  const row = {id, run: b1.id, otherRun: b2.id, started: Date.now(), status: 'running'};
  report.cases.push(row);
  let a, b;
  const before = canary.events.length;
  try {
    a = await page(b1);
    b = await page(b2);
    await evaluate(b1.cdp, a.sessionId, 'rtcScopedStart(' + JSON.stringify(config) + ')');
    await evaluate(b2.cdp, b.sessionId, 'rtcScopedStart(' + JSON.stringify(config) + ')');
    const offer = await evaluate(b1.cdp, a.sessionId, 'rtcScopedOffer()');
    const answer =
        await evaluate(b2.cdp, b.sessionId, 'rtcScopedAnswer(' + JSON.stringify(offer) + ')');
    await evaluate(b1.cdp, a.sessionId, 'rtcScopedRemote(' + JSON.stringify(answer) + ')');
    let ai = 0, bi = 0, sa, sb;
    for (let i = 0; i < 40; i++) {
      sa = await evaluate(b1.cdp, a.sessionId, 'rtcScopedState()');
      sb = await evaluate(b2.cdp, b.sessionId, 'rtcScopedState()');
      if (sa.candidates.length > ai) {
        await evaluate(
            b2.cdp, b.sessionId,
            'rtcScopedCandidates(' + JSON.stringify(sa.candidates.slice(ai)) + ')');
        ai = sa.candidates.length;
      }
      if (sb.candidates.length > bi) {
        await evaluate(
            b1.cdp, a.sessionId,
            'rtcScopedCandidates(' + JSON.stringify(sb.candidates.slice(bi)) + ')');
        bi = sb.candidates.length;
      }
      await delay(100);
    }
    row.observed = {a: sa, b: sb};
    row.status = 'observed';
    check(id + '/real-candidates', ai > 0 && bi > 0);
    check(
        id + '/scope-isolated',
        !sa.open && !sb.open && sa.received.length === 0 && sb.received.length === 0);
  } catch (error) {
    row.status = 'error';
    row.error = String(error.stack);
    check(id + '/scope-isolated', false, row.error);
  } finally {
    for (const [owner, p] of [[b1, a], [b2, b]])
      if (p) {
        await evaluate(owner.cdp, p.sessionId, 'rtcScopedClose()').catch(() => {});
        await owner.cdp.send('Target.closeTarget', {targetId: p.targetId}).catch(() => {});
      }
    await delay(300);
    row.canary = canary.events.slice(before);
    row.finished = Date.now();
    check(id + '/no-real-UDP-canary', !row.canary.length);
    await save();
  }
}

try {
  const main = await start('enabled');
  await runCase(
      main, 'internal-data', 'rtcPair', {bytes: 1048576, observeMs: 500},
      o => transfer('internal-data', o, 1048576));
  await runCase(
      main, 'pooled-data', 'rtcPair', {bytes: 65536, poolSize: 1, observeMs: 200},
      o => transfer('pooled-data', o, 65536));
  await runCase(
      main, 'internal-media', 'rtcPair',
      {bytes: 262144, media: true, restart: true, pcmStages: true, observeMs: 1500}, o => {
        transfer('internal-media', o, 262144);
        check('internal-media/video', o.videoFrames >= 10);
        check(
            'internal-media/restart',
            o.restartConnected && o.ufragBefore !== o.ufragAfter &&
                o.states.every(x => x === 'connected'));
        const stages = o.pcmStages ?? [];
        check('internal-media/pcm-stage-count', stages.length === 4);
        for (const s of stages)
          check(
              'internal-media/pcm-' + s.name,
              s.stableWindows >= 3 &&
                  (s.expectedLevel === 0 ? s.rms < 0.001 :
                                           s.rms > 0.005 && s.rms < 0.1 &&
                           Math.abs(s.peakHz - s.expectedHz) <= s.binHz * 1.5));
      });
  const serverMdnsName = crypto.randomUUID() + '.local';
  report.stunMdnsProbeName = serverMdnsName;
  const iceServers = [{
    urls: [
      ...canary.targets.map(
          t => `stun:${t.host.includes(':') ? '[' + t.host + ']' : t.host}:${t.port}`),
      `stun:${serverMdnsName}:19302`
    ]
  }];
  await runCase(
      main, 'with-stun', 'rtcPair', {bytes: 65536, iceServers, observeMs: 700},
      o => transfer('with-stun', o, 65536));
  await runCase(
      main, 'relay-no-server', 'rtcPair', {relayOnly: true, timeoutMs: 2500, observeMs: 100},
      o => closed('relay-no-server', o));
  await runCase(
      main, 'relay-to-all', 'rtcPair', {promoteRelay: true, bytes: 65536, observeMs: 200}, o => {
        transfer('relay-to-all', o, 65536);
        check(
            'relay-to-all/initially-no-hosts',
            o.beforePolicyUpgrade.candidateCounts.every(x => x === 0));
        check(
            'relay-to-all/policy-applied',
            o.beforePolicyUpgrade.policies.every(x => x === 'relay') &&
                o.afterPolicyUpgrade.every(x => x === 'all'));
      });
  await runCase(
      main, 'unknown-targets', 'rtcAdversarial', {targets: canary.targets, observeMs: 2500},
      o => check(
          'unknown-targets/valid-inputs',
          o.attempts.length === canary.targets.length && o.attempts.every(x => x.accepted)));
  const mdnsName = crypto.randomUUID() + '.local';
  report.mdnsProbeName = mdnsName;
  await runCase(
      main, 'unknown-mdns', 'rtcAdversarial',
      {targets: [{host: mdnsName, port: 12345}], observeMs: 1500},
      o => check('unknown-mdns/accepted-candidate', o.attempts[0].accepted));
  await runCase(
      main, 'same-origin-iframe', 'rtcPair',
      {crossDocument: true, bytes: 1024, timeoutMs: 2500, observeMs: 100},
      o => closed('same-origin-iframe', o));
  await cross(main, main, 'separate-tab');
  const other = await start('other-profile');
  await cross(main, other, 'separate-profile');
  await other.close();
  openRuns.splice(openRuns.indexOf(other), 1);
  await runCase(
      main, 'navigation', 'rtcPair', {hold: true, bytes: 65536, observeMs: 200},
      async (o, row, p) => {
        transfer('navigation-before', o, 65536);
        await main.cdp.send('Page.navigate', {url: origin + '/?replacement=1'}, p.sessionId);
        await waitPage(main, p.sessionId);
        row.afterNavigation =
            await evaluate(main.cdp, p.sessionId, 'rtcPair({bytes:65536,observeMs:200})');
        transfer('navigation-after', row.afterNavigation, 65536);
      });
  await main.close();
  openRuns.splice(openRuns.indexOf(main), 1);
  const fault = await start('failed-proxy', {route: rejectProxy.url});
  await runCase(
      fault, 'proxy-failure', 'rtcPair', {bytes: 65536, iceServers, observeMs: 500},
      o => transfer('proxy-failure', o, 65536));
  await fault.close();
  openRuns.splice(openRuns.indexOf(fault), 1);
  const display = await start('display-ip', {displayIP: '203.0.113.200'});
  await runCase(display, 'display-ip-internal', 'rtcPair', {bytes: 65536, observeMs: 200}, o => {
    transfer('display-ip-internal', o, 65536);
    check(
        'display-ip-internal/local-addresses',
        o.peers.length === 2 && o.peers.every(
            p => p.candidates.length > 0 && p.candidates.every(internalHostCandidate)));
  });
  await display.close();
  openRuns.splice(openRuns.indexOf(display), 1);
  const relay = await start('turn-proxy', {route: turnProxy.url});
  await runCase(
      relay, 'internal-tcp-namespace', 'rtcPair', {hold: true, bytes: 1024, observeMs: 100},
      async (o, row, p) => {
        transfer('internal-tcp-namespace', o, 1024);
        await evaluate(relay.cdp, p.sessionId, 'globalThis.__namespacePairs=[__heldPair];true');
        let observed = o;
        for (let attempt = 0; attempt < 8 && !namespaceCanary; attempt++) {
          for (const c of observed.peers.flatMap(p => p.candidates)
                   .filter(c => c.address === '127.0.0.1')) {
            const port = Number(c.candidate.split(/\s+/)[5]), events = [];
            const listener = net.createServer(socket => {
              events.push({time: Date.now(), accepted: true});
              socket.destroy();
            });
            try {
              await new Promise((resolve, reject) => {
                listener.once('error', reject);
                listener.listen(port, '127.0.0.1', resolve);
              });
              namespaceCanary = {server: listener, port, events};
              break;
            } catch {
              listener.close();
            }
          }
          if (!namespaceCanary) {
            observed = await evaluate(
                relay.cdp, p.sessionId,
                'rtcPair({hold:true,bytes:1024,observeMs:50}).then(r=>{__namespacePairs.push(__heldPair);return r;})');
            transfer('internal-tcp-namespace/additional-' + attempt, observed, 1024);
          }
        }
        if (!namespaceCanary)
          throw Error('No owned TCP namespace canary could bind');
        row.tcpEndpoint = {host: '127.0.0.1', port: namespaceCanary.port};
        row.selectedPeerCandidates = observed.peers.flatMap(p => p.candidates);
        turnTargets.set('127.0.0.1:' + namespaceCanary.port, row.tcpEndpoint);
        const spec = {
          relayOnly: true,
          iceServers: [{
            urls: `turn:127.0.0.1:${namespaceCanary.port}?transport=tcp`,
            username: 'owned',
            credential: 'unused'
          }],
          observeMs: 1200
        };
        row.whileBound =
            await evaluate(relay.cdp, p.sessionId, 'rtcGather(' + JSON.stringify(spec) + ')');
        row.tcpConnectionsWhileBound = namespaceCanary.events.length;
        await evaluate(
            relay.cdp, p.sessionId,
            '__namespacePairs.forEach(x=>{x.channels.forEach(c=>c.close());x.peers.forEach(p=>p.close());});true');
        await delay(300);
        row.afterClosed =
            await evaluate(relay.cdp, p.sessionId, 'rtcGather(' + JSON.stringify(spec) + ')');
        row.tcpConnectionsAfterClosed = namespaceCanary.events.length;
        check(
            'internal-tcp-namespace/no-OS-fallback',
            row.tcpConnectionsWhileBound === 0 && row.tcpConnectionsAfterClosed === 0);
      });
  const turnRow = await runCase(
      relay, 'turn-relay', 'rtcPair',
      {relayOnly: true, iceServers: turnServers, bytes: 262144, observeMs: 300},
      o => transfer('turn-relay', o, 262144));
  check(
      'turn-relay/server-data',
      turnRow.turnDelta.readBytes > 500000 && turnRow.turnDelta.authRequests >= 2);
  const allRow = await runCase(
      relay, 'turn-all-cross-doc', 'rtcPair',
      {crossDocument: true, iceServers: turnServers, bytes: 262144, observeMs: 300},
      o => transfer('turn-all-cross-doc', o, 262144));
  check(
      'turn-all-cross-doc/server-data',
      allRow.turnDelta.readBytes > 500000 && allRow.turnDelta.authRequests >= 2);
  await runCase(
      relay, 'wrong-turn', 'rtcGather', {
        relayOnly: true,
        iceServers: [{...turnServers[0], credential: 'owned-wrong'}],
        observeMs: 2500
      },
      o => check('wrong-turn/no-relay', !o.candidates.some(c => c.type === 'relay')));
  await relay.close();
  openRuns.splice(openRuns.indexOf(relay), 1);
  const off = await start('feature-off', {feature: false, route: turnProxy.url});
  await runCase(
      off, 'feature-off', 'rtcPair', {bytes: 1024, timeoutMs: 2500, observeMs: 100},
      o => closed('feature-off', o));
  await runCase(
      off, 'native-tcp-canary', 'rtcGather', {
        relayOnly: true,
        iceServers: [{
          urls: `turn:127.0.0.1:${namespaceCanary?.port ?? 9}?transport=tcp`,
          username: 'owned',
          credential: 'unused'
        }],
        observeMs: 1500
      },
      (o, row) => {
        row.tcpConnections = namespaceCanary?.events.length ?? 0;
        check('native-tcp-canary/reachable', row.tcpConnections > 0);
      });
  await off.close();
  openRuns.splice(openRuns.indexOf(off), 1);
  const native = await start('native-control', {route: null, feature: false, native: true});
  const nativeRow =
      await runCase(native, 'native-canary', 'rtcGather', {iceServers, observeMs: 2500});
  check('native-canary/positive-control', nativeRow.canary.some(e => e.bytes >= 20));
  await native.close();
  openRuns.splice(openRuns.indexOf(native), 1);
} catch (error) {
  report.error = String(error.stack);
  check('harness-completed', false, report.error);
} finally {
  for (const b of openRuns)
    await b.close().catch(error => check(b.id + '/normal-close', false, String(error)));
  server.closeAllConnections();
  await new Promise(r => server.close(r));
  canary.close();
  await rejectProxy.close();
  await turnProxy.close();
  if (namespaceCanary)
    await new Promise(r => namespaceCanary.server.close(r));
  turnChild.stdin.end('stop\n');
  await Promise.race([new Promise(r => turnChild.once('exit', r)), delay(5000)]);
  if (turnChild.exitCode === null)
    turnChild.kill();
  lines.close();
  await turnLog.close();
  check('turn-helper/closed', turnChild.exitCode === 0);
  report.networkEvidence = [];
  for (const r of report.runs.filter(r => !r.native)) {
    const logPath = path.join(work, r.id, 'netlog.json');
    try {
      const log = JSON.parse(await fs.readFile(logPath, 'utf8'));
      const names = new Map(Object.entries(log.constants.logEventTypes).map(([k, v]) => [v, k]));
      const queries = log.events.filter(e => /DNS|RESOLVER/.test(names.get(e.type) ?? '') && [
        report.mdnsProbeName, report.stunMdnsProbeName
      ].filter(Boolean).some(n => JSON.stringify(e.params ?? {}).includes(n)));
      report.networkEvidence.push({
        run: r.id,
        netlogSHA256: hash(await fs.readFile(logPath)),
        peerMdnsQueries: queries.length
      });
      check(r.id + '/peer-mdns-not-resolved', queries.length === 0);
    } catch (error) {
      check(r.id + '/netlog-readable', false, String(error));
    }
  }
  report.exeUnchanged = hash(await fs.readFile(browser)) === report.exeSHA256;
  report.dllUnchanged = hash(await fs.readFile(dll)) === report.dllSHA256;
  check('binary-unchanged', report.exeUnchanged && report.dllUnchanged);
  report.localAddressExposure = [];
  for (const c of report.cases.filter(c => c.id !== 'native-canary')) {
    const peers = c.observed?.peers ?? (c.observed?.candidates ? [c.observed] : []);
    const shown = JSON.stringify(peers.flatMap(
        p => [...p.candidates.map(x => x.candidate), ...p.errors.map(x => x.address)]));
    for (const ip of localIPs.filter(ip => ip !== '127.0.0.1' && ip !== '::1'))
      if (shown.includes(ip))
        report.localAddressExposure.push({case: c.id, address: ip});
  }
  check('no-real-host-candidates', !report.localAddressExposure.length);
  report.finishedUtc = new Date().toISOString();
  report.status = report.checks.every(x => x.pass) ? 'passed' : 'failed';
  await save();
}
console.log(JSON.stringify({
  status: report.status,
  cases: report.cases.length,
  checks: report.checks.length,
  failed: report.checks.filter(x => !x.pass).map(x => x.id)
}));
if (report.status !== 'passed')
  process.exitCode = 1;
