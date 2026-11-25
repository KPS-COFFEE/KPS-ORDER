"""Fixed-production, read-only central rollout metadata; never opens/adopts shifts."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import sys

# Load only the independent evidence tool, never application startup or migrations.
_spec = importlib.util.spec_from_file_location('central_release_safety', Path(__file__).with_name('production_release_preflight.py'))
_safety = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_safety)


def assess(shifts, plate_conflicts):
    """Classify projected IDs/booleans; never expose input row payloads."""
    by_user = defaultdict(list)
    by_id = {s['id']: s for s in shifts}
    for shift in shifts:
        by_user[shift['user_id']].append(shift)
    candidates, conflicts, grouped = [], [], []
    for user_id, rows in sorted(by_user.items()):
        identifiers = sorted(r['id'] for r in rows)
        groups = {r.get('workday_id') for r in rows if r.get('workday_id')}
        if groups and len(groups) == 1 and all(r.get('workday_id') for r in rows):
            bad = [r['id'] for r in rows if not r['scope_valid']]
            if bad:
                conflicts.append({'user_id': user_id, 'shift_ids': bad, 'reason': 'CURRENT_BRANCH_SCOPE'})
            else:
                grouped.append({'user_id': user_id, 'workday_id': next(iter(groups)), 'shift_ids': identifiers})
            continue
        if len(rows) != 1:
            conflicts.append({'user_id': user_id, 'shift_ids': identifiers, 'reason': 'MULTIPLE_EXISTING_WORKDAYS'})
            continue
        row = rows[0]
        reasons = []
        if not row['scope_valid']:
            reasons.append('CURRENT_BRANCH_SCOPE')
        if row['status'] != 'open':
            reasons.append('LEGACY_REQUIRES_CLOSURE')
        if row['morning'] or row['receipt_trial']:
            reasons.append('RESTRICTED_LEGACY_WORKDAY')
        if row['kind'] == 'driver' and not row['has_start_inspection']:
            reasons.append('MISSING_ORIGINAL_INSPECTION')
        parent = by_id.get(row.get('manager_shift_id'))
        if row['kind'] == 'driver' and (not parent or parent['status'] != 'open' or not parent['scope_valid']):
            reasons.append('MANAGER_NOT_AVAILABLE')
        if reasons:
            conflicts.append({'user_id': user_id, 'shift_ids': identifiers, 'reasons': reasons})
        else:
            candidates.append({'user_id': user_id, 'shift_id': row['id'], 'kind': row['kind'],
                               'manager_adoption_first': row['kind'] == 'driver' and not bool(parent.get('workday_id'))})
    duplicates = [{'shift_ids': sorted(row['shift_ids']), 'vehicle_ids': sorted(row['vehicle_ids'])}
                  for row in plate_conflicts]
    return {'active_shift_count': len(shifts), 'active_employee_count': len(by_user),
            'existing_grouped_workdays': grouped, 'legacy_adoption_candidates': candidates,
            'conflicts': conflicts, 'physical_vehicle_conflicts': duplicates,
            'metadata_preflight_clear': not conflicts and not duplicates,
            'activation_approved': False,
            'note': 'Candidates require canonical permission, branch availability and manager coverage checks at adoption. No adoption, cash transfer or shift merge has been performed.'}


def collect(conn):
    if conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] != 'on':
        raise ValueError('A read-only transaction is required.')
    source = conn.execute('SELECT current_database() AS database_name,current_schema() AS schema_name').fetchone()
    if source['database_name'] != _safety.DATABASE_NAME or source['schema_name'] != _safety.DATABASE_SCHEMA:
        raise ValueError('Unexpected source identity.')
    tables = conn.execute("SELECT to_regclass('central_workdays') IS NOT NULL AS days,to_regclass('central_workday_legs') IS NOT NULL AS legs,to_regclass('staff_brand_memberships') IS NOT NULL AS grants").fetchone()
    if not tables['grants'] or tables['days'] != tables['legs']:
        raise ValueError('Required membership/group schema is inconsistent.')
    grouping = 'LEFT JOIN central_workday_legs leg ON leg.shift_id=s.id' if tables['legs'] else ''
    workday = 'leg.workday_id' if tables['legs'] else 'NULL::text'
    # Only IDs, scope and lifecycle booleans leave PostgreSQL. Never select logs,
    # names, phones, notes, financial amounts, evidence or plate text.
    shifts = conn.execute(f"""SELECT s.id,s.user_id,s.brand_id,s.kind,s.status,s.manager_shift_id,
       {workday} AS workday_id,
       coalesce(s.delivery_window_snapshot->>'period'='morning',false) AS morning,
       coalesce(s.vehicle_log->'receipt_trial' NOT IN ('null'::jsonb,'false'::jsonb,'{{}}'::jsonb,'[]'::jsonb,'0'::jsonb,'""'::jsonb),false) AS receipt_trial,
       coalesce(s.vehicle_log->'start'<>'null'::jsonb,false) AS has_start_inspection,
       (u.active AND b.active AND (s.brand_id=u.brand_id OR m.active)
        AND (u.role='owner' AND s.brand_id=u.brand_id OR
          s.branch_ids <@ CASE WHEN s.brand_id=u.brand_id THEN u.branch_ids ELSE coalesce(m.branch_ids,'[]'::jsonb) END)
        AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(s.branch_ids) x(id)
          WHERE NOT EXISTS(SELECT 1 FROM branches br WHERE br.id=x.id AND br.brand_id=s.brand_id))) AS scope_valid
       FROM shifts s JOIN users u ON u.id=s.user_id JOIN brands b ON b.id=s.brand_id
       LEFT JOIN staff_brand_memberships m ON m.user_id=u.id AND m.brand_id=s.brand_id
       {grouping} WHERE s.status<>'closed' ORDER BY s.user_id,s.id""").fetchall()
    plates = conn.execute("""SELECT array_agg(DISTINCT s.id ORDER BY s.id) AS shift_ids,
       array_agg(DISTINCT v.id ORDER BY v.id) AS vehicle_ids
       FROM fleet_operating_segments f JOIN shifts s ON s.id=f.shift_id
       JOIN fleet_vehicles v ON v.id=f.vehicle_id
       WHERE s.status IN ('open','closing') AND f.returned_at IS NULL AND v.plate_key<>''
       GROUP BY v.plate_key HAVING count(DISTINCT s.id)>1 ORDER BY min(s.id)""").fetchall()
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'mode': 'READ_ONLY_METADATA',
            'database_id': _safety.DATABASE_ID, 'group_schema_present': tables['days'],
            **assess(shifts, plates)}


def main():
    parser = argparse.ArgumentParser(description='Private central activation metadata; performs no mutations and approves no release')
    parser.add_argument('--output', required=True, help='New private evidence file')
    args = parser.parse_args()
    try:
        url = _safety.validate_target(os.environ.get('DATABASE_URL', ''))
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=5,
                             options='-c default_transaction_read_only=on -c search_path=public',
                             application_name='kps_central_readonly_preflight') as conn:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                conn.execute("SET LOCAL statement_timeout='2s'")
                conn.execute("SET LOCAL lock_timeout='250ms'")
                conn.execute("SET LOCAL idle_in_transaction_session_timeout='5s'")
                conn.execute("SET LOCAL transaction_timeout='15s'")
                report = collect(conn)
        _safety.private_write(args.output, report)
    except Exception:
        print('Central read-only preflight failed; check private binding, schema and output path. No activation is approved.', file=sys.stderr)
        return 1
    print('Private central metadata saved. No shifts changed and no activation approved.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
