"""Reproduce both October 28 failures without application or database startup."""
import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.release_preflight import RELEASE_FEATURES, check

ROOT = Path(__file__).resolve().parents[1]
NEW = 'a' * 40
OLD = 'b' * 40


def approved(prefix='KPS_MULTIBRAND', sha=NEW):
    return {prefix + '_ENABLED': 'true', prefix + '_ACCEPTANCE_SHA': sha,
            prefix + '_OWNER_APPROVAL_SHA': sha}


def test_both_incident_failures_are_reported_together_and_environment_is_unchanged():
    env = {'RENDER_GIT_COMMIT': NEW, 'RUNTIME_REVISION': OLD, **approved(sha=OLD)}
    before = dict(env)
    result = check(env)
    assert not result['passed']
    assert {issue['key'] for issue in result['issues']} == {
        'RUNTIME_REVISION', 'KPS_MULTIBRAND_ACCEPTANCE_SHA', 'KPS_MULTIBRAND_OWNER_APPROVAL_SHA'}
    assert env == before


def test_correcting_only_the_pin_does_not_approve_the_new_release():
    env = {'RENDER_GIT_COMMIT': NEW, 'RUNTIME_REVISION': NEW, **approved(sha=OLD)}
    result = check(env)
    assert not result['passed']
    assert {i['code'] for i in result['issues']} == {'FEATURE_RELEASE_NOT_APPROVED'}
    env.update(approved())
    assert check(env)['passed']


@pytest.mark.parametrize('pin', ['', NEW])
def test_valid_platform_revision_with_optional_matching_pin(pin):
    assert check({'RENDER_GIT_COMMIT': NEW, 'RUNTIME_REVISION': pin, **approved()})['passed']


@pytest.mark.parametrize('missing', ['_ACCEPTANCE_SHA', '_OWNER_APPROVAL_SHA'])
def test_each_brand_requires_both_independent_approvals(missing):
    env = {'RENDER_GIT_COMMIT': NEW, **approved('KPS_GIFT_ORDERS_KPS'),
           **approved('KPS_GIFT_ORDERS_ENBISHAN')}
    del env['KPS_GIFT_ORDERS_ENBISHAN' + missing]
    result = check(env)
    assert result['issues'] == [{'code': 'FEATURE_RELEASE_NOT_APPROVED',
        'key': 'KPS_GIFT_ORDERS_ENBISHAN' + missing, 'reason': 'missing'}]


def test_hyphenated_brand_encoding_and_unrelated_switches():
    env = {'RENDER_GIT_COMMIT': NEW, **approved('KPS_CUSTOMER_SMS_TEST__BRAND'),
           'KPS_PORTAL_HOST_ROUTING_ENABLED': 'true', 'KPS_BRANCH_PAYMENT_LIVE_ENABLED': 'true'}
    assert check(env)['passed']
    env['KPS_CUSTOMER_SMS_TEST__BRAND_OWNER_APPROVAL_SHA'] = OLD
    assert not check(env)['passed']


def test_disabled_features_do_not_require_renewal():
    env = {'RENDER_GIT_COMMIT': NEW, **approved(sha=OLD)}
    env['KPS_MULTIBRAND_ENABLED'] = 'false'
    assert check(env)['passed']


@pytest.mark.parametrize('revision', ['', 'short', 'A' * 40, 'x' * 40])
def test_missing_or_malformed_identity_fails_closed(revision):
    assert not check({'RENDER_GIT_COMMIT': revision})['passed']


def test_pre_trigger_candidate_rejects_stale_pin_and_stale_platform_identity():
    assert not check({'RUNTIME_REVISION': OLD}, NEW)['passed']
    assert not check({'RENDER_GIT_COMMIT': OLD}, NEW)['passed']
    assert check({'RUNTIME_REVISION': NEW, **approved()}, NEW)['passed']


def test_cli_is_dependency_free_redacts_values_and_exits_nonzero():
    env = {'RENDER_GIT_COMMIT': NEW, **approved(),
           'KPS_MULTIBRAND_OWNER_APPROVAL_SHA': 'private-approval-value',
           'DATABASE_URL': 'postgresql://private-password@private-host/db'}
    result = subprocess.run([sys.executable, '-S', '-m', 'app.release_preflight'],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 1 and not result.stderr
    report = json.loads(result.stdout)
    assert not report['passed']
    assert 'private-' not in result.stdout
    env['KPS_MULTIBRAND_OWNER_APPROVAL_SHA'] = NEW
    result = subprocess.run([sys.executable, '-S', '-m', 'app.release_preflight'],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0 and json.loads(result.stdout)['passed']


def test_manifest_covers_every_runtime_exact_release_family():
    used = set()
    for path in (ROOT / 'app').glob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == 'activation'
                    and node.func.attr in {'require', 'enabled'}):
                assert node.args and isinstance(node.args[0], ast.Constant), path
                used.add(node.args[0].value)
    assert used == set(RELEASE_FEATURES)
