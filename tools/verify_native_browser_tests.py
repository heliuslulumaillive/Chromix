"""Require actual execution of the focused compiled native-browser cases."""
import argparse
import json
from pathlib import Path

REQUIRED = {
    'ShardXNativeBrowserTest.ManagedEnvironmentOwnsProfileShellIdentity',
    'ShardXNativeBrowserTest.InvalidManagedMarkerPreservesNativeIdentity',
    'ShardXNativeBrowserTest.ManagedProfileShortcutsRemainUnavailable',
    'ShardXNativeBrowserTest.BlockedPortParserPreservesBoundsAndDenySemantics',
}


def verify(data):
    iterations = data.get('per_iteration_data')
    if not isinstance(iterations, list) or len(iterations) != 1 or not isinstance(iterations[0], dict):
        raise ValueError('Expected one fresh native-browser test iteration')
    tests = iterations[0]
    if not REQUIRED.issubset(tests):
        raise ValueError('Required native-browser test cases did not execute')
    for name, attempts in tests.items():
        if not isinstance(attempts, list) or len(attempts) != 1 or attempts[0].get('status') != 'SUCCESS':
            raise ValueError('Native-browser test did not pass without retries: ' + name)
    return {'status': 'passed', 'required': len(REQUIRED), 'executed': len(tests)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(json.loads(args.summary.read_text(encoding='utf-8-sig')))))
