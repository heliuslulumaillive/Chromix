"""Require the compiled focused Cookie cases, without accepting skipped/retried tests."""
import argparse
import json
from pathlib import Path

REQUIRED = {
    'ShardXNativeCookieTest.BindingRequiresOwnedDefaultAndExactAttemptName',
    'ShardXNativeCookieTest.ExplicitPathSecureFalseAndSourceUrlArePreserved',
    'ShardXNativeCookieTest.SessionAndPersistentExpiryStayNative',
    'ShardXNativeCookieTest.UnspecifiedSameSiteRemainsAnUnspecifiedAttribute',
    'ShardXNativeCookieTest.DomainAndHostOnlyKeysAreNotCollapsed',
    'ShardXNativeCookieTest.ConflictingDuplicatesAndUnsupportedSemanticsFailPreflight',
    'ShardXNativeCookieTest.AllInputValidationPrecedesAnyMutation',
    'ShardXNativeCookieTest.ReadbackChecksValuesAttributesAndEviction',
    'ShardXNativeCookieTest.ExpiredDeletionMustActuallyBeAbsent',
    'ShardXNativeCookieTest.CompletionLedgerSupportsRecreatedProfileWithoutEnvelope',
}


def verify(data):
    iterations = data.get('per_iteration_data')
    if not isinstance(iterations, list) or len(iterations) != 1 or not isinstance(iterations[0], dict):
        raise ValueError('Expected one fresh native-cookie test iteration')
    tests = iterations[0]
    if set(tests) != REQUIRED:
        raise ValueError('Native-cookie test inventory must match the exact required cases')
    for name, attempts in tests.items():
        if not isinstance(attempts, list) or len(attempts) != 1 or not isinstance(attempts[0], dict) or attempts[0].get('status') != 'SUCCESS':
            raise ValueError('Native-cookie case did not pass without retries: ' + name)
    return {'status': 'passed', 'required': len(REQUIRED), 'executed': len(tests)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(json.loads(args.summary.read_text(encoding='utf-8-sig')))))
