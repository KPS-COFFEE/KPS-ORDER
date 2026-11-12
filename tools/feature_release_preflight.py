"""Read-only operator gate. Supply only release flags/SHAs, never credentials."""
import argparse
import json
import re
import sys


def assess(revision, values):
    failures = []
    if not re.fullmatch(r'[a-f0-9]{40}', revision):
        failures.append('INVALID_CANDIDATE_REVISION')
    if not isinstance(values, dict):
        return {'approved': False, 'failures': ['INVALID_RELEASE_MANIFEST']}
    allowed = re.compile(
        r'KPS_(?:MULTIBRAND|(?:CUSTOMER_SMS|AUTHENTICA_CUSTOMER_SMS|'
        r'CUSTOMER_BENEFITS|GIFT_ORDERS)_[A-Z0-9_]+)'
        r'_(?:ENABLED|ACCEPTANCE_SHA|OWNER_APPROVAL_SHA)')
    if any(not isinstance(key, str) or not (
            allowed.fullmatch(key) or key in {
                'RUNTIME_REVISION', 'KPS_CENTRAL_OPERATIONS_ENABLED'})
           for key in values):
        failures.append('UNEXPECTED_MANIFEST_KEYS')
    if any(not isinstance(value, str) for value in values.values()):
        failures.append('NON_STRING_CONFIGURATION')
    if any(not isinstance(key, str) for key in values):
        return {'approved': False, 'failures': failures}
    if values.get('KPS_CENTRAL_OPERATIONS_ENABLED', 'false') not in ('true', 'false'):
        failures.append('INVALID_FLAG:KPS_CENTRAL_OPERATIONS_ENABLED')
    pinned = values.get('RUNTIME_REVISION', '')
    if pinned and pinned != revision:
        failures.append('RUNTIME_REVISION_MISMATCH')
    if 'KPS_MULTIBRAND_ENABLED' not in values:
        failures.append('MULTIBRAND_STATE_MISSING')
    if (values.get('KPS_CENTRAL_OPERATIONS_ENABLED') == 'true'
            and values.get('KPS_MULTIBRAND_ENABLED') != 'true'):
        failures.append('CENTRAL_REQUIRES_MULTIBRAND')
    checked = []
    for key, value in sorted(values.items()):
        if not isinstance(key, str) or not allowed.fullmatch(key):
            continue
        if not key.endswith('_ENABLED'):
            continue
        if value not in ('true', 'false'):
            failures.append('INVALID_FLAG:' + key)
        if value != 'true':
            continue
        prefix = key[:-len('_ENABLED')]
        checked.append(prefix)
        for suffix in ('_ACCEPTANCE_SHA', '_OWNER_APPROVAL_SHA'):
            if values.get(prefix + suffix) != revision:
                failures.append('FEATURE_RELEASE_NOT_APPROVED:' + prefix + suffix)
    return {'approved': not failures, 'revision': revision,
            'checked_features': checked, 'failures': failures}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revision', required=True,
                        help='Exact commit that will be deployed, not the previous live commit')
    args = parser.parse_args()
    try:
        result = assess(args.revision, json.load(sys.stdin))
    except (ValueError, TypeError):
        result = {'approved': False, 'failures': ['INVALID_RELEASE_MANIFEST']}
    print(json.dumps(result, sort_keys=True))
    return 0 if result['approved'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
