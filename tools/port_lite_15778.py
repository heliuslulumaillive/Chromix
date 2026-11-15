"""Port selected product sources through authenticated, cached upstream bytes.

Fetches only reviewed product inputs, never full Chromium archives or objects.
The independent hosted lane still restores the exact .78 source/object snapshot.
"""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from tools.build_google_services_patch import (sections, patch_series,
    webrtc_revision_from_deps, v8_revision_from_deps, search_engines_revision_from_deps)
from tools.google_services_inputs import plain, write_if_changed
from tools.prepare_google_services import restore_domains

VERSIONS = {
    'old': ('157.0.8103.38', 'a733b5afbb3ba618844ff8876b89ad1a774b2f18',
            'de09e0c70c6bc1ac83d4ecdfd0e82c7e250c9a3c', 'f85bc55429c37b1c748b676b18fe79b38eee3207'),
    'new': ('157.0.8103.78', '4b3d0dabb8250b6f152d2d85d79e7706e309d087',
            '8c886dffc6ade39d8f7df5d64ace5a8824b455e1', '20e9a348fc616bd73a366fbd5d8881613d6c5cb0'),
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fetch(url, cache, binary=False):
    key = digest(url.encode())
    receipt = cache / (key + '.json')
    if receipt.exists():
        record = json.loads(receipt.read_bytes())
        assert record['url'] == url
        data = (cache / (record['sha256'] + '.blob')).read_bytes()
        assert len(data) == record['size'] and digest(data) == record['sha256']
        return data
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'ShardX-pinned-source-port'}), timeout=45) as response:
                data = response.read(32 * 1024 * 1024 + 1)
            assert len(data) <= 32 * 1024 * 1024
            if not binary and '?format=TEXT' in url:
                data = base64.b64decode(data, validate=True)
            break
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise
            if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(2 ** attempt)
        except (TimeoutError, urllib.error.URLError):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    record = {'url': url, 'sha256': digest(data), 'size': len(data)}
    write_if_changed(cache / (record['sha256'] + '.blob'), data)
    write_if_changed(receipt, (json.dumps(record, sort_keys=True) + '\n').encode())
    return data


def tooling(repository, commit, output, cache):
    data = fetch(f'https://codeload.github.com/ungoogled-software/{repository}/tar.gz/{commit}', cache, True)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        for entry in archive:
            parts = Path(entry.name).parts[1:]
            if not parts or not entry.isfile():
                continue
            assert '..' not in parts and not Path(*parts).is_absolute()
            write_if_changed(output.joinpath(*parts), archive.extractfile(entry).read())
    return {'repository': repository, 'commit': commit, 'archiveSHA256': digest(data)}


def pruned(core, windows):
    names = set()
    for file in (core / 'pruning.list', windows / 'pruning.list'):
        names.update(line.split('#', 1)[0].strip() for line in file.read_text().splitlines() if line.split('#', 1)[0].strip())
    return names


def prepare(repo, work, cache, patch_bin):
    manifest = json.loads((repo / 'build/lite-windows157.json').read_bytes())
    rows = manifest['source_files']
    report = {'versions': {}, 'oldManifestSHA256': digest((repo / 'build/lite-windows157.json').read_bytes())}
    for label, (version, chromium, core_commit, windows_commit) in VERSIONS.items():
        root = work / label
        core, windows = root / 'tooling/core', root / 'tooling/windows'
        pins = [tooling('ungoogled-chromium', core_commit, core, cache),
                tooling('ungoogled-chromium-windows', windows_commit, windows, cache)]
        deps = fetch(f'https://chromium.googlesource.com/chromium/src/+/{chromium}/DEPS?format=TEXT', cache)
        external = [
            ('v8', 'https://chromium.googlesource.com/v8/v8', v8_revision_from_deps(deps)),
            ('third_party/webrtc', 'https://webrtc.googlesource.com/src', webrtc_revision_from_deps(deps)),
            ('third_party/search_engines_data/resources', 'https://chromium.googlesource.com/external/search_engines_data.git', search_engines_revision_from_deps(deps)),
        ]
        prune = pruned(core, windows)
        native = {r['path'] for r in rows if r['before_sha256'] is not None or r['path'] in prune}
        created = {name for tree in (core, windows) for _, patch in patch_series(tree)
                   for name, added, _ in sections(patch) if added}
        pristine, baseline = root / 'pristine', root / 'baseline'
        def source(name):
            repository, revision, relative = 'https://chromium.googlesource.com/chromium/src', chromium, name
            for prefix, target, commit in external:
                if name.startswith(prefix + '/'):
                    repository, revision, relative = target, commit, name[len(prefix) + 1:]
                    break
            url = f'{repository}/+/{revision}/{relative}?format=TEXT'
            # Keep validated cached bytes. Official GitHub mirrors avoid a large
            # burst of Gitiles requests when only selected source files are needed.
            if not (cache / (digest(url.encode()) + '.json')).exists():
                if repository == 'https://chromium.googlesource.com/chromium/src':
                    url = f'https://raw.githubusercontent.com/chromium/chromium/{revision}/{relative}'
                elif repository == 'https://chromium.googlesource.com/v8/v8':
                    url = f'https://raw.githubusercontent.com/v8/v8/{revision}/{relative}'
            data = fetch(url, cache)
            write_if_changed(pristine / name, data)
            return {'path': name, 'sha256': digest(data), 'repository': repository, 'revision': revision}
        with ThreadPoolExecutor(max_workers=4) as pool:
            sources = []
            for record in pool.map(source, sorted(native - created)):
                sources.append(record)
                if len(sources) % 40 == 0:
                    print(label, 'verified source inputs', len(sources), '/', len(native - created), flush=True)
        marker = root / 'baseline-ready.json'
        if not marker.exists():
            assert not baseline.exists(), 'Partial baseline requires inspection; refusing overwrite'
            baseline.mkdir(parents=True)
            for record in sources:
                name = record['path']
                if name not in prune:
                    write_if_changed(baseline / name, (pristine / name).read_bytes())
            for tree in (core, windows):
                for name, patch in patch_series(tree):
                    selected = ''.join(section for path, _, section in sections(patch) if path in native)
                    if not selected:
                        continue
                    current = root / 'selected.patch'
                    current.write_text(selected, encoding='utf-8', newline='\n')
                    result = subprocess.run([patch_bin, '-p1', '--batch', '--forward', '--fuzz=0', '--no-backup-if-mismatch', '-i', str(current)], cwd=baseline, capture_output=True, text=True)
                    if result.returncode:
                        raise RuntimeError(label + '/' + name + '\n' + result.stdout + result.stderr)
            if label == 'old':
                for row in rows:
                    path = baseline / row['path']
                    actual = digest(path.read_bytes().replace(b'\r\n', b'\n')) if path.is_file() else None
                    assert actual == row['before_sha256'], ('Old baseline does not match reviewed bytes', row['path'], actual, row['before_sha256'])
            write_if_changed(marker, (json.dumps({'sourceInputs': sources, 'tooling': pins}, indent=2) + '\n').encode())
        report['versions'][label] = {'version': version, 'chromiumCommit': chromium, 'tooling': pins,
                                     'sourceInputs': sources, 'external': external, 'pruning': sorted(prune)}
    write_if_changed(work / 'preparation.json', (json.dumps(report, indent=2) + '\n').encode())
    print(json.dumps({'status': 'prepared', 'versions': {k: len(v['sourceInputs']) for k, v in report['versions'].items()}}))


