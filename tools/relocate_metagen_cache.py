"""Repair only the authenticated first .78 v3 checkpoint's cache placement.

Keep the full compiler fingerprint check unchanged. Both complete tool-tree
identities are pinned to recorded evidence; only the two already pinned Python
files may move. No compiled output or preparation marker is edited.
"""
import argparse
import json
from pathlib import Path
import stat
try:
    from . import install_windows_metagen_bindings as bindings
    from .prepare_restored_build import tool_fingerprint, MARKER
except ImportError:
    import install_windows_metagen_bindings as bindings
    from prepare_restored_build import tool_fingerprint, MARKER

DONOR = {'source_commit':'6d2736013ed645484995df0117a3ffafda942b87',
         'recipe_sha256':'deb1c6013dc55fdbf60c78000fb2e2afe337c8c59e32483a82c167b1b94116ea',
         'run_id':'43953428441','stage':1,'chromium_version':'157.0.8103.78',
         'repository':'heliuslulumaillive/Chromix','arch':'x64','resume_safe':True}
BASE = {'third_party/llvm-build/Release+Asserts':'c3b6d0009b4c9636fee77466686b3d64f76b9ec2b96318f0eb44e4ee83c8d976',
        'third_party/rust-toolchain':'aae8e8ad83ae10e302dfdc92027ba880ca9859f5d89c05a3b6a5503622152e13'}
WITH_CACHE = dict(BASE, **{'third_party/llvm-build/Release+Asserts':'004d081b66e7135a10ee0d1efafc953c76c871c7618be7084118bbe5caf34e35'})


def relocate(src: Path, manifest: dict):
    src = bindings.local(src.absolute())
    for name, value in DONOR.items():
        if manifest.get(name) != value:
            raise ValueError('Cache relocation is not authorized for this donor: '+name)
    marker = bindings.local(src / MARKER)
    if json.loads(marker.read_bytes()).get('tool_fingerprint') != BASE:
        raise ValueError('Original compiler fingerprint marker does not match the exact donor')
    if bindings.LLVM_COMMIT != 'ea7d852a70e8bdfaf601d6626a760f9771b2c4b4':
        raise ValueError('Unexpected cache profile')
    old = bindings.local(src / bindings.SOURCE_PACKAGES[0])
    destination = bindings.retained_package(src)
    current = tool_fingerprint(src, 'windows', 'x64')
    if current == BASE and destination.exists():
        bindings.read_package(destination)
        return {'status':'already-relocated','compiled_outputs_touched':False,'tool_fingerprint':current}
    if current != WITH_CACHE:
        raise ValueError('Compiler tree differs from the authenticated cache-bearing checkpoint')
    if not old.is_dir() or {p.name for p in old.iterdir()} != set(bindings.HASHES):
        raise ValueError('Legacy cache has unknown or missing entries')
    files = bindings.read_package(old)
    stamps = {name:(stat.S_IMODE((old/name).stat().st_mode),(old/name).stat().st_atime_ns,(old/name).stat().st_mtime_ns) for name in files}
    bindings.retain_package(src, files)
    removed = []
    try:
        for name, content in files.items():
            file = bindings.local(old/name)
            if file.read_bytes() != content:
                raise ValueError('Legacy cache changed during relocation')
            file.unlink(); removed.append(name)
        after = tool_fingerprint(src, 'windows', 'x64')
        if after != BASE:
            raise ValueError('Original full compiler fingerprints were not restored')
    except BaseException:
        import os
        for name in removed:
            file = old/name
            with file.open('xb') as out:out.write(files[name])
            mode, atime, mtime = stamps[name];file.chmod(mode);os.utime(file,ns=(atime,mtime))
        raise
    for directory in [old,old.parent,old.parent.parent]:
        bindings.local(directory)
        if directory.is_dir() and not any(directory.iterdir()):directory.rmdir()
    return {'status':'relocated-verified-cache','files':sorted(files),'destination':str(destination),
            'compiled_outputs_touched':False,'tool_fingerprint':after}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--src',type=Path,required=True);parser.add_argument('--manifest',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(relocate(args.src,json.loads(bindings.local(args.manifest).read_bytes())),sort_keys=True))
