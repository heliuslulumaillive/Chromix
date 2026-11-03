"""Bound a CI command and all its owned children using the existing Windows job helper."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
try:
    from .fingerprint_subprocess import WindowsJob
except ImportError:
    from fingerprint_subprocess import WindowsJob


def github_read_command_allowed(command):
    """Only canonical invocations of the reviewed read-only transfer helpers."""
    if (len(command) < 5 or not all(isinstance(part, str) for part in command)
            or command[1:4] != ['-B', '-X', 'utf8']):
        return False
    try:
        if Path(command[0]).resolve() != Path(sys.executable).resolve():
            return False
        script = Path(command[4]).resolve()
        directory = Path(__file__).resolve().parent
        if script == directory / 'fetch_upstream_cache.py':
            return True
        return (script == directory / 'security_checkpoint.py' and len(command) >= 6
                and command[5] in ('preflight', 'restore'))
    except (OSError, ValueError):
        return False


def run(command, cwd, timeout, log, github_read_token=False):
    if os.name != 'nt' or not 0 < timeout <= 21000:
        raise ValueError('A bounded Windows command is required')
    if github_read_token and not github_read_command_allowed(command):
        raise ValueError('GitHub read token is restricted to reviewed source acquisition commands')
    started = time.monotonic()
    result = {'timed_out': False, 'exit_code': None, 'cleanup_errors': []}
    job = WindowsJob()
    process = None
    try:
        with log.open('xb') as output:
            gate = ('import subprocess,sys;sys.exit(subprocess.call(sys.argv[1:], '
                    'stdin=subprocess.DEVNULL) if sys.stdin.buffer.read(1)==b"1" else 125)')
            env = {k: v for k, v in os.environ.items() if not any(
                marker in k.upper() for marker in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY', 'CREDENTIAL'))}
            if github_read_token and os.environ.get('GH_TOKEN'):
                env['GH_TOKEN'] = os.environ['GH_TOKEN']
            process = subprocess.Popen([sys.executable, '-c', gate, *command], cwd=cwd,
                env=env, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW)
            job.assign(process)
            process.stdin.write(b'1'); process.stdin.close()
            next_status = 0
            while process.poll() is None:
                elapsed = time.monotonic() - started
                if elapsed >= timeout:
                    result['timed_out'] = True
                    break
                if elapsed >= next_status:
                    print(f'Owned build command running: {int(elapsed)} s; log {log.name}', flush=True)
                    next_status = elapsed + 60
                time.sleep(0.2)
            if not result['timed_out']:
                result['exit_code'] = process.returncode
    finally:
        try:
            job.terminate()
        except OSError as error:
            result['cleanup_errors'].append(str(error))
        job.close()
        if process:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            process.wait(timeout=15)
    result['seconds'] = round(time.monotonic() - started, 3)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cwd', required=True)
    p.add_argument('--timeout', type=int, required=True)
    p.add_argument('--log', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--github-read-token', action='store_true', help='For reviewed upstream/checkpoint acquisition only')
    p.add_argument('command', nargs=argparse.REMAINDER)
    a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ['--'] else a.command
    if not command:
        p.error('command required')
    if a.github_read_token and not github_read_command_allowed(command):
        p.error('GitHub read token may only be forwarded to reviewed source acquisition commands')
    a.log.parent.mkdir(parents=True, exist_ok=True)
    result = run(command, a.cwd, a.timeout, a.log, a.github_read_token)
    a.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    raise SystemExit(124 if result['timed_out'] else (result['exit_code'] or bool(result['cleanup_errors'])))


if __name__ == '__main__':
    main()
