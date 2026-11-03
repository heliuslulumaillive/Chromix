#!/usr/bin/env python3
"""Describe restored Ninja state and pending work without compiling anything."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import uuid


def run_logged(command, *, env, timeout, log_path, cwd):
    # The existing helper assigns its gated child to a kill-on-close Job before
    # releasing it. Timeout cleanup includes GN, not only its Ninja parent.
    try:
        from .fingerprint_subprocess import run_command
    except ImportError:
        from fingerprint_subprocess import run_command
    receipt = run_command(command, timeout=timeout, log=log_path, cwd=cwd, env=env)
    output = log_path.read_text(encoding='utf-8', errors='replace')
    return SimpleNamespace(returncode=receipt['exit_code'], stdout=output, stderr='',
                           timed_out=receipt['timed_out'], receipt=receipt)


def manifest_recovery_reason(previous):
    if previous.get('status') != 'failed':
        return None
    if previous.get('failure_phase') in ('manifest-refresh', 'manifest-recovery'):
        return 'previous_manifest_command_failed'
    # The first variant used subprocess.run and lost its partial output on
    # timeout. Its checkpoint can retain incomplete GN metadata, so regenerate
    # once even if build.ninja happens to look newer than all inputs.
    error = str(previous.get('error', ''))
    if (previous.get('compiler_invoked') is False and
            'build.ninja' in error and 'timed out' in error):
        return 'legacy_manifest_timeout_without_tree_receipt'
    return None


def summarize_log(path):
    digest, outputs, malformed = hashlib.sha256(), set(), 0
    with path.open('rb') as stream:
        header = stream.readline()
        digest.update(header)
        if not re.fullmatch(rb'# ninja log v\d+\r?\n?', header):
            raise ValueError('Missing or invalid restored Ninja log header')
        for line in stream:
            digest.update(line)
            fields = line.rstrip(b'\r\n').split(b'\t')
            if len(fields) == 5 and all(field.isdigit() for field in fields[:2]):
                outputs.add(fields[3])
            elif line.strip():
                malformed += 1
    return {'log_version': header.decode().strip(), 'log_sha256': digest.hexdigest(),
            'cached_log_outputs': len(outputs), 'unparsed_log_entries': malformed}


def pending_count(text):
    progress = re.findall(r'\[(\d+)/(\d+)\]\s*([^\r\n]*)', text)
    if progress:
        if all('Regenerating ninja files' in description for _, _, description in progress):
            return None  # A dry run cannot execute GN and observe the new graph.
        return max(int(total) for _, total, _ in progress)
    if 'ninja: no work to do.' in text:
        return 0
    raise ValueError('Dry run did not report the pending build work')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ninja', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--target', action='append', default=[])
    parser.add_argument('--refresh-manifest', action='store_true')
    parser.add_argument('--ensure-manifest', action='store_true',
                        help='Ask Ninja to update build.ninja before planning; GN runs only for dirty inputs')
    parser.add_argument('--manifest-timeout-seconds', type=int, default=240)
    parser.add_argument('--plan-timeout-seconds', type=int, default=240)
    parser.add_argument('--previous-audit', type=Path,
                        help='Restored checkpoint audit; recover only a confirmed failed metadata phase')
    args = parser.parse_args()
    if not all(0 < value <= 7200 for value in
               (args.manifest_timeout_seconds, args.plan_timeout_seconds)):
        parser.error('audit timeouts must be in 1..7200 seconds')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    result = {'schema_version': 1, 'compiler_invoked': False, 'status': 'failed',
              'safe_to_snapshot': True, 'commands': []}
    command_dir = args.report.parent / (args.report.stem + '-commands-' + uuid.uuid4().hex[:12])
    command_dir.mkdir()
    phase = 'read-cache'
    try:
        previous = None
        if args.previous_audit:
            data = args.previous_audit.read_bytes()
            previous = json.loads(data)
            if not isinstance(previous, dict):
                raise ValueError('Previous audit must be an object')
            (command_dir / 'previous-audit.json').write_bytes(data)
            result['previous_audit_sha256'] = hashlib.sha256(data).hexdigest()
        recovery = manifest_recovery_reason(previous or {})
        result.update(summarize_log(args.out / '.ninja_log'))
        env = {**os.environ, 'NINJA_STATUS': '[%f/%t] '}
        targets = args.target or ['chrome']
        result['targets'] = targets
        result['command_log_directory'] = str(command_dir)

        def execute(command, label, timeout):
            nonlocal phase
            phase = label
            log_path = command_dir / (label + '.log')
            result['safe_to_snapshot'] = False
            print(json.dumps({'audit_phase': label, 'timeout_seconds': timeout,
                              'log': str(log_path)}), flush=True)
            run = run_logged(command, env=env, timeout=timeout, log_path=log_path,
                             cwd=args.out.resolve().parent.parent)
            receipt = getattr(run, 'receipt', {})
            result['commands'].append({'phase': label, 'timeout_seconds': timeout,
                                       'log': str(log_path), **receipt})
            result['safe_to_snapshot'] = not receipt.get('cleanup_errors')
            if not result['safe_to_snapshot']:
                raise ValueError('Audit command tree did not terminate cleanly')
            if getattr(run, 'timed_out', False):
                raise ValueError(f'{label} exceeded its {timeout}-second budget; partial log preserved')
            return run

        if args.ensure_manifest:
            # New targets can be absent from the inherited graph, so refresh
            # this single metadata target before asking for their dry run.
            # Ninja checks build.ninja's actual dependencies and does nothing
            # when the restored GN inputs retain their timestamps.
            if recovery:
                gn = args.out / 'gn.exe'
                if not gn.is_file():
                    raise ValueError('Interrupted metadata requires the checkpoint GN executable')
                result['manifest_recovery_reason'] = recovery
                refresh = execute([str(gn), 'gen', str(args.out), '--fail-on-unused-args'],
                                  'manifest-recovery', args.manifest_timeout_seconds)
            else:
                refresh = execute([str(args.ninja), '-C', str(args.out), 'build.ninja'],
                                  'manifest-refresh', args.manifest_timeout_seconds)
            if refresh.returncode:
                raise ValueError('Ninja manifest refresh failed')
            result.update(manifest_checked=True,
                          manifest_refreshed=bool(recovery) or 'Regenerating ninja files' in refresh.stdout)
        for attempt in range(2):
            plan = execute([str(args.ninja), '-C', str(args.out), '-n', *targets],
                           f'plan-{attempt + 1}', args.plan_timeout_seconds)
            if plan.returncode:
                raise ValueError('Ninja could not plan the restored tree; see dry-run log')
            pending = pending_count(plan.stdout)
            if args.ensure_manifest and pending is None:
                raise ValueError('Ninja manifest is still dirty after the metadata check')
            if pending is not None or not args.refresh_manifest:
                break
            if attempt:
                raise ValueError('Ninja manifest is still dirty after a metadata-only refresh')
            # This target only refreshes GN/Ninja metadata. Never invoke a
            # browser or test compilation merely to measure pending work.
            refresh = execute([str(args.ninja), '-C', str(args.out), 'build.ninja'],
                              'manifest-refresh', args.manifest_timeout_seconds)
            if refresh.returncode:
                raise ValueError('Ninja manifest refresh failed')
            result['manifest_refreshed'] = True
        result['pending_compile_actions'] = len(re.findall(r'\]\s+(?:CXX|CC|OBJCXX|OBJC)\s', plan.stdout))
        result.update(status='passed' if pending is not None else 'manifest_regeneration_required', pending_actions=pending,
                      scope='Cached log output count and current pending actions; not a cache-hit percentage')
    except Exception as error:
        result['failure_phase'] = phase
        result['error'] = str(error)
    args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in ('passed', 'manifest_regeneration_required') else 1


if __name__ == '__main__':
    raise SystemExit(main())
