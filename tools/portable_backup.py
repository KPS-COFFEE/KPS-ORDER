"""Private PostgreSQL snapshot and isolated restore drill. Credentials only in environment."""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db
from app.exports import build_archive, restore_archive, verify_archive


def main():
    parser = argparse.ArgumentParser(description='Private backup / separate PostgreSQL restore drill')
    actions = parser.add_subparsers(dest='action', required=True)
    backup = actions.add_parser('backup')
    backup.add_argument('--output', required=True)
    verify = actions.add_parser('verify')
    verify.add_argument('--input', required=True)
    restore = actions.add_parser('restore-drill')
    restore.add_argument('--input', required=True)
    restore.add_argument('--schema', default=None)
    args = parser.parse_args()
    if args.action == 'backup':
        with db.tx() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            data, manifest = build_archive(conn, full=True)
        path = Path(args.output)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                output.write(data)
            verify_archive(path.read_bytes(), require_full=True)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        print('Private backup verified. Preserve it outside the web service in protected storage.')
    elif args.action == 'verify':
        verify_archive(Path(args.input).read_bytes())
        print('Archive integrity verified; this alone is not a restore drill.')
    else:
        target = os.environ.get('KPS_RESTORE_DATABASE_URL')
        if not target:
            raise SystemExit('Set KPS_RESTORE_DATABASE_URL to a separate local test database.')
        restore_archive(Path(args.input).read_bytes(), target_url=target,
                        target_schema=args.schema or 'kps_restore_' + uuid.uuid4().hex)
        print('Restore verified in a NEW test schema. Source data was not overwritten.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Operation failed. Review private environment, input format and target restrictions.', file=sys.stderr)
        raise SystemExit(1) from None
