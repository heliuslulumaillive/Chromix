#!/usr/bin/env python3
"""Describe restored Ninja state and pending work without compiling anything."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def summarize_log(path):
    digest, outputs, malformed = hashlib.sha256(), set(), 0
    with path.open('rb') as stream:
        header = stream.readline()
        digest.update(header)
        if not re.fullmatch(rb'# ninja log v\d+\r?\n?', header):
            raise ValueError('Missing or invalid restored Ninja log header')
        for line in stream:
            digest.update(line)
            fields = line.rstrip(b'\r\n').split(b'\t')
            if len(fields) == 5 and all(field.isdigit() for field in fields[:2]):
                outputs.add(fields[3])
            elif line.strip():
                malformed += 1
    return {'log_version': header.decode().strip(), 'log_sha256': digest.hexdigest(),
            'cached_log_outputs': len(outputs), 'unparsed_log_entries': malformed}


def pending_count(text):
    progress = re.findall(r'\[(\d+)/(\d+)\]\s*([^\r\n]*)', text)
    if progress:
        if all('Regenerating ninja files' in description for _, _, description in progress):
            return None  # A dry run cannot execute GN and observe the new graph.
        return max(int(total) for _, total, _ in progress)
    if 'ninja: no work to do.' in text:
        return 0
    raise ValueError('Dry run did not report the pending build work')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ninja', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    result = {'schema_version': 1, 'compiler_invoked': False, 'status': 'failed'}
    try:
        result.update(summarize_log(args.out / '.ninja_log'))
        env = {**os.environ, 'NINJA_STATUS': '[%f/%t] '}
        plan = subprocess.run([str(args.ninja), '-C', str(args.out), '-n', 'chrome'],
                              env=env, capture_output=True, text=True, timeout=240)
        args.report.with_suffix('.log').write_text(plan.stdout + plan.stderr, encoding='utf-8')
        if plan.returncode:
            raise ValueError('Ninja could not plan the restored tree; see dry-run log')
        pending = pending_count(plan.stdout)
        result.update(status='passed' if pending is not None else 'manifest_regeneration_required', pending_actions=pending,
                      scope='Cached log output count and current pending actions; not a cache-hit percentage')
    except Exception as error:
        result['error'] = str(error)
    args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    return 0 if result['status'] in ('passed', 'manifest_regeneration_required') else 1


if __name__ == '__main__':
    raise SystemExit(main())
