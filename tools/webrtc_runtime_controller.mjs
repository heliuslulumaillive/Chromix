import {spawn} from 'node:child_process';
import path from 'node:path';
const delay = ms => new Promise(r => setTimeout(r, ms));
const errorText = e => String(e?.message || e).slice(0, 1500);
async function deadline(promise, ms, message) {
  let timer;
  try {
    return await Promise.race([
      promise, new Promise((_, reject) => timer = setTimeout(() => reject(Error(message)), ms))
    ]);
  } finally {
    clearTimeout(timer);
  }
}
async function connectCDP(endpoint, trace) {
  const socket = new WebSocket(endpoint), pending = new Map();
  let next = 1;
  await deadline(
      new Promise((resolve, reject) => {
        socket.addEventListener('open', resolve, {once: true});
        socket.addEventListener(
            'error', () => reject(new Error('Owned CDP connection failed')), {once: true});
      }),
      10000, 'CDP connection deadline');
  socket.addEventListener('message', event => {
    const packet = JSON.parse(String(event.data));
    if (pending.has(packet.id)) {
      const task = pending.get(packet.id);
      pending.delete(packet.id);
      packet.error ? task.reject(new Error(packet.error.message)) : task.resolve(packet.result);
    }
  });
  socket.addEventListener('close', () => {
    for (const task of pending.values())
      task.reject(new Error('CDP closed'));
    pending.clear();
  });
  return {
    async send(method, params = {}, sessionId) {
      const id = next++;
      trace.push({method, parameterKeys: Object.keys(params), attached: !!sessionId});
      try {
        return await deadline(
            new Promise((resolve, reject) => {
              pending.set(id, {resolve, reject});
              socket.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
            }),
            method === 'Runtime.evaluate' ? 75000 : 10000, `CDP deadline: ${method}`);
      } finally {
        pending.delete(id);
      }
    },
    close() {
      socket.close();
    },
  };
}
async function evaluate(cdp, session, expression, userGesture = false) {
  const reply = await cdp.send(
      'Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true, userGesture},
      session);
  if (reply.exceptionDetails)
    throw new Error(reply.exceptionDetails.text || 'Owned page evaluation failed');
  return reply.result?.value;
}
export function browserEnvironment(source) {
  return Object.fromEntries(Object.entries(source).filter(
      ([key]) => !/^(GH_|GITHUB_|GOOGLE_|CLOAK|OPENAI_|ANTHROPIC_|CODEX_|AWS_|AZURE_)/i.test(key) &&
          !/(TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL)/i.test(key)));
}
export async function ownedWindowsProcesses(rootPID, known = null) {
  if (process.platform !== 'win32')
    throw new Error('Windows process verification required');
  if (!Number.isSafeInteger(rootPID) || rootPID <= 0)
    throw new Error('Invalid owned root PID');
  const payload = Buffer.from(JSON.stringify({rootPID, known}), 'utf8').toString('base64');
  const script = `
$ErrorActionPreference='Stop'
$request=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${
      payload}')) | ConvertFrom-Json
$all=@(Get-CimInstance Win32_Process -Property ProcessId,ParentProcessId,CreationDate | ForEach-Object {
  if ($null -ne $_.CreationDate) {
    [PSCustomObject]@{pid=[int]$_.ProcessId;parent=[int]$_.ParentProcessId;created=$_.CreationDate.ToUniversalTime().ToString('o')}
  }
})
$owned=@()
if ($null -ne $request.known) {
  foreach ($item in @($request.known)) {
    $owned+=@($all | Where-Object { $_.pid -eq $item.pid -and $_.created -ceq $item.created })
  }
} else {
  $owned=@($all | Where-Object { $_.pid -eq $request.rootPID })
  for ($i=0; $i -lt $owned.Count; $i++) {
    $parent=$owned[$i]
    $owned+=@($all | Where-Object {
      $_.parent -eq $parent.pid -and $_.pid -notin @($owned.pid) -and $_.created -cge $parent.created
    })
  }
}
ConvertTo-Json -InputObject @($owned) -Compress
`;
  const executable = path.join(
      process.env.SystemRoot || 'C:\\Windows', 'System32/WindowsPowerShell/v1.0/powershell.exe');
  const child = spawn(
      executable,
      [
        '-NoProfile', '-NonInteractive', '-EncodedCommand',
        Buffer.from(script, 'utf16le').toString('base64')
      ],
      {windowsHide: true, env: browserEnvironment(process.env), stdio: ['ignore', 'pipe', 'pipe']});
  let output = '', error = '';
  child.stdout.on('data', data => {
    output += data;
  });
  child.stderr.on('data', data => {
    error += data;
  });
  try {
    const code = await deadline(
        new Promise((resolve, reject) => {
          child.once('error', reject);
          child.once('close', resolve);
        }),
        10000, 'Owned process inventory deadline');
    if (code !== 0)
      throw new Error('Owned process inventory unavailable: ' + error.slice(-1000));
    const records = JSON.parse(output.replace(/^\uFEFF/, '').trim());
    if (!Array.isArray(records) ||
        records.some(r => !Number.isSafeInteger(r.pid) || typeof r.created !== 'string'))
      throw new Error('Invalid owned process inventory');
    return records;
  } finally {
    if (child.exitCode === null)
      child.kill();
  }  // Only the inventory helper itself.
}
async function stopOwned(child, cdp) {
  let closeSent = false, forced = false, before = [], remaining = [], treeError = null;
  if (child.pid && child.exitCode === null) {
    try {
      before = await ownedWindowsProcesses(child.pid);
    } catch (error) {
      treeError = errorText(error);
    }
  }
  if (cdp) {
    closeSent = true;
    await cdp.send('Browser.close').catch(() => {});
  }
  for (let i = 0; i < 100 && child.exitCode === null; i++)
    await delay(100);
  if (child.exitCode === null && child.pid) {
    forced = true;
    // An error cleanup is limited to this exact spawn's PID tree. Never by name.
    const killer = spawn(
        'taskkill.exe', ['/PID', String(child.pid), '/T', '/F'],
        {windowsHide: true, stdio: 'ignore', env: browserEnvironment(process.env)});
    await deadline(
        new Promise(resolve => {
          killer.once('exit', resolve);
          killer.once('error', resolve);
        }),
        10000, 'Owned cleanup deadline')
        .catch(() => {});
    for (let i = 0; i < 30 && child.exitCode === null; i++)
      await delay(100);
  }
  if (before.length) {
    try {
      remaining = await ownedWindowsProcesses(child.pid, before);
      if (remaining.length) {
        await delay(500);
        remaining = await ownedWindowsProcesses(child.pid, before);
      }
      for (const ownedEntry of remaining) {
        forced = true;
        // These PIDs were descendants of this spawn and their creation time
        // still matches. A reused PID is never treated as an owned process.
        const fresh = await ownedWindowsProcesses(child.pid, [ownedEntry]);
        if (!fresh.length)
          continue;
        const killer = spawn(
            'taskkill.exe', ['/PID', String(ownedEntry.pid), '/T', '/F'],
            {windowsHide: true, stdio: 'ignore', env: browserEnvironment(process.env)});
        await deadline(
            new Promise(resolve => {
              killer.once('close', resolve);
              killer.once('error', resolve);
            }),
            10000, 'Owned descendant cleanup deadline');
      }
      if (remaining.length)
        remaining = await ownedWindowsProcesses(child.pid, before);
    } catch (error) {
      treeError = errorText(error);
    }
  }
  cdp?.close();
  return {
    rootPID: child.pid,
    closeSent,
    forced,
    exitCode: child.exitCode,
    owned_tree_before_close: before,
    known_owned_processes_remaining: remaining,
    process_tree_error: treeError,
    normalExit: closeSent && !forced && child.exitCode === 0 && before.length > 0 && !treeError &&
        remaining.length === 0
  };
}


export {connectCDP, evaluate, stopOwned};
