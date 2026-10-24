"""Rollout evidence must classify conflicts without exposing private row data."""
import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location('central_activation', Path(__file__).resolve().parents[1] / 'tools' / 'central_activation_preflight.py')
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def shift(identifier='s1', **updates):
    return dict({'id': identifier, 'user_id': 'u1', 'brand_id': 'kps', 'kind': 'manager',
                 'status': 'open', 'scope_valid': True, 'morning': False, 'receipt_trial': False,
                 'has_start_inspection': False, 'workday_id': None, 'manager_shift_id': None,
                 'display_name': 'PRIVATE_NAME', 'phone': 'PRIVATE_PHONE', 'vehicle_log': 'PRIVATE_LOG'}, **updates)


class CentralActivationPreflight(unittest.TestCase):
    def test_multiple_legacy_shifts_never_become_adoption_candidate(self):
        result = preflight.assess([shift(), shift('s2', brand_id='enbishan')], [])
        self.assertFalse(result['metadata_preflight_clear'])
        self.assertEqual(result['legacy_adoption_candidates'], [])
        self.assertEqual(result['conflicts'][0]['reason'], 'MULTIPLE_EXISTING_WORKDAYS')
        self.assertEqual(result['conflicts'][0]['shift_ids'], ['s1', 's2'])

    def test_existing_group_is_one_day_but_revoked_branch_blocks(self):
        rows = [shift(workday_id='w1'), shift('s2', workday_id='w1', brand_id='enbishan')]
        result = preflight.assess(rows, [])
        self.assertTrue(result['metadata_preflight_clear'])
        self.assertEqual(len(result['existing_grouped_workdays']), 1)
        rows[1]['scope_valid'] = False
        result = preflight.assess(rows, [])
        self.assertFalse(result['metadata_preflight_clear'])
        self.assertEqual(result['conflicts'][0]['reason'], 'CURRENT_BRANCH_SCOPE')

    def test_single_driver_needs_start_and_manager_adoption_first(self):
        rows = [shift('manager', user_id='manager'), shift('driver', kind='driver', manager_shift_id='manager')]
        result = preflight.assess(rows, [])
        self.assertIn('MISSING_ORIGINAL_INSPECTION', result['conflicts'][0]['reasons'])
        rows[1]['has_start_inspection'] = True
        result = preflight.assess(rows, [])
        self.assertTrue(result['metadata_preflight_clear'])
        driver = next(r for r in result['legacy_adoption_candidates'] if r['kind'] == 'driver')
        self.assertTrue(driver['manager_adoption_first'])
        rows[0]['workday_id'] = 'wm'
        result = preflight.assess(rows, [])
        self.assertFalse(result['legacy_adoption_candidates'][0]['manager_adoption_first'])

    def test_closing_restricted_and_scope_conflicts_require_review(self):
        for updates, reason in [({'status': 'closing'}, 'LEGACY_REQUIRES_CLOSURE'),
                                ({'morning': True}, 'RESTRICTED_LEGACY_WORKDAY'),
                                ({'receipt_trial': True}, 'RESTRICTED_LEGACY_WORKDAY'),
                                ({'scope_valid': False}, 'CURRENT_BRANCH_SCOPE'),
                                ({'kind': 'driver', 'has_start_inspection': True}, 'MANAGER_NOT_AVAILABLE')]:
            with self.subTest(reason=reason):
                result = preflight.assess([shift(**updates)], [])
                self.assertFalse(result['metadata_preflight_clear'])
                self.assertIn(reason, result['conflicts'][0]['reasons'])

    def test_physical_conflicts_only_emit_ids_and_never_approve_activation(self):
        result = preflight.assess([shift()], [{'shift_ids': ['s2', 's1'], 'vehicle_ids': ['v2', 'v1'], 'plate': 'PRIVATE_PLATE'}])
        self.assertFalse(result['metadata_preflight_clear'])
        self.assertFalse(result['activation_approved'])
        self.assertEqual(result['physical_vehicle_conflicts'], [{'shift_ids': ['s1', 's2'], 'vehicle_ids': ['v1', 'v2']}])
        output = json.dumps(result)
        for value in ('PRIVATE_NAME', 'PRIVATE_PHONE', 'PRIVATE_LOG', 'PRIVATE_PLATE'):
            self.assertNotIn(value, output)
        self.assertFalse(preflight.assess([], [])['activation_approved'])

    def test_collect_rejects_writable_transaction_before_any_source_reads(self):
        class Result:
            def fetchone(self):
                return {'transaction_read_only': 'off'}
        class Connection:
            def execute(self, query):
                if query != 'SHOW transaction_read_only':
                    raise AssertionError('Read occurred before transaction guard')
                return Result()
        with self.assertRaisesRegex(ValueError, 'read-only'):
            preflight.collect(Connection())


