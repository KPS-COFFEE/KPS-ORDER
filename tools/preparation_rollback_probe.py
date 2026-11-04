"""Run the exact prior application against a candidate-created TEST route.

This probe never restores a database or uses production credentials. All
operational actions pass through the prior image's ordinary authenticated API.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import uuid4

parts = urlsplit(os.environ.get('DATABASE_URL', ''))
if (os.environ.get('APP_ENV') != 'testing' or parts.scheme not in {'postgresql', 'postgres'}
        or parts.hostname not in {'localhost', '127.0.0.1', '::1', 'postgres'}
        or not (parts.path.lstrip('/').startswith('test_') or parts.path.endswith('_test'))):
    raise RuntimeError('Preparation rollback probe requires an isolated local TEST database.')

source = Path(sys.argv[1])
spec = json.loads(sys.argv[2])
sys.path.insert(0, str(source))
from fastapi.testclient import TestClient
from PIL import Image
from app import auth, db, exports
from app.main import app

oid, target, original = spec['order_id'], spec['target_branch_id'], spec['source_branch_id']
out = {'synthetic_only': True, 'database_restored': False, 'scenario': spec['scenario'],
       'app_orders_sha256': hashlib.sha256((source / 'app/orders.py').read_bytes()).hexdigest(),
       'ops_js_sha256': hashlib.sha256((source / 'public/ops.js').read_bytes()).hexdigest()}
identity = source / 'app/rollback_source_identity.json'
if identity.exists():
    out['source_identity'] = json.loads(identity.read_text())

db.migrate()
with TestClient(app, base_url='http://testserver', headers={'Origin': 'http://testserver'}) as client:
    def actor(name):
        with db.tx() as conn:
            user = conn.execute('SELECT * FROM users WHERE id=%s', ('ops-' + name,)).fetchone()
        assert user and user['active']
        app.dependency_overrides[auth.current_user] = lambda: user
        return client

    def get(name, path, status=200, **kwargs):
        response = actor(name).get(path, **kwargs)
        assert response.status_code == status, response.text
        return response.json()

    def post(name, path, body, status=200):
        response = actor(name).post(path, json=body, headers={'Idempotency-Key': uuid4().hex})
        assert response.status_code == status, response.text
        return response.json()

    out['health'] = client.get('/health').status_code
    out['ready'] = client.get('/ready').status_code
    assert out['health'] == out['ready'] == 200
    queue = get('target', '/api/preparation-routing/tasks')['preparation_tasks']
    task = next(row for row in queue if row['id'] == oid)
    assert task['preparation_branch']['id'] == target
    assert all(word not in json.dumps(task) for word in ('halalas', 'phone', 'receipt_profile'))
    get('target', '/api/orders/' + oid, status=404)
    get('branch', '/api/preparation-routing/tasks/' + oid, status=404)
    source_denial = post('branch', '/api/orders/' + oid + '/preparation',
                        {'version': task['version'], 'status': 'accepted'}, status=409)
    assert source_denial['detail']['code'] == 'PREPARATION_TARGET_QUEUE'
    disabled = post('manager', '/api/preparation-routing/orders/' + oid,
                    {'version': task['version'], 'target_branch_id': original,
                     'expected_preparation_branch_id': target, 'reason': 'No new routes in prior image'}, status=409)
    assert disabled['detail']['code'] == 'PREPARATION_ROUTE_PRIOR_READ_ONLY'
    devices = get('manager', '/api/printing/operational-devices', params={'order_id': oid})['devices']
    assert [device['branch_id'] for device in devices] == [target]
    post('manager', '/api/printing/orders/' + oid, {'device_id': 'printer-' + original}, status=409)
    post('manager', '/api/printing/orders/' + oid, {'device_id': 'printer-' + target, 'kind': 'both'})
    out.update(target_queue=True, generic_target_access_denied=True, source_preparation_denied=True,
               new_route_creation_denied=True, print_destination=target)

    if spec['scenario'] == 'unprinted':
        for status in ('accepted', 'preparing', 'ready'):
            task = get('target', '/api/preparation-routing/tasks/' + oid)['preparation_task']
            updated = post('target', '/api/preparation-routing/tasks/' + oid + '/prepare',
                           {'version': task['version'], 'status': status})['preparation_task']
            assert updated['preparation_status'] == status
        out['preparation_status'] = 'ready'
    elif spec['scenario'] == 'accepted':
        tid = spec['task_id']
        tasks = get('driver', '/api/tasks')['tasks']
        assigned = next(row for row in tasks if row['id'] == tid)
        assert assigned['status'] == 'accepted'
        assert assigned['branch_id'] == original and assigned['preparation_branch']['id'] == target
        # Read-only route computation is local: the synthetic public token only
        # enables the normal route-context validation; no provider call occurs.
        os.environ['MAPBOX_ENABLED'] = 'true'
        os.environ['MAPBOX_PUBLIC_TOKEN_OPS_BRAND'] = 'pk.' + 'synthetic' * 5
        from app import dispatch_map
        with db.tx() as conn:
            manager = conn.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
            conn.execute("INSERT INTO driver_latest_locations(driver_id,brand_id,shift_id,lat,lng,accuracy_m) "
                         "VALUES('ops-driver','ops-brand',%s,28.4,45.9,5) ON CONFLICT(driver_id) DO UPDATE "
                         "SET shift_id=EXCLUDED.shift_id,lat=EXCLUDED.lat,lng=EXCLUDED.lng,accuracy_m=5,updated_at=now()",
                         (spec['shift_id'],))
            plan = dispatch_map.route_context(conn, manager, 'ops-driver')
            pickup = next(stop for stop in plan['stops'] if stop['task_id'] == tid and stop['kind'] == 'pickup')
            assert pickup['branch_id'] == target
            assert pickup['location'] == {'lat': 28.405, 'lng': 45.905}
        image = io.BytesIO()
        Image.new('RGB', (12, 12), 'white').save(image, 'PNG')
        photo = post('driver', '/api/tasks/' + tid + '/photos',
                     {'stage': 'picked_up', 'content_type': 'image/png',
                      'data_base64': base64.b64encode(image.getvalue()).decode()})
        post('driver', '/api/tasks/' + tid + '/transition', {'status': 'picked_up', 'photo_id': photo['id']})
        post('driver', '/api/driver-work-hours/depart',
             {'shift_id': spec['shift_id'], 'order_ids': [oid], 'branch_id': original}, status=403)
        trip = post('driver', '/api/driver-work-hours/depart',
                    {'shift_id': spec['shift_id'], 'order_ids': [oid], 'branch_id': target})['trip']
        assert trip['departure_branch_id'] == target
        with db.tx() as conn:
            accepted = conn.execute('SELECT accepted_at FROM driver_tasks WHERE id=%s', (tid,)).fetchone()['accepted_at']
        assert accepted.isoformat() == spec['accepted_at']
        out.update(driver_pickup=target, route_pickup=pickup, trip_departure=trip['departure_branch_id'],
                   accepted_at=accepted.isoformat(), accepted_at_preserved=True)
    else:
        raise AssertionError('Unknown rollback preparation scenario')
    app.dependency_overrides.clear()

with db.tx() as conn:
    row = conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
    route = conn.execute('SELECT * FROM order_preparation_routes WHERE id=%s', (row['preparation_route_id'],)).fetchone()
    assert route['target_branch_id'] == target and row['branch_id'] == original
    jobs = conn.execute('SELECT * FROM branch_print_jobs WHERE order_id=%s ORDER BY kind', (oid,)).fetchall()
    assert len(jobs) == 2 and {job['branch_id'] for job in jobs} == {target}
    assert all(job['payload']['preparation_branch_id'] == target for job in jobs)
    _, manifest = exports.build_archive(conn, full=True)
    out['exported_preparation_routes'] = manifest['counts']['order_preparation_routes']
    out['preservation_tables'] = list(db.preservation_snapshot(conn))
    assert 'order_preparation_routes' in out['preservation_tables']
    out['route_id'] = route['id']
print(json.dumps(out, default=str))
db.close_pools()
