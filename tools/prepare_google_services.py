#!/usr/bin/env python3
"""Restore Google endpoints and apply the focused Windows 155 service patch."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile

CPP_TOKENS = re.compile(
    r'(?P<raw>R"(?P<delimiter>[^ ()\\\t\r\n]{0,16})\([\s\S]*?\)(?P=delimiter)")'
    r'|(?P<string>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')'
    r'|(?P<comment>//[^\r\n]*|/\*[\s\S]*?\*/)'
)

DOMAIN_RULES_SHA256 = 'f59c4316b4ac32f9dfe4f6440c4cf9ad5dab0ab8827769f5dc2587ca07e91f09'
DOMAIN_INVERSES = [
    (r'f0ntz(\\*?)\.9oo91e8p1(\\*?)\.qjz9zk', r'fonts\g<1>.googleapis\g<2>.com'),
    (r'9oo91e([A-Za-z\-]*?\\*?)\.qjz9zk', r'google\g<1>.com'),
    (r'95tat1c([A-Za-z\-]*?\\*?)\.qjz9zk', r'gstatic\g<1>.com'),
    (r'ch40me([A-Za-z\-]*?\\*?)\.qjz9zk', r'chrome\g<1>.com'),
    (r'ch40m1um([A-Za-z\-]*?\\*?)\.qjz9zk', r'chromium\g<1>.org'),
    (r'm0z111a([A-Za-z\-]*?\\*?)\.qjz9zk', r'mozilla\g<1>.org'),
    (r'f8c3b00k([A-Za-z\-]*?\\*?)\.qjz9zk', r'facebook\g<1>.com'),
    (r'8pp2p8t([A-Za-z\-]*?\\*?)\.qjz9zk', r'appspot\g<1>.com'),
    (r'y0u1ub3([A-Za-z\-]*?\\*?)\.qjz9zk', r'youtube\g<1>.com'),
    (r'yt1mg([A-Za-z\-]*?\\*?)\.qjz9zk', r'ytimg\g<1>.com'),
    (r'9ma1l([A-Za-z\-]*?\\*?)\.qjz9zk', r'gmail\g<1>.com'),
    (r'60u613cl1c4([A-Za-z\-]*?\\*?)\.n3t\.qjz9zk', r'doubleclick\g<1>.net'),
    (r'60u613cl1c4([A-Za-z\-]*?\\*?)\.c0m\.qjz9zk', r'doubleclick\g<1>.com'),
    (r'9oo91e21p(\\*?)\.qjz9zk', r'googlezip\g<1>.net'),
    (r'b3ac0n2([1-9]?\\*?)\.9vt([1-9]?\\*?)\.qjz9zk', r'beacons\g<1>.gvt\g<2>.com'),
    (r'99pht(\\*?)\.qjz9zk', r'ggpht\g<1>.com'),
    (r'm1cr050ft(\\*?)\.qjz9zk', r'microsoft\g<1>.com'),
    (r'l3lOO(\\*?)\.qjz9zk', r'1e100\g<1>.net'),
    (r'8n6r01d(\\*?)\.qjz9zk', r'android\g<1>.com'),
    (r'goo\.gl(e?)\.qjz9zk', r'goo.gl\g<1>'),
    (r'pr1v4cy54ndb0x([A-Za-z\-]*?\\*?)\.qjz9zk', r'privacysandbox\g<1>.com'),
]


def pinned_inverse_rules(rules: Path):
    if hashlib.sha256(rules.read_bytes().replace(b'\r\n', b'\n')).hexdigest() != DOMAIN_RULES_SHA256:
        raise ValueError('Domain rules do not match this Windows 155 checkpoint')
    return [(re.compile(pattern), replacement) for pattern, replacement in reversed(DOMAIN_INVERSES)]


def inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("Source path escapes the build tree")
    return path


def reverse_map(cache: Path, rules: Path):
    patterns = []
    for line in rules.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            pattern, replacement = line.split("#", 1)
            patterns.append((re.compile(pattern), replacement))
    mapping, paths = {}, set()
    with tarfile.open(cache, "r|*") as archive:
        for entry in archive:
            if not entry.isfile() or not entry.name.replace("\\", "/").startswith("orig/"):
                continue
            name = entry.name.replace("\\", "/")[5:]
            if ".." in Path(name).parts or name.startswith("/"):
                raise ValueError("Unsafe domain-cache path")
            paths.add(name)
            stream = archive.extractfile(entry)
            original = stream.read().decode("latin1")
            for pattern, replacement in patterns:
                def record(match):
                    changed = match.expand(replacement)
                    if changed in mapping and mapping[changed] != match[0]:
                        raise ValueError("Ambiguous domain-substitution inverse")
                    mapping[changed] = match[0]
                    return changed
                original = pattern.sub(record, original)
    if not mapping or not paths:
        raise ValueError("Empty domain-substitution cache")
    return mapping, paths


def restore_domains(src: Path, cache: Path | None, rules: Path, filelist: Path, full_files=(), *, pinned=False):
    inverses = pinned_inverse_rules(rules) if pinned else None
    mapping, names = ({}, set()) if pinned else reverse_map(cache, rules)
    names.update(x.strip() for x in filelist.read_text().splitlines() if x.strip() and not x.startswith("#"))
    pattern = re.compile("|".join(re.escape(x) for x in sorted(mapping, key=len, reverse=True))) if mapping else None
    changed, occurrences = 0, 0
    for name in sorted(names):
        if Path(name).parts[0] == "out":
            raise ValueError("Domain list must not include build outputs")
        path = inside(src, name)
        if not path.is_file():
            continue
        original = path.read_bytes()
        if b"qjz9zk" not in original:
            continue
        source = original.decode("latin1")
        count = 0
        def replace_domains(value):
            nonlocal count
            if inverses:
                for inverse, replacement in inverses:
                    value, hits = inverse.subn(replacement, value)
                    count += hits
                return value
            result, hits = pattern.subn(lambda match: mapping[match[0]], value)
            count += hits
            return result
        if path.suffix in {".cc", ".h", ".c", ".cpp", ".cxx", ".mm"} and name not in full_files:
            # Keep comment-only changes from invalidating thousands of cached
            # translation units. Executable URL/domain strings are restored.
            result = CPP_TOKENS.sub(lambda token: token[0] if token.group("comment") else replace_domains(token[0]), source)
        else:
            result = replace_domains(source)
        updated = result.encode("latin1")
        if updated != original:
            path.write_bytes(updated)
            changed += 1
            occurrences += count
    return {"files_changed": changed, "replacements": occurrences, "mapping_entries": len(inverses or mapping)}


def apply_patch(src: Path, executable: str, patch: Path):
    run = subprocess.run([executable, "-p1", "--batch", "--forward", "--fuzz=0", "--no-backup-if-mismatch", "-i", str(patch.resolve())], cwd=src, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if run.returncode:
        raise RuntimeError(run.stdout + run.stderr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--patch-bin", required=True)
    args = parser.parse_args()
    src = args.work / "src"
    patch = args.repo / "patches/google-services/0001-restore-google-services.patch"
    digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    marker = args.work / "google-services-source.json"
    if marker.exists():
        saved = json.loads(marker.read_text())
        if saved.get("patch_sha256") != digest:
            raise ValueError("The service patch changed; start from the original checkpoint")
        print("Google-service source is already prepared")
        return
    blocker = args.work / ".google-services-preparing"
    if blocker.exists():
        raise ValueError("Source preparation was interrupted; restore a clean checkpoint")
    version = (src / "chrome/VERSION").read_text()
    if dict(re.findall(r"(?m)^(MAJOR|MINOR|BUILD|PATCH)=(\d+)\s*$", version)) != {"MAJOR":"155","MINOR":"0","BUILD":"8059","PATCH":"39"}:
        raise ValueError("This restoration is limited to Chromium 155.0.8059.39")
    cache = next((args.work / n for n in ["domain_substitution_cache.tar.gz", "domain_substitution_cache.tar"] if (args.work / n).is_file()), None)
    blocker.write_text(digest)
    affected = set(json.loads(patch.with_suffix(".json").read_text())["changed_files"])
    # Imported upstream snapshots need not retain the original substitution
    # archive. The pinned reversible rules also cover strings added afterward.
    domains = restore_domains(src, cache, args.work / "tooling/ungoogled-chromium/domain_regex.list", args.work / "tooling/ungoogled-chromium-windows/domain_substitution.list", affected, pinned=True)
    print(json.dumps({"domain_restoration": domains}), flush=True)
    renderer = src / "content/renderer/render_thread_impl.cc"
    if "blink::WebString::FromUTF8(timezone)" in renderer.read_text():
        apply_patch(src, args.patch_bin, args.repo / "patches/chromium155-windows/0233-renderer-timezone-webstring-api.patch")
    apply_patch(src, args.patch_bin, patch)
    checker = (src / "content/browser/webauth/webauth_request_security_checker_impl.h").read_text()
    if "https://www.gstatic.com/securitykey/origins.json" not in checker:
        raise ValueError("Google WebAuthn AppID was not restored")
    report = {"variant": "google-services-self-use", "chromium_version": "155.0.8059.39", "patch_sha256": digest, "domain_restoration": domains, "google_account_and_cloud_access": "not_tested", "credentials": "runtime_environment_only"}
    marker.write_text(json.dumps(report, indent=2), encoding="utf-8")
    blocker.unlink()
    print("Google-service source prepared; existing fingerprint and Windows storage implementations retained")


if __name__ == "__main__":
    main()