if __name__ == '__main__':
    unittest.main()


import pytest
from app import db
from test_enbishan_dual_brand import dual


@pytest.mark.postgres
def test_collect_executes_on_real_isolated_schema_without_changing_workdays(dual, monkeypatch):
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED', 'false')
    _, _, _, open_shifts, _ = dual
    with db.tx() as c:
        c.execute("UPDATE users SET vehicle=%s WHERE id='dual-driver'", (db.json({'plate': 'SYNTHETIC PREFLIGHT 123', 'odometer_km': 1000}),))
    manager, driver = open_shifts('kps')

    def collect_readonly():
        with db.tx() as c:
            c.execute('SET TRANSACTION READ ONLY')
            # The fixture verifies a localhost synthetic DB before this test.
            # Only source identity is replaced, never readonly guard or queries.
            monkeypatch.setattr(preflight._safety, 'DATABASE_NAME', c.info.dbname)
            monkeypatch.setattr(preflight._safety, 'DATABASE_SCHEMA', c.execute('SELECT current_schema() AS name').fetchone()['name'])
            return preflight.collect(c)

    first = collect_readonly()
    assert first['metadata_preflight_clear'], first
    assert {row['shift_id'] for row in first['legacy_adoption_candidates']} == {manager, driver}
    assert first['physical_vehicle_conflicts'] == []
    other_manager, other_driver = open_shifts('enbishan')
    second = collect_readonly()
    assert not second['metadata_preflight_clear']
    assert second['legacy_adoption_candidates'] == []
    assert len(second['conflicts']) == 2
    assert len(second['physical_vehicle_conflicts']) == 1
    assert second['physical_vehicle_conflicts'][0]['shift_ids'] == sorted([driver, other_driver])
    assert len(second['physical_vehicle_conflicts'][0]['vehicle_ids']) == 2
    assert 'SYNTHETIC PREFLIGHT 123' not in json.dumps(second)
    with db.tx() as c:
        rows = c.execute("SELECT id,status FROM shifts WHERE user_id IN ('dual-manager','dual-driver') ORDER BY id").fetchall()
        assert {r['id'] for r in rows} == {manager, driver, other_manager, other_driver}
        assert all(r['status'] == 'open' for r in rows)


def test_startup_summary_drops_all_conflict_and_actor_ids():
    from app.central_activation_preflight import summarize
    report = preflight.assess([shift()], [{'shift_ids': ['PRIVATE_SHIFT'], 'vehicle_ids': ['PRIVATE_VEHICLE']}])
    report['conflicts'] = [{'user_id': 'PRIVATE_USER', 'shift_ids': ['PRIVATE_SHIFT']}]
    value = summarize(report)
    assert value['conflict_count'] == 1
    assert value['physical_vehicle_conflict_count'] == 1
    assert value['legacy_adoption_candidate_count'] == 1
    assert not value['activation_approved']
    assert 'PRIVATE_' not in json.dumps(value)


def test_startup_helper_is_optional_and_never_runs_when_feature_active(monkeypatch):
    from app.central_activation_preflight import run
    monkeypatch.setenv('DATABASE_URL', 'PRIVATE_INVALID_BINDING')
    assert run() is None
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED', 'true')
    assert run(enabled=True)['status'] == 'skipped_feature_active'
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED', 'false')
    report = run(enabled=True)
    assert report['status'] == 'failed'
    assert not report['metadata_preflight_clear']
    assert 'PRIVATE_INVALID_BINDING' not in json.dumps(report)
