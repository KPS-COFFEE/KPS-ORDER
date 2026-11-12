"""Read-only exact-release environment check, before workers or migrations run.

Only the standard library is imported. This check does not grant approval,
change feature switches, connect to PostgreSQL, or start the application.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Mapping

# Keep aligned with the exact-release families used through app.activation.
# Other KPS_*_ENABLED switches do not use that approval contract.
RELEASE_FEATURES = (
    'multibrand', 'customer_sms', 'authentica_customer_sms',
    'customer_benefits', 'gift_orders',
)
SHA = re.compile(r'[a-f0-9]{40}')


def gated_prefix(key: str) -> str | None:
    for feature in RELEASE_FEATURES:
        prefix = 'KPS_' + feature.upper()
        # Brand hyphens are encoded as double underscores by activation.enabled.
        if re.fullmatch(re.escape(prefix) + r'(?:_[A-Z0-9]+(?:__[A-Z0-9]+)*)?_ENABLED', key):
            return key[:-len('_ENABLED')]
    return None


def check(environment: Mapping[str, str], candidate_sha: str | None = None) -> dict:
    actual = environment.get('RENDER_GIT_COMMIT', '')
    pin = environment.get('RUNTIME_REVISION', '')
    target = candidate_sha if candidate_sha is not None else actual or pin
    valid = bool(SHA.fullmatch(target))
    issues = []
    if not valid:
        issues.append({'code': 'INVALID_CANDIDATE_REVISION'})
    if actual and (not SHA.fullmatch(actual) or actual != target):
        issues.append({'code': 'DEPLOYED_SOURCE_REVISION_MISMATCH', 'key': 'RENDER_GIT_COMMIT'})
    if pin and (not SHA.fullmatch(pin) or pin != target):
        issues.append({'code': 'RUNTIME_REVISION_MISMATCH', 'key': 'RUNTIME_REVISION'})

    enabled = sorted({prefix for key, value in environment.items()
                      if value == 'true' and (prefix := gated_prefix(key))})
    for prefix in enabled:
        for suffix in ('_ACCEPTANCE_SHA', '_OWNER_APPROVAL_SHA'):
            key = prefix + suffix
            value = environment.get(key, '')
            if not valid or value != target:
                issues.append({'code': 'FEATURE_RELEASE_NOT_APPROVED', 'key': key,
                               'reason': 'missing' if not value else 'revision_mismatch'})
    return {'check': 'exact_release_environment', 'revision': target if valid else None,
            'passed': not issues, 'enabled_release_gates': enabled, 'issues': issues}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate-sha', help='Full intended source SHA for a pre-trigger check')
    args = parser.parse_args(argv)
    report = check(os.environ, args.candidate_sha)
    # Print variable names and the source SHA only, never environment values.
    print(json.dumps(report, sort_keys=True))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
