"""Driver location security and persistence; no external GPS/Mapbox requests."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import auth, db, dispatch_map


@pytest.fixture
def gps_client():
    app = FastAPI()
    app.include_router(dispatch_map.router)
    principal = {'id': 'gps-driver', 'role': 'driver', 'brand_id': 'kps', 'branch_ids': ['gps-branch']}
    app.dependency_overrides[auth.current_user] = lambda: principal
    return TestClient(app), principal


@pytest.mark.parametrize('body', [
    {'lat':91,'lng':40,'accuracy_m':5}, {'lat':20,'lng':181,'accuracy_m':5},
    {'lat':20,'lng':40,'accuracy_m':-1}, {'lat':20,'lng':40,'accuracy_m':10001},
    {'lat':'20','lng':40,'accuracy_m':5}, {'lat':True,'lng':40,'accuracy_m':5},
    {'lat':20,'lng':40,'accuracy_m':5,'driver_id':'other'},
    {'lat':20,'lng':40,'accuracy_m':5,'updated_at':'2099-01-01'},
])
def test_location_rejects_invalid_or_forged_fields(gps_client, body):
    client, _ = gps_client
    assert client.post('/api/operations/driver-location', json=body).status_code == 422


@pytest.mark.parametrize('role', ['customer','branch_manager','admin','delivery_manager'])
def test_non_driver_cannot_publish_or_delete(gps_client, role):
    client, user = gps_client
    user['role'] = role
    assert client.post('/api/operations/driver-location', json={'lat':20,'lng':40,'accuracy_m':5}).status_code == 403
    assert client.delete('/api/operations/driver-location').status_code == 403


def test_location_read_requires_permission(gps_client):
    client, user = gps_client
    assert client.get('/api/operations/driver-locations').status_code == 403
    user.update(role='delivery_manager', permission_denials=['drivers_view'])
    assert client.get('/api/operations/driver-locations').status_code == 403


@pytest.fixture
def gps_data(database, gps_client):
    client, user = gps_client
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('gps-driver','GPS Driver','driver','kps',%s)", (db.json(['gps-branch']),))
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES('gps-shift','driver','gps-driver','kps',%s)", (db.json(['gps-branch']),))
    return client, user


@pytest.mark.postgres
def test_latest_location_scope_throttle_and_close(gps_data):
    client, user = gps_data
    endpoint = '/api/operations/driver-location'
    first = client.post(endpoint, json={'lat':28.4,'lng':45.9,'accuracy_m':10})
    assert first.status_code == 200, first.text
    assert first.json()['updated_at'] and first.json()['shift_id'] == 'gps-shift'
    second = client.post(endpoint, json={'lat':29.4,'lng':46.9,'accuracy_m':11})
    assert second.status_code == 200 and second.json()['throttled']
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_latest_locations').fetchone()['n'] == 1
    user.update(role='delivery_manager')
    visible = client.get('/api/operations/driver-locations').json()['drivers']
    assert len(visible) == 1 and visible[0]['location'] == {'lat':28.4,'lng':45.9}
    assert visible[0]['stale'] is False
    user['branch_ids'] = ['not-allowed']
    assert client.get('/api/operations/driver-locations').json()['drivers'] == []
    user.update(role='owner', brand_id='enbishan')
    assert client.get('/api/operations/driver-locations').json()['drivers'] == []
    user.update(role='delivery_manager', brand_id='kps', branch_ids=['gps-branch'])
    with db.tx() as c:
        c.execute("UPDATE driver_latest_locations SET updated_at=now()-interval '3 minutes'")
    assert client.get('/api/operations/driver-locations').json()['drivers'][0]['stale'] is True
    with db.tx() as c:
        c.execute("UPDATE shifts SET status='closed' WHERE id='gps-shift'")
    assert client.get('/api/operations/driver-locations').json()['drivers'] == []
    user['role'] = 'driver'
    assert client.post(endpoint, json={'lat':28.4,'lng':45.9,'accuracy_m':10}).status_code == 409
    assert client.delete(endpoint).status_code == 200
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_latest_locations').fetchone()['n'] == 0


@pytest.mark.postgres
def test_location_cannot_publish_after_branch_revocation(gps_data):
    client, user = gps_data
    user['branch_ids'] = []
    assert client.post('/api/operations/driver-location', json={'lat':28.4,'lng':45.9,'accuracy_m':10}).status_code == 403
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_latest_locations').fetchone()['n'] == 0


def test_current_and_rollback_export_register_scoped_gps_table():
    import hashlib
    import importlib.util
    import json
    from pathlib import Path
    from app import exports
    root = Path(__file__).resolve().parents[1]
    path = root/'tools/rollback_application_overlay/app/exports.py'
    spec = importlib.util.spec_from_file_location('app._gps_rollback_exports', path)
    overlay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(overlay)
    for module in (exports, overlay):
        assert module.TABLES.count('driver_latest_locations') == 1
        assert module.TABLES.index('users') < module.TABLES.index('shifts') < module.TABLES.index('driver_latest_locations')
        assert module._scoped_sql('driver_latest_locations', 'kps') == ('brand_id=%s', ('kps',))
        assert module._scoped_sql('driver_latest_locations', None) == ('TRUE', ())
    manifest = json.loads((root/'tools/rollback_application_overlay/manifest.json').read_text())
    assert manifest['files']['app/exports.py'] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.postgres
def test_gps_point_preserved_in_full_export_and_brand_scoped(gps_data):
    import json
    from app import exports
    client, user = gps_data
    response = client.post('/api/operations/driver-location', json={'lat':28.4,'lng':45.9,'accuracy_m':10})
    assert response.status_code == 200
    with db.tx() as c:
        full, manifest = exports.build_archive(c, full=True)
        scoped, scoped_manifest = exports.build_archive(c, brand_id='kps')
        other, other_manifest = exports.build_archive(c, brand_id='different-brand')
    assert manifest['counts']['driver_latest_locations'] == 1
    assert scoped_manifest['counts']['driver_latest_locations'] == 1
    assert other_manifest['counts']['driver_latest_locations'] == 0
    for content in (full, scoped):
        archive, saved = exports.verify_archive(content)
        rows = json.loads(archive.read('data/driver_latest_locations.json'))
        assert rows[0]['driver_id'] == 'gps-driver'
        assert rows[0]['shift_id'] == 'gps-shift'
        assert rows[0]['lat'] == 28.4
