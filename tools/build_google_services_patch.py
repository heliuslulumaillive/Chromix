#!/usr/bin/env python3
"""Build a focused restoration patch from pinned Chromium and patch inputs.

The two scratch trees reproduce the same existing Windows/Chromix layers.
Only the configured Google-service removals differ; fingerprints and Windows
data-encryption behavior stay in both trees. No account credentials are used.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.request import Request, urlopen

sys.dont_write_bytecode = True
from patch_selection import select

HEADER = re.compile(r"^--- (a/[^\r\n]+|/dev/null)\r?\n\+\+\+ (b/[^\r\n]+|/dev/null)\r?\n", re.M)
HUNK = re.compile(r"^@@ [^\n]+\n", re.M)
INCLUDE = re.compile(r'^#include "([^"\n]+)"\n', re.M)


def prune_selected_sources(tree: Path, paths, *, keep=()):
    """Replay pruning for the selected inputs before the patch layers."""
    root = tree.resolve()
    for name in sorted(set(paths) - set(keep)):
        path = (root / name).resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError('Pruning path escapes the replay tree')
        if path.exists():
            path.unlink()


def filter_pruned_includes(baseline: str, restored: str, unavailable):
    """Do not revive a removed header through a mixed sign-in cleanup hunk."""
    removed = []
    def keep(match):
        if match[1] in unavailable and match[0] not in baseline:
            removed.append(match[1])
            return ''
        return match[0]
    return INCLUDE.sub(keep, restored), removed


def retain_hunks(section: str, restore_markers: list[str]) -> str:
    """Omit only reviewed cleanup hunks; preserve unrelated feature removals."""
    matches = list(HUNK.finditer(section))
    kept = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        hunk = section[match.start():end]
        changes = '\n'.join(line[1:] for line in hunk.splitlines() if line.startswith(('+', '-')))
        if not any(marker in changes for marker in restore_markers):
            kept.append(hunk)
    return section[:matches[0].start()] + ''.join(kept) if kept else ''


def sections(data: str):
    matches = list(HEADER.finditer(data))
    for index, match in enumerate(matches):
        name = match[2][2:] if match[2] != "/dev/null" else match[1][2:]
        if name.startswith("/") or ".." in Path(name).parts:
            raise ValueError("Unsafe patch source path")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(data)
        # A diff --git header belongs to the following file, not this section.
        text = data[match.start():end]
        text = re.split(r"^diff --git ", text, flags=re.M)[0]
        yield name, match[1] == "/dev/null", text


def patch_series(root: Path):
    for line in (root / "patches/series").read_text().splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            yield name, (root / "patches" / name).read_text(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--patch-bin", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((args.repo / "build/google-services-win155.json").read_text())
    removed = set(config["remove_patches"])
    restore = {k: set(v) for k, v in config["restore_sections"].items()}
    restore_hunks = config.get("restore_hunks", {})
    windows_restore = {k: set(v) for k, v in config.get("restore_windows_sections", {}).items()}
    pruning_file = args.core / 'pruning.list'
    pruning_data = pruning_file.read_bytes().replace(b'\r\n', b'\n')
    pruned = {line.strip() for line in pruning_data.decode().splitlines() if line.strip() and not line.startswith('#')}
    restored_pruned = set(config.get('restore_pruned_sources', []))
    if not restored_pruned.issubset(pruned):
        raise ValueError('Requested pruned sources do not match the pinned pruning list')
    core = list(patch_series(args.core))
    windows = list(patch_series(args.windows))
    _, chromix_bytes = select(args.repo, "windows")
    chromix = [(name, data.decode()) for name, data in chromix_bytes]
    targets = set(config["extra_sources"])
    targets.update(restored_pruned)
    for name, data in core:
        if name in removed:
            targets.update(path for path, _, _ in sections(data))
        targets.update(restore.get(name, set()))
        targets.update(restore_hunks.get(name, {}))
    for name, _ in windows:
        targets.update(windows_restore.get(name, set()))
    # Keep the existing flag definitions for backward-compatible startup args.
    # Restoring service behavior does not require removing their UI scaffolding.
    targets = {p for p in targets if not re.match(r"chrome/browser/(?:ungoogled|bromite)_.*\.h$", p)}
    all_patches = [("core", *p) for p in core] + [("windows", *p) for p in windows] + [("chromix", *p) for p in chromix]
    created = {path for _, _, data in all_patches for path, new, _ in sections(data) if new and path in targets}
    pristine = args.work / "pristine"
    pristine.mkdir(parents=True, exist_ok=True)

    def fetch(name):
        path = pristine / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            url = f"https://raw.githubusercontent.com/chromium/chromium/{config['chromium_version']}/{name}"
            for attempt in range(3):
                try:
                    with urlopen(Request(url, headers={"User-Agent": "Chromix-Google-services"}), timeout=40) as response:
                        data = response.read()
                    path.write_bytes(data)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(attempt + 1)
        return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    with ThreadPoolExecutor(max_workers=8) as pool:
        sources = list(pool.map(fetch, sorted(targets - created)))
    trees = []
    for variant in ("baseline", "restored"):
        tree = args.work / variant
        if tree.exists():
            raise FileExistsError(f"Use a fresh work directory: {tree}")
        shutil.copytree(pristine, tree)
        prune_selected_sources(tree, targets & pruned, keep=restored_pruned if variant == 'restored' else ())
        count = 0
        for layer, name, data in all_patches:
            selected = []
            for path, _, section in sections(data):
                if path not in targets:
                    continue
                if variant == "restored" and layer == "core" and (name in removed or path in restore.get(name, set())):
                    continue
                if variant == "restored" and layer == "windows" and path in windows_restore.get(name, set()):
                    continue
                if variant == "restored" and layer == "core" and path in restore_hunks.get(name, {}):
                    section = retain_hunks(section, restore_hunks[name][path])
                    if not section:
                        continue
                selected.append(section)
            if not selected:
                continue
            patch = args.work / "current.patch"
            patch.write_text("".join(selected), encoding="utf-8", newline="\n")
            run = subprocess.run([args.patch_bin, "-p1", "--batch", "--forward", "--fuzz=0", "--no-backup-if-mismatch", "-i", str(patch.resolve())], cwd=tree, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            if run.returncode:
                raise RuntimeError(f"{variant}: {layer}/{name}\n{run.stdout}{run.stderr}")
            count += 1
        trees.append(tree)
        print(f"{variant}: replayed {count} relevant patch fragments", flush=True)

    generation = trees[1] / "components/password_manager/core/browser/password_feature_manager_impl.cc"
    text = generation.read_text()
    before = "case sync_util::SyncState::kNotActive:\n      return false;"
    after = "case sync_util::SyncState::kNotActive:\n      // The caller separately checks whether saving and filling are enabled.\n      // Local-only profiles can generate passwords without cloud enrollment.\n      return true;"
    if text.count(before) != 1:
        raise ValueError("Password-generation source changed")
    generation.write_text(text.replace(before, after), encoding="utf-8", newline="\n")

    removed_includes = {}
    for name in sorted(targets):
        old, new = trees[0] / name, trees[1] / name
        if new.suffix not in {'.cc', '.h'} or not new.is_file():
            continue
        text, removed = filter_pruned_includes(old.read_text() if old.exists() else '', new.read_text(), pruned - restored_pruned)
        if removed:
            new.write_text(text, encoding='utf-8', newline='\n')
            removed_includes[name] = removed

    # Keep the unrelated Safe Browsing tracked-pref removal in this mixed hunk.
    factory = trees[1] / "chrome/browser/prefs/chrome_pref_service_factory.cc"
    if factory.exists():
        text = factory.read_text()
        unrelated = [
            '    {18, prefs::kSafeBrowsingIncidentsSent, EnforcementLevel::ENFORCE_ON_LOAD,\n'
            '     PrefTrackingStrategy::ATOMIC, ValueType::IMPERSONAL},\n',
        ]
        for value in unrelated:
            if text.count(value) != 1:
                raise ValueError('The mixed preference cleanup changed')
            text = text.replace(value, '')
        factory.write_text(text, encoding='utf-8', newline='\n')

    differences, changed = [], []
    for name in sorted(targets):
        old, new = trees[0] / name, trees[1] / name
        a = old.read_text().splitlines(keepends=True) if old.exists() else []
        b = new.read_text().splitlines(keepends=True) if new.exists() else []
        if a != b:
            differences.extend(difflib.unified_diff(a, b, fromfile="a/" + name if old.exists() else "/dev/null", tofile="b/" + name if new.exists() else "/dev/null"))
            changed.append(name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.is_file() and args.output.read_text() != ''.join(differences):
        previous = args.output.read_bytes()
        history = args.output.parent / 'history'
        history.mkdir(exist_ok=True)
        (history / (hashlib.sha256(previous).hexdigest() + '.patch')).write_bytes(previous)
    args.output.write_text("".join(differences), encoding="utf-8", newline="\n")
    report = {"version": config["chromium_version"], "changed_files": changed, "source_inputs": sources, "pruning_list_sha256": hashlib.sha256(pruning_data).hexdigest(), "baseline_pruned_sources": sorted(targets & pruned), "restored_pruned_sources": sorted(restored_pruned), "removed_pruned_includes": removed_includes, "patch_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(), "qualification": "Source replay only; requires native build and actual account tests"}
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps({"changed_files": len(changed), "patch_bytes": args.output.stat().st_size, "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
