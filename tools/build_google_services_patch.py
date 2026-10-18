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
    core = list(patch_series(args.core))
    windows = list(patch_series(args.windows))
    _, chromix_bytes = select(args.repo, "windows")
    chromix = [(name, data.decode()) for name, data in chromix_bytes]
    targets = set(config["extra_sources"])
    for name, data in core:
        if name in removed:
            targets.update(path for path, _, _ in sections(data))
        targets.update(restore.get(name, set()))
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
        count = 0
        for layer, name, data in all_patches:
            selected = []
            for path, _, section in sections(data):
                if path not in targets:
                    continue
                if variant == "restored" and layer == "core" and (name in removed or path in restore.get(name, set())):
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

    differences, changed = [], []
    for name in sorted(targets):
        old, new = trees[0] / name, trees[1] / name
        a = old.read_text().splitlines(keepends=True) if old.exists() else []
        b = new.read_text().splitlines(keepends=True) if new.exists() else []
        if a != b:
            differences.extend(difflib.unified_diff(a, b, fromfile="a/" + name if old.exists() else "/dev/null", tofile="b/" + name if new.exists() else "/dev/null"))
            changed.append(name)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(differences), encoding="utf-8", newline="\n")
    report = {"version": config["chromium_version"], "changed_files": changed, "source_inputs": sources, "patch_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(), "qualification": "Source replay only; requires native build and actual account tests"}
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"changed_files": len(changed), "patch_bytes": args.output.stat().st_size, "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
