"""Private metadata-only preflight. Never imports startup or runs migrations.

Run only in the already authorized KPS ORDER private service environment. The
database binding is deliberately fixed; this is not a general SQL runner.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DATABASE_ID = 'dpg-db4rjgo473hc738soueg-a'
DATABASE_NAME = 'kps_order_emergency_v4_db'
DATABASE_SCHEMA = 'public'
HOSTS = {DATABASE_ID, DATABASE_ID + '.frankfurt-postgres.render.com'}
CAPABILITIES = {'vehicle_maintenance_manage', 'fleet_active_swap', 'branch_printers_manage',
                'drivers_manage', 'drivers_view', 'delivery_assign'}
# Applied on the retained database, but absent from the current startup branch.
# Independently matched to the original Git object; this SQL must not be rerun
# or silently treated as an unverified unknown migration.
HISTORICAL_APPLIED = {
    '030_menu_preview.sql': {
        'sha256': '34a7e4f5c08094a32faf2f2d09b10a79530b352ecc1ff78a88c9ab60fc04ce2e',
        'kind': 'historical_applied',
        'source_revision': 'e109108bf97270dbfbfabf71b142c03338e4089f',
    },
}


def validate_target(value):
    """Reject alternate services, hostaddr, options and unrelated project DSNs."""
    try:
        if any(os.getenv(key) for key in ('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS')):
            raise ValueError
        parsed = urlsplit(value)
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        if (parsed.scheme not in {'postgres', 'postgresql'} or parsed.hostname not in HOSTS
                or unquote(parsed.path) != '/' + DATABASE_NAME or parsed.fragment
                or parsed.port not in {None, 5432} or not parsed.username
                or any(key not in {'sslmode', 'channel_binding'} for key, _ in query)
                or len({key for key, _ in query}) != len(query)):
            raise ValueError
        if parsed.hostname != DATABASE_ID and dict(query).get('sslmode') not in {'require', 'verify-ca', 'verify-full'}:
            raise ValueError
    except Exception:
        raise ValueError('The existing KPS ORDER database binding is required.') from None
    return value


def migration_catalog(root=ROOT):
    """Use exactly the text/hash operation used by app.db and multibrand.migrate."""
    result = {}
    for directory, kind in ((root / 'migrations', 'normal_startup'),
                            (root / 'app' / 'multibrand_migrations', 'guarded_multibrand')):
        for path in sorted(directory.glob('*.sql')):
            if path.name in result:
                raise ValueError('Ambiguous migration filename in candidate source.')
            result[path.name] = {'sha256': hashlib.sha256(path.read_text().encode()).hexdigest(), 'kind': kind}
    if not result:
        raise ValueError('The complete candidate migration source is required.')
    if result.keys() & HISTORICAL_APPLIED.keys():
        raise ValueError('A historical migration must not become pending startup SQL.')
    result.update({name: dict(record) for name, record in HISTORICAL_APPLIED.items()})
    return result


def compare_registry(rows, catalog):
    applied = {row['version']: row['sha256'] for row in rows}
    unknown = sorted(set(applied) - set(catalog))
    changed = sorted(name for name in set(applied) & set(catalog) if applied[name] != catalog[name]['sha256'])
    pending = [{'version': name, **catalog[name]} for name in sorted(set(catalog) - set(applied))
               if catalog[name]['kind'] != 'historical_applied']
    return {'compatible_applied_checksums': not unknown and not changed,
            'unknown_applied_versions': unknown, 'changed_applied_versions': changed,
            'applied': [{'version': row['version'], 'sha256': row['sha256'], 'applied_at': row['applied_at']} for row in rows],
            'historical_applied': [{'version': name, **catalog[name]} for name in sorted(set(applied) & set(catalog))
                                   if catalog[name]['kind'] == 'historical_applied'],
            'pending': pending,
            'note': 'Matching registry checksums do not certify lock duration, recovery or permission to migrate.'}


def normalize_staff_phone(value):
    compact = re.sub(r'[\s()+.\-\u200e\u200f\u202a-\u202e\u2066-\u2069]', '', value or '')
    compact = compact.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹', '01234567890123456789'))
    if compact.startswith('00966'):
        compact = compact[2:]
    if re.fullmatch(r'05\d{8}', compact):
        compact = '966' + compact[1:]
    if re.fullmatch(r'5\d{8}', compact):
        compact = '966' + compact
    if not re.fullmatch(r'9665\d{8}', compact):
        raise ValueError('A Saudi staff phone is required.')
    return compact


def staff_projection(row):
    # Load only the pure source policy, not auth/main/db or runtime feature gates.
    spec = importlib.util.spec_from_file_location('release_employee_permissions', ROOT / 'app' / 'employee_permissions.py')
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    effective = policy.effective(row)
    return {'account_id': row['id'], 'active': row['active'], 'role': row['role'],
            'home_brand_id': row['brand_id'], 'branch_ids': row['branch_ids'],
            'relevant_effective_capabilities': sorted(effective & CAPABILITIES),
            'source_active_swap_role_and_capability': bool(row['active'] and row['role'] in {'owner', 'admin'} and 'fleet_active_swap' in effective),
            'source_printer_capability': bool(row['active'] and row['role'] != 'customer' and 'branch_printers_manage' in effective),
            'note': 'Current home-brand source policy projection; actual route/resource scope and operating prerequisites still apply.'}


def collect(conn, catalog, staff_phone=None):
    if conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] != 'on':
        raise ValueError('A read-only transaction is required.')
    source = conn.execute('SELECT current_database() AS database_name,current_schema() AS schema_name,current_setting(\'server_version\') AS server_version').fetchone()
    if source['database_name'] != DATABASE_NAME or source['schema_name'] != DATABASE_SCHEMA:
        raise ValueError('Unexpected source identity.')
    registry = conn.execute('SELECT version,sha256,applied_at FROM schema_migrations ORDER BY version').fetchall()
    revision = os.getenv('RUNTIME_REVISION') or os.getenv('RENDER_GIT_COMMIT') or ''
    report = {'generated_at': datetime.now(timezone.utc).isoformat(), 'mode': 'READ_ONLY_METADATA',
              'database_id': DATABASE_ID, 'source': source,
              'runtime_revision': revision if re.fullmatch(r'[a-f0-9]{40}', revision) else None,
              'registry': compare_registry(registry, catalog),
              'release_approved': False, 'backup_verified': False, 'restore_verified': False,
              'migration_duration_verified': False, 'production_migration_permitted': False}
    report['table_sizes'] = conn.execute("SELECT c.relname AS table_name,c.reltuples::bigint AS estimated_rows,pg_total_relation_size(c.oid) AS total_bytes FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind='r' ORDER BY total_bytes DESC LIMIT 30", (DATABASE_SCHEMA,)).fetchall()
    report['lock_counts'] = conn.execute("SELECT mode,granted,count(*) AS count FROM pg_locks WHERE database=(SELECT oid FROM pg_database WHERE datname=current_database()) GROUP BY mode,granted ORDER BY mode,granted").fetchall()
    report['recovery_observations'] = conn.execute('SELECT brand_id,last_success_at,last_failure_at,last_verified_at,verification_source,recoverable_from,recoverable_until FROM recovery_state WHERE database_id=%s ORDER BY brand_id', (DATABASE_ID,)).fetchall()
    report['last_recorded_restore_observation'] = conn.execute("SELECT brand_id,outcome,source,occurred_at,verified_at FROM recovery_events WHERE database_id=%s AND event_type='isolated_restore_verified' ORDER BY occurred_at DESC LIMIT 1", (DATABASE_ID,)).fetchone()
    report['recovery_note'] = 'Observations are not a verified current private backup artifact or independent restore/reconciliation certificate.'
    if staff_phone:
        phone = normalize_staff_phone(staff_phone)
        rows = conn.execute("SELECT id,active,role,brand_id,branch_ids,permissions,permission_denials,extra_permissions FROM users WHERE role<>'customer' AND phone=ANY(%s) ORDER BY id LIMIT 10", ([phone, '+' + phone, '0' + phone[3:]],)).fetchall()
        report['staff_readback'] = {'phone_last4': phone[-4:], 'accounts': [staff_projection(row) for row in rows],
                                    'membership_readback': 'not_requested_no_cross_brand_projection'}
    return report


def private_write(path, value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description='Private, fixed-target, read-only production release evidence; never authorizes a deploy')
    parser.add_argument('--output', required=True, help='New private metadata file, never a shared report')
    parser.add_argument('--staff-phone-env', help='Name of private environment variable holding one authorized staff phone')
    args = parser.parse_args()
    # No DSN/phone is accepted on command line or printed on failure.
    try:
        url = validate_target(os.environ.get('DATABASE_URL', ''))
        phone = None
        if args.staff_phone_env:
            if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', args.staff_phone_env):
                raise ValueError
            phone = normalize_staff_phone(os.environ.get(args.staff_phone_env, ''))
        catalog = migration_catalog()
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=5,
                             options='-c default_transaction_read_only=on -c search_path=public',
                             application_name='kps_private_readonly_release_preflight') as conn:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                conn.execute("SET LOCAL statement_timeout='2s'")
                conn.execute("SET LOCAL lock_timeout='250ms'")
                conn.execute("SET LOCAL idle_in_transaction_session_timeout='5s'")
                conn.execute("SET LOCAL transaction_timeout='15s'")
                report = collect(conn, catalog, phone)
        private_write(args.output, report)
    except Exception:
        print('Read-only preflight failed; no release is approved. Review the private binding, schema, timeout and output path.', file=sys.stderr)
        return 1
    print('Private read-only evidence saved. Production release, backup recovery and permission changes remain unapproved.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
