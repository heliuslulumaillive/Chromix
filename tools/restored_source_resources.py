"""Pinned binary resources that GNU text patches cannot faithfully restore."""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath


def resources(repo: Path, config: dict) -> dict[str, bytes]:
    result = {}
    for name, spec in config.get('restore_pruned_resources', {}).items():
        relative = PurePosixPath(name)
        if not relative.parts or relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
            raise ValueError('Restored resource path escapes the source tree')
        payload = (repo / spec['repository_path']).resolve()
        if not payload.is_relative_to(repo.resolve()) or not payload.is_file():
            raise ValueError('Restored resource payload must be a repository file')
        data = payload.read_bytes()
        if len(data) != spec['size'] or hashlib.sha256(data).hexdigest() != spec['sha256']:
            raise ValueError('Restored resource payload identity mismatch: ' + name)
        result[name] = data
    return result


def restore(src: Path, repo: Path, config: dict) -> list[str]:
    restored = []
    for name, expected in resources(repo, config).items():
        path = (src / name).resolve()
        if not path.is_relative_to(src.resolve()):
            raise ValueError('Restored resource redirects outside the source tree')
        if path.exists():
            if path.read_bytes() != expected:
                raise ValueError('Existing restored resource has unexpected bytes: ' + name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
        restored.append(name)
    return restored


def verify(src: Path, repo: Path, config: dict) -> list[str]:
    for name, expected in resources(repo, config).items():
        path = (src / name).resolve()
        if not path.is_relative_to(src.resolve()) or not path.is_file() or path.read_bytes() != expected:
            raise ValueError('Restored source resource is missing or changed: ' + name)
    return list(config.get('restore_pruned_resources', {}))
