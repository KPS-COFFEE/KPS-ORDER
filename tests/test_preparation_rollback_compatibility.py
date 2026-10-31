"""Candidate creates a route; the immutable prior image must operate it safely."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
from app import db
from test_preparation_routing import routing
from test_operations import ops
from test_rollback_application_compatibility import assert_candidate_source_identity

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FIELDS = ('brand_id', 'branch_id', 'customer_id', 'snapshot', 'customer', 'subtotal_halalas',
                 'delivery_fee_halalas', 'total_halalas', 'payment_method', 'payment_status')


def prior_probe(spec):
    probe = ROOT / 'tools/preparation_rollback_probe.py'
    image = os.environ.get('ROLLBACK_COMPAT_IMAGE')
    if image:
        assert re.fullmatch(r'(?:[^\s]+@)?sha256:[0-9a-f]{64}', image), 'An immutable prior image is required.'
        command = ['docker', 'run', '--rm', '--network', 'host', '--entrypoint', 'python',
                   '--volume', str(probe) + ':/probe.py:ro', '--env', 'DATABASE_URL', '--env', 'APP_ENV=testing',
                   '--env', 'PUBLIC_BASE_URL=http://testserver', '--env', 'APP_ALLOWED_HOSTS=testserver,localhost,127.0.0.1',
                   '--env', 'SESSION_COOKIE_SECURE=false', '--env', 'OTP_PROVIDER=disabled', '--env', 'EMAIL_PROVIDER=disabled',
                   '--env', 'REPORT_WORKER_ENABLED=false', '--env', 'EMAIL_WORKER_ENABLED=false',
                   '--env', 'RECOVERY_POLL_ENABLED=false', '--env', 'DISPATCH_OFFER_ACCEPT_ENABLED=false',
                   '--env', 'DISPATCH_AUTO_ASSIGN_ENABLED=false', image, '/probe.py', '/app', json.dumps(spec)]
    else:
        command = [sys.executable, str(probe), os.environ['ROLLBACK_COMPAT_DIR'], json.dumps(spec)]
    result = subprocess.run(command, cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + '\n' + result.stderr
    evidence = json.loads(result.stdout.strip().splitlines()[-1])
    manifest_path = ROOT / 'tools/rollback_application_overlay/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    assert evidence['app_orders_sha256'] == manifest['files']['app/orders.py']
    assert evidence['ops_js_sha256'] == manifest['files']['public/ops.js']
    if image:
        identity = evidence['source_identity']
        assert identity['baseline_sha'] == manifest['baseline_sha']
        assert identity['overlay_manifest_sha256'] == hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        assert_candidate_source_identity(identity)
    return evidence


def immutable_source(conn, oid):
    row = conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
    return {'order': {key: row[key] for key in SOURCE_FIELDS},
            'routes': conn.execute('SELECT * FROM order_preparation_routes WHERE order_id=%s ORDER BY id', (oid,)).fetchall(),
            'money': {table: conn.execute('SELECT * FROM ' + table + ' WHERE order_id=%s ORDER BY id', (oid,)).fetchall()
                      for table in ('collections', 'refunds', 'fiscal_documents')}}


@pytest.mark.skipif(not (os.environ.get('ROLLBACK_COMPAT_DIR') or os.environ.get('ROLLBACK_COMPAT_IMAGE')),
                    reason='Dedicated prior application certification requires prepared prior source/image')
@pytest.mark.postgres
@pytest.mark.parametrize('scenario', ['unprinted', 'accepted'])
def test_existing_preparation_route_operates_in_exact_prior_without_financial_rewrite(routing, scenario):
    r = routing
    oid = r['order'](workflow='branch_fulfillment' if scenario == 'unprinted' else 'manager_dispatch')
    route = r['choose'](oid)
    spec = {'scenario': scenario, 'order_id': oid, 'source_branch_id': r['source'], 'target_branch_id': r['target']}
    if scenario == 'accepted':
        _, shift = r['both_shifts']()
        task = r['post']('manager', '/api/orders/' + oid + '/assign', {'driver_shift_id': shift, 'accept_order': True})['task']
        r['post']('driver', '/api/tasks/' + task['id'] + '/transition', {'status': 'accepted'})
        with db.tx() as conn:
            accepted = conn.execute('SELECT accepted_at FROM driver_tasks WHERE id=%s', (task['id'],)).fetchone()['accepted_at']
        spec.update(task_id=task['id'], shift_id=shift, accepted_at=accepted.isoformat())
    with db.tx() as conn:
        before = immutable_source(conn, oid)
        assert not conn.execute('SELECT 1 FROM branch_print_jobs WHERE order_id=%s', (oid,)).fetchone()
    evidence = prior_probe(spec)
    assert evidence['synthetic_only'] and evidence['database_restored'] is False
    assert evidence['health'] == evidence['ready'] == 200
    assert evidence['route_id'] == route['route_id']
    assert evidence['target_queue'] and evidence['generic_target_access_denied'] and evidence['source_preparation_denied']
    assert evidence['new_route_creation_denied'] and evidence['print_destination'] == r['target']
    assert evidence['exported_preparation_routes'] == 1
    if scenario == 'unprinted':
        assert evidence['preparation_status'] == 'ready'
    else:
        assert evidence['accepted_at_preserved'] and evidence['accepted_at'] == spec['accepted_at']
        assert evidence['driver_pickup'] == evidence['trip_departure'] == r['target']
    with db.tx() as conn:
        assert immutable_source(conn, oid) == before
    # The same schema then re-enters the candidate, with no restore/clock reset.
    db.migrate()
    with db.tx() as conn:
        assert immutable_source(conn, oid) == before
        proof = conn.execute("SELECT details FROM audit_events WHERE action='release.operational_data_preserved' "
                             "ORDER BY created_at DESC,id DESC LIMIT 1").fetchone()['details']
        assert proof['checks']['order_preparation_routes']['count'] == 1
    artifacts = ROOT / 'test-artifacts'
    artifacts.mkdir(exist_ok=True)
    evidence.update(immutable_source_preserved=True, reupgrade_preserved=True)
    (artifacts / ('preparation-rollback-' + scenario + '.json')).write_text(json.dumps(evidence, indent=2, default=str) + '\n')
