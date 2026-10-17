"""Private PostgreSQL snapshot and isolated restore drill. Credentials only in environment."""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db
from app.exports import build_archive, restore_archive, verify_archive
from app import recovery

ENCRYPTED_FORMAT = b'KPSENC1\x00'


def _backup_key():
    try:
        key = base64.b64decode(os.environ['KPS_BACKUP_KEY'], altchars=b'-_', validate=True)
        if len(key) != 32:
            raise ValueError
        return key
    except (KeyError, ValueError):
        raise ValueError('An authorized private 32-byte KPS_BACKUP_KEY is required.') from None


def encrypt_archive(data):
    verify_archive(data, require_full=True)
    nonce = secrets.token_bytes(12)
    return ENCRYPTED_FORMAT + nonce + AESGCM(_backup_key()).encrypt(nonce, data, ENCRYPTED_FORMAT)


def read_archive(path):
    data = Path(path).read_bytes()
    if data.startswith(ENCRYPTED_FORMAT):
        envelope = len(ENCRYPTED_FORMAT)
        data = AESGCM(_backup_key()).decrypt(data[envelope:envelope+12], data[envelope+12:], ENCRYPTED_FORMAT)
    verify_archive(data, require_full=True)
    return data


def private_write(path, contents):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(contents)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise


def record_drill(result):
    config = recovery.configuration()
    if (not config['provider_configured'] or not result.get('verified')
            or not result.get('static_restore_verified') or not result.get('complete_rows_verified')
            or not result.get('sequence_state_verified')):
        raise ValueError('Only a verified full restore for the bound source can be recorded.')
    if result['source_revision'] != (os.getenv('RUNTIME_REVISION') or os.getenv('RENDER_GIT_COMMIT') or 'local-unversioned'):
        raise ValueError('Drill evidence must match the executing source revision.')
    with db.tx() as conn:
        recovery._record(conn, config, db.new_id('restore_drill_'), 'isolated_restore_verified', 'observed',
                         'restore_drill', datetime.now(timezone.utc), db.digest(result), result, backup_state=False)


def record_failure(action):
    """Persist a sanitized local-tool failure through the existing owner queue.

    Local snapshots and restore drills are separate from provider backup truth.
    A disconnected source cannot persist its own alert, so retain an explicit
    private scheduler failure without claiming an external notification arrived.
    """
    events = {'backup': 'portable_backup_failed', 'verify': 'portable_archive_verification_failed',
              'restore-drill': 'isolated_restore_failed'}
    config = recovery.configuration()
    if action not in events or not config['provider_configured']:
        return False
    try:
        with db.tx() as conn:
            recovery._record(conn, config, db.new_id('portable_failure_'), events[action], 'failed',
                             'portable_backup', datetime.now(timezone.utc), db.digest({'action': action}),
                             {'action': action}, backup_state=False)
            recovery.queue_alert_notifications(conn, config)
        return True
    except Exception:
        print('Failure alert could not be persisted; independent scheduler monitoring is required.', file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description='Private backup / separate PostgreSQL restore drill')
    actions = parser.add_subparsers(dest='action', required=True)
    backup = actions.add_parser('backup')
    backup.add_argument('--output', required=True)
    backup.add_argument('--encrypt', action='store_true', help='Encrypt using KPS_BACKUP_KEY from private secrets')
    verify = actions.add_parser('verify')
    verify.add_argument('--input', required=True)
    restore = actions.add_parser('restore-drill')
    restore.add_argument('--input', required=True)
    restore.add_argument('--schema', default=None)
    restore.add_argument('--target-database-id', default=None, help='Explicit internal existing recovery database ID')
    restore.add_argument('--assets-output', default=None, help='New private directory for restored static asset bytes')
    restore.add_argument('--evidence-output', default=None, help='New private file for measured restore evidence')
    restore.add_argument('--record', action='store_true', help='Record verified full restore evidence in the bound source DB')
    args = parser.parse_args()
    try:
        execute(args)
    except Exception:
        record_failure(args.action)
        raise


def execute(args):
    if args.action == 'backup':
        with db.tx() as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            data, manifest = build_archive(conn, full=True)
        path = Path(args.output)
        if args.encrypt:
            data = encrypt_archive(data)
        private_write(path, data)
        try:
            read_archive(path)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        print('Private backup verified. A local file alone does not establish durable external storage.')
    elif args.action == 'verify':
        read_archive(args.input)
        print('Archive integrity verified; this alone is not a restore drill.')
    else:
        target = os.environ.get('KPS_RESTORE_DATABASE_URL')
        if not target:
            raise SystemExit('Set KPS_RESTORE_DATABASE_URL to a separate local test or the bound INTERNAL recovery database.')
        result = restore_archive(read_archive(args.input), target_url=target,
                                 target_schema=args.schema or 'kps_restore_' + uuid.uuid4().hex,
                                 target_database_id=args.target_database_id, asset_directory=args.assets_output)
        if args.evidence_output:
            private_write(Path(args.evidence_output), json.dumps(result, sort_keys=True, separators=(',', ':')).encode())
        if args.record:
            record_drill(result)
        print('Restore verified in a NEW test schema. Source data was not overwritten.')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Operation failed. Review private environment, input format and target restrictions.', file=sys.stderr)
        raise SystemExit(1) from None
