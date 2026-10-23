"""Safety boundaries for the private read-only evidence tool, without a DB."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('release_preflight', Path(__file__).resolve().parents[1] / 'tools' / 'production_release_preflight.py')
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


class ProductionReleasePreflightSafety(unittest.TestCase):
    def test_target_is_fixed_and_connection_indirection_cannot_override_it(self):
        valid = f'postgresql://user:private@{preflight.DATABASE_ID}/{preflight.DATABASE_NAME}'
        self.assertEqual(preflight.validate_target(valid), valid)
        for invalid in (
            valid.replace(preflight.DATABASE_ID, 'localhost'),
            valid.replace(preflight.DATABASE_NAME, 'kps_apex_db'),
            valid + '?hostaddr=127.0.0.1', valid + '?service=unrelated',
            valid + '?options=-cdefault_transaction_read_only=off', valid + '#secret',
            valid + '?sslmode=require&sslmode=disable',
            valid.replace(preflight.DATABASE_ID, preflight.DATABASE_ID + '.frankfurt-postgres.render.com'),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, '^The existing KPS ORDER database binding is required.$'):
                    preflight.validate_target(invalid)
        external = valid.replace(preflight.DATABASE_ID, preflight.DATABASE_ID + '.frankfurt-postgres.render.com') + '?sslmode=require'
        self.assertEqual(preflight.validate_target(external), external)
        for key in ('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS'):
            with patch.dict(os.environ, {key: 'private-override'}):
                with self.assertRaisesRegex(ValueError, '^The existing KPS ORDER database binding is required.$'):
                    preflight.validate_target(valid)

    def test_unknown_or_changed_registry_prevents_checksum_compatibility(self):
        catalog = {'001_core.sql': {'sha256': 'good', 'kind': 'normal_startup'},
                   '002_guarded.sql': {'sha256': 'guard', 'kind': 'guarded_multibrand'}}
        rows = [{'version': '001_core.sql', 'sha256': 'good', 'applied_at': 'now'}]
        report = preflight.compare_registry(rows, catalog)
        self.assertTrue(report['compatible_applied_checksums'])
        self.assertEqual(report['pending'][0]['kind'], 'guarded_multibrand')
        for bad in ([dict(rows[0], sha256='changed')], rows + [{'version': 'unknown.sql', 'sha256': 'x', 'applied_at': 'now'}]):
            self.assertFalse(preflight.compare_registry(bad, catalog)['compatible_applied_checksums'])

    def test_permission_readback_excludes_phone_name_password_and_other_capabilities(self):
        row = {'id': 'staff1', 'active': True, 'role': 'admin', 'brand_id': 'kps', 'branch_ids': ['branch1'],
               'permissions': ['fleet_active_swap', 'branch_printers_manage', 'accounting_reports'],
               'extra_permissions': [], 'permission_denials': ['branch_printers_manage'],
               'password_hash': 'private-password', 'phone': 'private-phone', 'display_name': 'private-name'}
        report = preflight.staff_projection(row)
        text = json.dumps(report)
        self.assertTrue(report['source_active_swap_role_and_capability'])
        self.assertFalse(report['source_printer_capability'])
        self.assertEqual(report['relevant_effective_capabilities'], ['fleet_active_swap'])
        for secret in ('private-password', 'private-phone', 'private-name', 'accounting_reports'):
            self.assertNotIn(secret, text)
        self.assertFalse(preflight.staff_projection(dict(row, role='driver'))['source_active_swap_role_and_capability'])
        self.assertFalse(preflight.staff_projection(dict(row, active=False))['source_active_swap_role_and_capability'])

    def test_evidence_file_is_private_and_refuses_overwrite_or_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'evidence.json'
            preflight.private_write(path, {'release_approved': False})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                preflight.private_write(path, {'release_approved': True})
            self.assertEqual(path.read_bytes(), original)
            link = Path(directory) / 'link.json'
            link.symlink_to(path)
            with self.assertRaises(FileExistsError):
                preflight.private_write(link, {})
            self.assertEqual(path.read_bytes(), original)

    def test_collect_refuses_write_transaction_before_any_source_read(self):
        class Result:
            def fetchone(self):
                return {'transaction_read_only': 'off'}
        class Connection:
            def execute(self, query):
                if query != 'SHOW transaction_read_only':
                    raise AssertionError('Attempted source read in a write transaction')
                return Result()
        with self.assertRaisesRegex(ValueError, 'read-only'):
            preflight.collect(Connection(), {})

    def test_staff_phone_variants_are_canonical_and_invalid_phone_is_sanitized(self):
        for phone in ('050 000 1234', '+966 50 000 1234', '00966500001234', '500001234', '٠٥٠٠٠٠١٢٣٤'):
            self.assertEqual(preflight.normalize_staff_phone(phone), '966500001234')
        with self.assertRaisesRegex(ValueError, '^A Saudi staff phone is required.$'):
            preflight.normalize_staff_phone('private-nonphone')


if __name__ == '__main__':
    unittest.main()
