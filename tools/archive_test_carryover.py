"""Exact-row owner-classified test archival; caller owns transaction/write pause.

No CLI or public endpoint: deployment operator must provide the reviewed row
identity, original timestamp, source shift, owner and amount. No financial
records or historical reports are removed. Application rollback must retain
the archive-aware shift-opening query; the DB check also blocks consumption
by older code rather than silently reviving test custody.
"""
from app import db


def archive(c, *, owner_id, expected, reason):
    required = {'id', 'user_id', 'from_shift_id', 'amount_halalas', 'created_at'}
    if set(expected) != required or not isinstance(reason, str) or not reason.strip():
        raise ValueError('Exact reviewed identity and owner classification reason required')
    owner = c.execute('SELECT id,role,active,brand_id FROM users WHERE id=%s', (owner_id,)).fetchone()
    if not owner or owner['role'] != 'owner' or not owner['active']:
        raise ValueError('Active owner required')
    c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('shift:' + expected['user_id'],))
    row = c.execute('SELECT * FROM retained_carryovers WHERE id=%s FOR UPDATE', (expected['id'],)).fetchone()
    if not row or any(row[key] != value for key, value in expected.items()):
        raise ValueError('Reviewed carryover changed')
    source = c.execute('SELECT user_id,brand_id,status FROM shifts WHERE id=%s', (row['from_shift_id'],)).fetchone()
    if not source or source['status'] != 'closed' or source['user_id'] != row['user_id'] or source['brand_id'] != owner['brand_id']:
        raise ValueError('Closed source shift in owner scope required')
    if row['to_shift_id'] is not None or row['carried_at'] is not None:
        raise ValueError('Already consumed carryover cannot be archived')
    if row['archived_test_at'] is not None:
        if row['archived_test_by'] == owner_id and row['archived_test_reason'] == reason.strip():
            return {'id': row['id'], 'already_archived': True}
        raise ValueError('Existing classification differs')
    if c.execute("SELECT 1 FROM shifts WHERE user_id=%s AND status<>'closed'", (row['user_id'],)).fetchone():
        raise ValueError('Driver has an active shift')
    c.execute('UPDATE retained_carryovers SET archived_test_at=now(),archived_test_by=%s,archived_test_reason=%s WHERE id=%s', (owner_id, reason.strip(), row['id']))
    db.audit(c, owner_id, 'carryover.archive_owner_classified_test', 'retained_carryover', row['id'], {
        'classification': 'owner_confirmed_test', 'reason': reason.strip(),
        'amount_halalas_preserved': row['amount_halalas'], 'from_shift_id': row['from_shift_id'],
        'user_id': row['user_id'], 'source_created_at': row['created_at'].isoformat(),
        'financial_settlement_created': False,
    })
    return {'id': row['id'], 'already_archived': False}