def port_sources(repo,work):
    manifest=json.loads(subprocess.check_output(['git','-C',str(repo),'show','HEAD:build/lite-windows157.json']))
    old=work/'old/restored'
    if not old.exists():
        shutil.copytree(work/'old/baseline',old)
        for entry in manifest['patches']:
            raw=subprocess.check_output(['git','-C',str(repo),'show','HEAD:'+entry['path']])
            patch=work/'product.patch';patch.write_bytes(raw)
            subprocess.run(['git','-c','core.autocrlf=false','apply','--no-index','--whitespace=nowarn',str(patch)],cwd=old,check=True,capture_output=True)
        for name,spec in manifest['runtime']['restore_pruned_resources'].items():
            write_if_changed(old/name,(repo/spec['repository_path']).read_bytes())
        restore_domains(old,None,work/'old/tooling/core/domain_regex.list',work/'old/tooling/windows/domain_substitution.list',manifest['domain_full_files'],pinned=True)
    for row in manifest['source_files']:
        file=old/row['path'];actual=digest(file.read_bytes().replace(b'\r\n',b'\n')) if file.is_file() else None
        assert actual==row['after_sha256'],('Reviewed old product differs',row['path'],actual,row['after_sha256'])
    output=work/'new/restored';output.mkdir(parents=True,exist_ok=True)
    changes=[];conflicts=[]
    for row in manifest['source_files']:
        name=row['path'];old_file=old/name
        if not old_file.is_file():continue
        custom=old_file.read_bytes().replace(b'\r\n',b'\n')
        if row['before_sha256'] is None:
            old_base,new_base=work/'old/pristine'/name,work/'new/pristine'/name
        else:
            old_base,new_base=work/'old/baseline'/name,work/'new/baseline'/name
        if not old_base.is_file():
            merged=custom
        else:
            assert new_base.is_file(),('Source removed upstream needs explicit review',name)
            before=old_base.read_bytes().replace(b'\r\n',b'\n');after=new_base.read_bytes().replace(b'\r\n',b'\n')
            if before==after:merged=custom
            elif b'\0' in custom or b'\0' in before or b'\0' in after:
                assert custom==before,('Changed restored binary needs explicit review',name)
                merged=after
            else:
                temp=work/'merge-inputs';temp.mkdir(exist_ok=True)
                for key,data in [('new',after),('old',before),('custom',custom)]:write_if_changed(temp/key,data)
                result=subprocess.run(['git','merge-file','-p',str(temp/'new'),str(temp/'old'),str(temp/'custom')],capture_output=True)
                if result.returncode:
                    assert result.returncode>0, result.stderr
                    conflicts.append(name);write_if_changed(work/'conflicts'/name,result.stdout)
                    continue
                merged=result.stdout
            if before!=after:changes.append({'path':name,'oldUpstreamSHA256':digest(before),'newUpstreamSHA256':digest(after),'mergedSHA256':digest(merged)})
        write_if_changed(output/name,merged)
    report={'status':'conflicts-require-review' if conflicts else 'merged','upstreamChangedFiles':changes,'conflicts':conflicts,
            'oldProductCheckedFiles':len(manifest['source_files']),'compiled':False,'installedPackageChanged':False}
    write_if_changed(work/'merge-report.json',(json.dumps(report,indent=2)+'\n').encode())
    print(json.dumps(report))
    return 2 if conflicts else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--patch-bin', required=True)
    parser.add_argument('--phase',choices=('prepare','port'),default='prepare')
    args = parser.parse_args()
    for directory in (args.work, args.cache):
        plain(directory).mkdir(parents=True, exist_ok=True)
    if args.phase=='prepare':prepare(args.repo.resolve(), args.work.resolve(), args.cache.resolve(), args.patch_bin)
    else:raise SystemExit(port_sources(args.repo.resolve(),args.work.resolve()))
