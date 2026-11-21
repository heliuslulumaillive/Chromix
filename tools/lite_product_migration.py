"""Apply explicitly reviewed same-version product source repairs, never objects."""
from __future__ import annotations

import copy
import os
import re

try:
    from . import lite_patch_stack as stack
    from .google_services_inputs import owned, write_if_changed
except ImportError:
    import lite_patch_stack as stack
    from google_services_inputs import owned, write_if_changed

HEX = re.compile(r'[a-f0-9]{64}\Z')


def reviewed_updates(repo, rule):
    if (rule.get('source_patch_kind') != stack.KIND or rule.get('target_patch_kind') != stack.KIND
            or rule.get('source_aggregate_sha256') == rule.get('target_aggregate_sha256')):
        raise ValueError('Source updates require a reviewed changed product stack')
    updates = rule.get('product_source_updates')
    if not isinstance(updates, list) or not updates:
        raise ValueError('Product checkpoint migration requires an identical source stack or reviewed source updates')
    target = stack.load(repo)
    if target['stack_sha256'] != rule.get('target_aggregate_sha256'):
        raise ValueError('Product source updates target another stack')
    declarations = {row['path']: row for row in target['source_files']}
    seen = set()
    for update in updates:
        if not isinstance(update, dict) or set(update) != {'path', 'before_sha256', 'after_sha256', 'replacement_path'}:
            raise ValueError('Invalid product source update')
        name = update['path']
        owned(repo, name)
        before, after = update['before_sha256'], update['after_sha256']
        target_row = declarations.get(name)
        if name in seen or target_row is None or target_row['after_sha256'] != after or before == after:
            raise ValueError('Product source update differs from its reviewed target')
        seen.add(name)
        product_created = 'before_sha256' in target_row and target_row['before_sha256'] is None
        if before is not None and not (isinstance(before, str) and HEX.fullmatch(before)):
            raise ValueError('Invalid registered source preimage')
        if before is None and not product_created:
            raise ValueError('Source creation requires absence in the upstream baseline')
        replacement = update['replacement_path']
        if after is None:
            if not product_created or before is None or replacement is not None:
                raise ValueError('Only a pinned product-created source can be removed')
            continue
        if not isinstance(after, str) or not HEX.fullmatch(after):
            raise ValueError('Invalid target source digest')
        if not isinstance(replacement, str) or not replacement.startswith('build/checkpoint-sources/'):
            raise ValueError('Product source replacement must be a registered checkpoint source')
        if stack.digest(owned(repo, replacement).read_bytes()) != after:
            raise ValueError('Product source replacement bytes changed')
    return updates


def verify_donor_sources(repo, src, rule, *, inverse_rules=()):
    updates = reviewed_updates(repo, rule)
    donor = copy.deepcopy(stack.load(repo))
    preimages = {row['path']: row['before_sha256'] for row in updates}
    for row in donor['source_files']:
        if row['path'] in preimages:
            row['after_sha256'] = preimages[row['path']]
    # Unchanged files must already match the new manifest exactly. Changed
    # files must match their registered old bytes before any source write.
    stack._verify_manifest_source(donor, src, inverse_rules=inverse_rules)
    return updates


def apply_source_updates(repo, src, rule, *, inverse_rules=()):
    updates = verify_donor_sources(repo, src, rule, inverse_rules=inverse_rules)
    replacements = {}
    for update in updates:
        if update['after_sha256'] is None:
            continue
        data = owned(repo, update['replacement_path']).read_bytes()
        if stack.digest(data) != update['after_sha256']:
            raise ValueError('Product source replacement changed before transition')
        replacements[update['path']] = data
    previous = []
    try:
        for update in updates:
            name = update['path']
            path = owned(src, name)
            stat = path.stat() if path.is_file() else None
            before = path.read_bytes() if stat else None
            if update['after_sha256'] is None:
                path.unlink()
            else:
                write_if_changed(path, replacements[name])
            previous.append((name, before, stat))
        stack.verify_source(repo, src, inverse_rules=inverse_rules)
    except BaseException:
        for name, before, stat in reversed(previous):
            path = owned(src, name)
            if before is None:
                path.unlink(missing_ok=True)
            else:
                write_if_changed(path, before)
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        raise
    return [row['path'] for row in updates]
