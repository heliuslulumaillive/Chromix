"""Extract ordinary members from a pinned Chromium tar stream on NTFS.

The Windows source tree represents internal POSIX aliases with the contents of
their targets. This works without creating reparse points or requiring the
desktop or runner account to have a symbolic-link privilege.
"""
import os
from pathlib import Path
import posixpath
import shutil
import subprocess
import tarfile

_DEVICES = {'CON', 'CONIN$', 'CONOUT$', 'PRN', 'AUX', 'NUL'} | {
    p + n for p in ('LPT', 'COM') for n in '123456789¹²³'}


def _relative_name(name):
    if (not name or name.startswith('/') or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF
                                                or c in '\\:<>"|?*' for c in name)
            or any(p in ('', '.', '..') or p.endswith(('.', ' ')) or
                   p.split('.', 1)[0].rstrip().upper() in _DEVICES for p in name.split('/'))):
        raise ValueError(f'Invalid Windows source path: {name!r}')
    return name


def _link_target(name, linkname):
    if not linkname or linkname.startswith('/') or '\\' in linkname or ':' in linkname:
        raise ValueError(f'Invalid alias target for {name!r}')
    return _relative_name(posixpath.normpath(posixpath.join(posixpath.dirname(name), linkname)))


def _materialize_aliases(root, links):
    resolved = {}

    def resolve(name, trail):
        if name in resolved:
            return resolved[name]
        if name in trail:
            raise ValueError('Cyclic source alias: ' + name)
        parts = links[name].split('/')
        for i in range(len(parts), 0, -1):
            prefix = '/'.join(parts[:i])
            if prefix in links:
                parts[:i] = resolve(prefix, trail | {name}).split('/')
                break
        resolved[name] = '/'.join(parts)
        return resolved[name]

    finished = set()

    def copy_alias(name, trail):
        if name in finished:
            return
        if name in trail:
            raise ValueError('Cyclic source directory alias: ' + name)
        target = resolve(name, set())
        source, dest = root / target, root / name
        if dest.exists():
            raise ValueError('Source alias collides with a file: ' + name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            for child in links:
                if child.startswith(target + '/'):
                    copy_alias(child, trail | {name})
            shutil.copytree(source, dest)
        elif source.is_file():
            shutil.copy2(source, dest)
        else:
            raise ValueError(f'Missing source alias target: {name} -> {target}')
        finished.add(name)

    for name in links:
        copy_alias(name, set())


def extract_tar(stream, dest, root_name, excluded):
    # The Windows 158 source contains paths longer than MAX_PATH.
    if os.name == 'nt':
        full = os.path.abspath(dest)
        if not full.startswith('\\\\?\\'):
            full = ('\\\\?\\UNC\\' + full[2:]) if full.startswith('\\\\') else ('\\\\?\\' + full)
        dest = Path(full)
    if not dest.is_dir() or any(dest.iterdir()):
        raise ValueError('Expected an empty native source directory')
    prefix = _relative_name(root_name) + '/'
    ignored = tuple(d + '/' for d in excluded)
    links = {}
    directories = {''}
    files = 0
    with tarfile.open(fileobj=stream, mode='r|', bufsize=1024 * 1024) as archive:
        for member in archive:
            archive.members.clear()
            if member.name.rstrip('/') == root_name and member.isdir():
                continue
            if not member.name.startswith(prefix):
                raise ValueError('Unexpected Chromium archive root')
            name = member.name[len(prefix):].rstrip('/')
            if name in excluded or name.startswith(ignored):
                continue
            _relative_name(name)
            path = dest / name
            if member.isdir():
                if name not in directories:
                    path.mkdir(parents=True, exist_ok=True)
                    directories.add(name)
            elif member.isfile():
                parent = posixpath.dirname(name)
                if parent not in directories:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    directories.add(parent)
                with archive.extractfile(member) as src, path.open('xb') as out:
                    shutil.copyfileobj(src, out, 1024 * 1024)
                os.utime(path, (member.mtime, member.mtime))
                files += 1
                if files % 250000 == 0:
                    print(f'Extracted {files:,} Chromium source files', flush=True)
            elif member.issym() or member.islnk():
                if name in links:
                    raise ValueError('Duplicate source alias: ' + name)
                if member.islnk():
                    if not member.linkname.startswith(prefix):
                        raise ValueError('Archive hard link refers to another root')
                    links[name] = _relative_name(member.linkname[len(prefix):])
                else:
                    links[name] = _link_target(name, member.linkname)
            else:
                raise ValueError('Unsupported Chromium source member: ' + name)
    _materialize_aliases(dest, links)
    return {'ordinary_source_files': files, 'materialized_aliases': len(links)}


def unpack_xz(sevenzip, archive, destination, root_name, excluded):
    with subprocess.Popen([str(sevenzip), 'x', '-so', '-bsp0', str(archive)],
                          stdout=subprocess.PIPE) as decoder:
        try:
            result = extract_tar(decoder.stdout, destination, root_name, excluded)
            while decoder.stdout.read(1024 * 1024):
                pass
        finally:
            decoder.stdout.close()
        if decoder.wait() != 0:
            raise ValueError('The Chromium source archive could not be decompressed')
    return result
