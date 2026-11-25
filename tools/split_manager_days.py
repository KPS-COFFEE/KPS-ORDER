"""Preview or apply one owner-authorized manager-day correction, inside Render.

Uses the existing bound database only. Never prints credentials or modifies the
network allowlist. Default mode only previews; apply requires the reviewed hash.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.production_release_preflight import validate_target
from app import db, operations as ops, manager_day_split, central_workdays


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-shift', required=True)
    parser.add_argument('--owner-id', required=True)
    parser.add_argument('--apply-fingerprint')
    parser.add_argument('--reason', default='')
    args = parser.parse_args()
    validate_target(os.environ.get('DATABASE_URL', ''))
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        central_workdays.lock_lanes(c)
        owner = c.execute("SELECT * FROM users WHERE id=%s AND active AND role='owner'", (args.owner_id,)).fetchone()
        if not owner:
            raise ValueError('A verified active owner identity is required.')
        if args.apply_fingerprint:
            result = manager_day_split.apply(c, owner, args.source_shift, args.apply_fingerprint, args.reason)
            output = {'applied': True, **result}
        else:
            result = manager_day_split.plan(c, owner, args.source_shift)
            output = {'applied': False, 'fingerprint': result['fingerprint'], 'validation': result['validation'],
                      'days': {day: [{'shift_id': s['id'], 'brand_id': s['brand_id'],
                                    'opened_at': s['opened_at']} for s in rows]
                               for day, rows in result['groups'].items()}}
    print(json.dumps(ops.safe(output), ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
