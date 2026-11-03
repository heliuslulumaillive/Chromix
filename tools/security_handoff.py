"""One-shot handoff after a named donor job, without an idle hosted runner."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import re
import subprocess

try:
    from . import security_checkpoint as checkpoint
    from .security_build_record import plain
    from .security_migration import write_json
except ImportError:
    import security_checkpoint as checkpoint
    from security_build_record import plain
    from security_migration import write_json


def gh_json(endpoint):
    return json.loads(checkpoint.gh(['api', endpoint]))


def validate_plan(plan):
    if (plan.get('schema_version') != 1
            or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', plan.get('repository', ''))
            or not re.fullmatch(r'[1-9][0-9]*', str(plan.get('donor_run', '')))
            or type(plan.get('producer_job')) is not int or plan['producer_job'] <= 0
            or type(plan.get('donor_stage')) is not int or not 1 <= plan['donor_stage'] <= 99
            or not re.fullmatch(r'[a-f0-9]{40}', plan.get('donor_commit', ''))
            or not re.fullmatch(r'[a-f0-9]{40}', plan.get('target_commit', ''))
            or not re.fullmatch(r'codex/[A-Za-z0-9_.-]+', plan.get('target_branch', ''))
            or not re.fullmatch(r'[a-z0-9_-]{8,64}', plan.get('handoff_id', ''))
            or plan.get('workflow') != 'build-google-services-win155.yml'
            or plan.get('max_stages') not in (1, 2, 3, 4)
            or plan.get('retire_remaining_donor_stages') is not True):
        raise ValueError('Handoff plan must pin the authorized donor job and target branch/commit')


def readiness(repo, plan):
    validate_plan(plan)
    repository = plan['repository']
    base = f'repos/{repository}/actions'
    job = gh_json(base + '/jobs/' + str(plan['producer_job']))
    if (job.get('run_id') != int(plan['donor_run'])
            or job.get('head_sha') != plan['donor_commit']):
        raise ValueError('The named donor job has a different source/run')
    if job.get('status') != 'completed':
        return {'status': 'waiting-for-donor-job', 'job_status': job.get('status')}
    reviewed_budget_stop = job.get('conclusion') == 'failure' and plan.get('reviewed_budget_stop') is not None
    if job.get('conclusion') != 'success' and not reviewed_budget_stop:
        return {'status': 'review-required', 'reason': 'Donor job did not succeed; inspect retained failure evidence first'}
    remote = gh_json(f'repos/{repository}/git/ref/heads/{plan["target_branch"]}')
    if remote.get('object', {}).get('sha') != plan['target_commit']:
        raise ValueError('Target branch moved since this handoff was prepared')
    local = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    if local != plan['target_commit']:
        raise ValueError('Local migration verifier is not the reviewed target commit')
    if subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'], text=True).strip():
        raise ValueError('Commit the complete reviewed continuation before dispatch')
    old_repo = os.environ.get('GITHUB_REPOSITORY')
    old_run = os.environ.pop('GITHUB_RUN_ID', None)
    os.environ['GITHUB_REPOSITORY'] = repository
    try:
        preflight = checkpoint.preflight(repo, str(plan['donor_run']), 1, plan['donor_stage'], True)
    except ValueError as error:
        return {'status': 'review-required', 'reason': str(error), 'cold_fallback_started': False}
    finally:
        if old_repo is None:
            os.environ.pop('GITHUB_REPOSITORY', None)
        else:
            os.environ['GITHUB_REPOSITORY'] = old_repo
        if old_run is not None:
            os.environ['GITHUB_RUN_ID'] = old_run
    if str(preflight['run_attempt']) != str(job.get('run_attempt')):
        return {'status': 'review-required', 'reason': 'The checkpoint comes from another producer attempt'}
    stamp = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
    created = preflight['manifest_artifact'].get('created_at')
    if not created or not (stamp(job['started_at']) <= stamp(created) <= stamp(job['completed_at'])):
        return {'status': 'review-required', 'reason': 'Checkpoint was not published within the named producer job'}
    evidence = None
    if reviewed_budget_stop:
        try:
            from .security_budget_review import verify
        except ImportError:
            from security_budget_review import verify
        evidence = verify(repository, str(plan['donor_run']), job, preflight, plan['reviewed_budget_stop'])
    return {'status': 'ready', 'preflight': preflight, 'large_transfer_started': False,
            'reviewed_budget_stop': evidence}


def find_dispatch(plan, state):
    endpoint = (f'repos/{plan["repository"]}/actions/workflows/{plan["workflow"]}/runs'
                f'?branch={plan["target_branch"]}&event=workflow_dispatch&per_page=30')
    earliest = datetime.fromisoformat(state['requested_at']).astimezone(timezone.utc) - timedelta(seconds=5)
    candidates = [r for r in gh_json(endpoint)['workflow_runs']
                  if r.get('head_sha') == plan['target_commit']
                  and r.get('display_title') == 'Windows 156 / upgrade / ' + plan['handoff_id']
                  and datetime.fromisoformat(r['created_at'].replace('Z', '+00:00')) >= earliest]
    if len(candidates) > 1:
        raise ValueError('Multiple matching continuations; refuse to dispatch or cancel anything else')
    return candidates[0] if candidates else None


@contextmanager
def handoff_lock(state_path):
    """The OS releases this lock after a crash; a leftover filename is harmless."""
    path = plain(state_path.with_name(state_path.name + '.lock'))
    with path.open('a+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def handoff(repo, plan, state_path):
    """Write intent before dispatch; an unknown response is reconciled, never retried blindly."""
    validate_plan(plan)
    plain(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with handoff_lock(state_path):
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        if state and state.get('plan') != plan:
            raise ValueError('Handoff state belongs to another plan')
        if state and state.get('status') not in ('dispatched', 'dispatch-requested', 'dispatch-response-unknown'):
            raise ValueError('Unknown handoff state; refuse a possible duplicate dispatch')
        if state.get('status') == 'dispatched' and 'remaining_donor_stages_cancel_requested' in state:
            return state
        if state.get('status') == 'dispatched':
            run = gh_json(f'repos/{plan["repository"]}/actions/runs/{state["continuation_run"]}')
            if run.get('head_sha') != plan['target_commit']:
                raise ValueError('Recorded continuation source no longer matches')
        elif state.get('status') in ('dispatch-requested', 'dispatch-response-unknown'):
            run = find_dispatch(plan, state)
            if not run:
                return {**state, 'status': 'dispatch-response-unknown',
                        'reason': 'No matching run visible yet; no duplicate dispatch was attempted'}
        else:
            ready = readiness(repo, plan)
            if ready['status'] != 'ready':
                return ready
            state = {'schema_version': 1, 'status': 'dispatch-requested', 'plan': plan,
                     'requested_at': datetime.now(timezone.utc).isoformat(),
                     'donor_manifest': ready['preflight']['manifest_artifact'],
                     'donor_volumes': ready['preflight']['volume_artifacts'],
                     'donor_run_attempt': ready['preflight']['run_attempt'],
                     'reviewed_budget_stop': ready.get('reviewed_budget_stop'),
                     'producer_job_completed_before_dispatch': True}
            write_json(state_path, state)
            args = ['workflow', 'run', plan['workflow'], '--repo', plan['repository'],
                    '--ref', plan['target_branch'], '-f', 'build_variant=fingerprint-modes-win156',
                    '-f', 'operation=upgrade', '-f', 'source_run=' + str(plan['donor_run']),
                    '-f', 'source_stage=' + str(plan['donor_stage']),
                    '-f', 'handoff_id=' + plan['handoff_id'],
                    '-f', 'retain_checkpoint=true', '-f', 'max_stages=' + str(plan['max_stages'])]
            try:
                checkpoint.gh(args)
            except RuntimeError:
                state['status'] = 'dispatch-response-unknown'
                write_json(state_path, state)
                raise
            run = find_dispatch(plan, state)
            if not run:
                return state
        state.update(status='dispatched', continuation_run=run['id'],
                     continuation_url=run['html_url'], source_job_preserved=True,
                     production_activated=False)
        write_json(state_path, state)  # The new run exists before stopping old sibling work.
        donor = gh_json(f'repos/{plan["repository"]}/actions/runs/{plan["donor_run"]}')
        if (str(donor.get('id')) != str(plan['donor_run'])
                or donor.get('head_sha') != plan['donor_commit']
                or str(donor.get('run_attempt')) != str(state.get('donor_run_attempt'))):
            state['remaining_donor_stages_cancel_requested'] = False
            state['cancellation_skipped_reason'] = 'Donor identity/attempt changed; no cancellation was sent'
        elif donor.get('status') != 'completed':
            checkpoint.gh(['run', 'cancel', str(plan['donor_run']), '--repo', plan['repository']])
            state['remaining_donor_stages_cancel_requested'] = True
        else:
            state['remaining_donor_stages_cancel_requested'] = False
        write_json(state_path, state)
        return state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('operation', choices=['check', 'dispatch'])
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--state', type=Path)
    a = p.parse_args()
    plan = json.loads(plain(a.plan).read_text(encoding='utf-8'))
    if a.operation == 'dispatch':
        if not a.state:
            p.error('dispatch requires a durable --state path')
        result = handoff(a.repo, plan, a.state)
    else:
        result = readiness(a.repo, plan)
    print(json.dumps({k: v for k, v in result.items() if k not in ('preflight', 'plan')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
