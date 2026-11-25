"""Replay the staged 158 browser feature patches on a matching ungoogled source tree."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

if __package__:
    from .google_services_inputs import owned
    from .verify_history_retention_source import verify as verify_history
    from .verify_native_browser_source import body, compact, verify_shell
    from .verify_native_cookie_source import verify_implementation as verify_cookie_implementation
    from .verify_translation_init_source import verify as verify_translation
else:
    from google_services_inputs import owned
    from verify_history_retention_source import verify as verify_history
    from verify_native_browser_source import body, compact, verify_shell
    from verify_native_cookie_source import verify_implementation as verify_cookie_implementation
    from verify_translation_init_source import verify as verify_translation


def digest(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def source_bytes(tree: Path, relative: str) -> bytes | None:
    path = owned(tree, relative)
    return path.read_bytes().replace(b'\r\n', b'\n') if path.is_file() else None


def verify_signin(src: Path) -> list[str]:
    handler = 'chrome/browser/signin/dice_response_handler.cc'
    method = compact(body(owned(src, handler).read_text('utf-8'),
                          'DiceResponseHandler::DiceSigninSession::FetchTokenForAccount'))
    guard = ('if(account.no_authorization_code){handler_->OnNoAuthorizationCode(account_id);'
             'if(account.account_info.gaia_id==signin_info_.GetInitiator()->account_info.gaia_id){'
             'delegate_->HandleNoAuthorizationCode(account.account_info.email);}return;}')
    if guard not in method or method.index(guard) > method.index('CheckTokenBindingEligibility('):
        raise ValueError('The 158 error path must preserve account attribution and the initiator-only notification')
    sync = 'chrome/browser/ui/webui/signin/history_sync_optin_helper.cc'
    flow = compact(body(owned(src, sync).read_text('utf-8'), 'HistorySyncOptinHelper::ShowHistorySyncOptinScreen'))
    consent = ('if(result==signin_util::ShouldShowHistorySyncOptinResult::kShow&&'
               'command_line->HasSwitch("shardx-auto-enable-history-sync")&&'
               'command_line->GetSwitchValueASCII("shardx-auto-enable-history-sync")!="false"&&'
               'IdentityManagerFactory::GetForProfile(profile())->GetPrimaryAccountId(signin::ConsentLevel::kSignin)'
               '==account_info_.account_id){signin_util::EnableHistorySync(GetSyncService(profile()));'
               'result=signin_util::ShouldShowHistorySyncOptinScreen(*profile_.get());}')
    if consent not in flow or flow.index(consent) > flow.index('RecordMetricsForHistorySyncStarted('):
        raise ValueError('History sync must preserve native eligibility, account identity and explicit opt-out')
    return [handler, sync]


def verify(repo: Path, baseline: Path, patch_bin: Path) -> dict:
    manifest_path = owned(repo, 'build/windows158-browser-feature-port.json')
    manifest = json.loads(manifest_path.read_bytes())
    release = json.loads(owned(repo, 'build/lite-windows157.json').read_bytes())
    release_digests = {p['path']: p['sha256'] for p in release['patches']}
    for name, expected in release_digests.items():
        if digest(owned(repo, name).read_bytes()) != expected:
            raise ValueError('Changed 157 input: ' + name)
    groups = manifest['groups']
    connections = manifest['featureConnections']
    patches = [*groups, connections]
    file_rows = [f for p in patches for f in p['files']]
    if len({f['path'] for f in file_rows}) != len(file_rows):
        raise ValueError('The staged feature patches have overlapping sources')
    for group in groups:
        if release_digests.get(group['releasePatch']) != group['releaseSha256']:
            raise ValueError('Unexpected release lineage: ' + group['path'])
    for row in connections['files']:
        if release_digests.get(row['from']) != row['releaseSha256']:
            raise ValueError('Unexpected connection source: ' + row['path'])

    options = ['-p1', '--batch', '--fuzz=0', '--binary', '--get=0', '--no-backup-if-mismatch', '--reject-file=-']
    with tempfile.TemporaryDirectory(prefix='shardx-158-browser-features-') as tmp:
        tree = Path(tmp)
        for row in file_rows:
            data = source_bytes(baseline, row['path'])
            if digest(data) != row['before']:
                raise ValueError('Wrong upstream source: ' + row['path'])
            if data is not None:
                dest = owned(tree, row['path'])
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
        inputs = []
        for group in patches:
            payload = owned(repo, group['path']).read_bytes()
            targets = re.findall(rb'^diff --git a/(\S+) b/\S+$', payload, re.M)
            expected = [row['path'] for row in group['files'] if row['before'] != row['after']]
            if digest(payload) != group['sha256'] or [v.decode() for v in targets] != expected:
                raise ValueError('Patch contents differ from the feature manifest: ' + group['path'])
            inputs.append(payload)
            subprocess.run([str(patch_bin), *options, '--forward'], input=payload, cwd=tree,
                           check=True, stdout=subprocess.DEVNULL)
        for row in file_rows:
            if digest(source_bytes(tree, row['path'])) != row['after']:
                raise ValueError('Wrong patched source: ' + row['path'])
        checks = {
            'cookieImplementation': verify_cookie_implementation(tree),
            'historyRetention': verify_history(tree),
            'translation': verify_translation(tree),
            'windowsShellAndAvatar': verify_shell(tree, account_overlay=True),
            'signinAndHistorySync': verify_signin(tree),
        }
        for data in reversed(inputs):
            subprocess.run([str(patch_bin), *options, '--reverse'], input=data, cwd=tree,
                           check=True, stdout=subprocess.DEVNULL)
        for row in file_rows:
            if digest(source_bytes(tree, row['path'])) != row['before']:
                raise ValueError('Reverse replay did not recover upstream: ' + row['path'])
    return {'status': 'source-verified', 'releaseSource': manifest['releaseSource'], 'chromiumVersion': manifest['chromiumVersion'],
            'manifestSha256': digest(manifest_path.read_bytes()), 'releaseInputsVerified': len(release_digests),
            'featureGroups': len(groups), 'connectionFiles': len(connections['files']), 'forwardAndReverseFiles': len(file_rows),
            'sourceChecks': checks, 'compiled': False, 'runtimeAcceptance': 'pending'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--baseline', type=Path, required=True, help='Ungoogled Windows 158 after upstream overlay and pruning')
    parser.add_argument('--patch-bin', type=Path, default=Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'Git/usr/bin/patch.exe')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = verify(args.repo.resolve(), args.baseline.resolve(), args.patch_bin.resolve())
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.report:
        if args.report.exists():
            if args.report.read_bytes() != text.encode():
                raise ValueError('Use a fresh report path')
        else:
            with args.report.open('x', encoding='utf-8', newline='\n') as file:
                file.write(text)
    print(f"Verified {result['featureGroups']} feature groups and {result['forwardAndReverseFiles']} source paths")


if __name__ == '__main__':
    main()
