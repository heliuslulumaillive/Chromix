"""Admit only a specifically reviewed misclassified compile-budget checkpoint."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import tempfile

try:
    from . import security_checkpoint as checkpoint
except ImportError:
    import security_checkpoint as checkpoint

FILES = ('stage-state.json', '014-ninja.json', '014-ninja.log', 'build-inputs.json')
FAILED_STEP = 'Prepare or restore, compile and verify Windows 156'


def assess(job, manifest, files):
    """Validate evidence semantics independently of the user-visible job badge."""
    if (job.get('status') != 'completed' or job.get('conclusion') != 'failure'
            or str(job.get('run_id')) != str(manifest.get('run_id'))
            or str(job.get('run_attempt')) != str(manifest.get('run_attempt'))
            or job.get('head_sha') != manifest.get('source_commit')):
        raise ValueError('Reviewed budget evidence belongs to another job/run/attempt')
    failures = [s.get('name') for s in job.get('steps', []) if s.get('conclusion') == 'failure']
    if failures != [FAILED_STEP]:
        raise ValueError('The donor has an unreviewed failing step')
    state, command, build = (json.loads(files[n].decode('utf-8-sig'))
                             for n in ('stage-state.json', '014-ninja.json', 'build-inputs.json'))
    if (state.get('stage') != manifest.get('stage') or state.get('status') != 'budget-stop'
            or state.get('error') != 'SECURITY_BUDGET_STOP'
            or state.get('resume_safe') is not True or manifest.get('resume_safe') is not True
            or state.get('finished') is not False):
        raise ValueError('The donor did not retain a safe deliberate budget stop')
    if (command.get('timed_out') is not True or command.get('exit_code') is not None
            or command.get('cleanup_errors') != []
            or type(command.get('seconds')) not in (int, float) or not 0 < command['seconds'] <= 18001):
        raise ValueError('The compiler command did not end as a cleanly owned timeout')
    for key in ('chromium_version', 'core_commit', 'windows_commit', 'source_commit',
                'run_id', 'recipe_sha256', 'source_receipt_sha256'):
        if str(build.get(key)) != str(manifest.get(key)):
            raise ValueError('Build inputs differ from the checkpoint: ' + key)
    if build.get('gn', {}).get('sha256') != manifest.get('gn_args_sha256'):
        raise ValueError('Compiled GN inputs differ from the checkpoint')
    log = files['014-ninja.log'].decode('utf-8', errors='strict')
    if re.search(r'(?im)^FAILED:|fatal error|\berror:|\berror (?:C|LNK)\d+|ninja: build stopped', log):
        raise ValueError('Compiler errors cannot be approved as a mere budget stop')
    progress = re.findall(r'(?m)^\[(\d+)/(\d+)\]', log)
    if not progress or not 0 < int(progress[-1][0]) <= int(progress[-1][1]):
        raise ValueError('Missing real compiler progress in the reviewed evidence')
    return {'status': 'reviewed-budget-stop', 'completed_actions': int(progress[-1][0]),
            'total_actions_at_stop': int(progress[-1][1]), 'native_build_accepted': False}


def verify(repository, source_run, job, preflight, review):
    if (not isinstance(review, dict) or review.get('version') != 1
            or not isinstance(review.get('files_sha256'), dict)
            or set(review.get('files_sha256', {})) != set(FILES)
            or any(not re.fullmatch(r'[a-f0-9]{64}', str(v)) for v in review['files_sha256'].values())):
        raise ValueError('Missing explicit reviewed budget evidence identities')
    expected = review.get('artifact', {})
    artifact_name = f'security-win156-evidence-s{preflight["stage"]}'
    if not isinstance(expected, dict) or expected.get('name') != artifact_name:
        raise ValueError('Unexpected reviewed evidence artifact')
    base = f'repos/{repository}/actions/runs/{source_run}'

    def verify_artifact():
        matches = [a for a in checkpoint.artifact_inventory(base)
                   if a.get('name') == artifact_name and not a.get('expired')]
        if len(matches) != 1 or checkpoint.artifact_identity(matches[0]) != expected:
            raise ValueError('The reviewed evidence artifact changed or expired')

    verify_artifact()
    with tempfile.TemporaryDirectory(prefix='reviewed-budget-') as directory:
        root = Path(directory)
        checkpoint.gh(['run', 'download', source_run, '--repo', repository, '--name', artifact_name, '--dir', str(root)])
        files = {}
        for name in FILES:
            path = checkpoint.plain(root / name)
            if not path.is_file() or path.stat().st_size > 16 * 1024**2:
                raise ValueError('Reviewed evidence file missing or oversized: ' + name)
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != review['files_sha256'][name]:
                raise ValueError('Reviewed evidence content changed: ' + name)
            files[name] = content
        result = assess(job, preflight['manifest'], files)
    verify_artifact()
    return {**result, 'evidence_artifact_id': expected['id']}
