"""Synthetic vehicle-maintenance acceptance in isolated PostgreSQL only."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app import auth, db, maintenance
from test_admin import owner

PREFIX = '/api/vehicle-maintenance'
PASSWORD = 'TestOnly!VehicleMaintenance1'
PAYLOAD = {'branch_id': 'kps-branch-1', 'category': 'brakes', 'vehicle_description': 'مركبة اختبار فقط',
           'vehicle_plate': 'TEST 123', 'description': 'صوت عند الفرملة — بيانات اختبار فقط'}


def principal(role='driver', branches=None, permissions=None, brand='kps'):
    identifier = db.new_id('test_vehicle_')
    with db.tx() as conn:
        conn.execute('INSERT INTO users(id,username,display_name,role,brand_id,branch_ids,permissions,password_hash) '
                     'VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                     (identifier, identifier, 'موظف اختبار مركبات', role, brand,
                      db.json(['kps-branch-1'] if branches is None else branches),
                      db.json(permissions or []), auth.hash_password(PASSWORD)))
    return identifier


def login(client, identifier):
    client.cookies.clear()
    client.headers.pop('X-CSRF-Token', None)
    response = client.post('/api/auth/login', json={'username': identifier, 'password': PASSWORD})
    assert response.status_code == 200, response.text
    client.headers['X-CSRF-Token'] = response.json()['csrf_token']


def submit(client, body=None, key=None):
    return client.post(PREFIX + '/reports', json=body or PAYLOAD, headers={'Idempotency-Key': key or uuid4().hex})


def change(client, identifier, status, version, note='ملاحظة اختبار محفوظة', key=None):
    return client.post(PREFIX + '/reports/' + identifier + '/status',
                       json={'status': status, 'version': version, 'note': note},
                       headers={'Idempotency-Key': key or uuid4().hex})


def test_categories_are_stable_bilingual_and_permissions_are_separate():
    assert len(maintenance.CATEGORIES) == len(maintenance.CATEGORY_CODES) == 18
    assert maintenance.CATEGORY_CODES - maintenance.DELIVERY_CATEGORIES == {'engine','transmission','brakes','tyres','battery','electrical','cooling','air_conditioning','oil_leak','accident','scheduled_service','other'}
    assert maintenance.DELIVERY_CATEGORIES == {'order_problem','delay','customer_problem','payment_problem','branch_problem','general'}
    for code, ar, en in maintenance.CATEGORIES:
        assert any('\u0600' <= ch <= '\u06ff' for ch in ar)
        assert en.isascii() and en
        assert maintenance.ReportCreate(**{**PAYLOAD, 'category': code}).category == code
    assert maintenance.PERMISSION in auth.ADMIN_PERMISSIONS
    assert maintenance.PERMISSION not in auth.CONFIGURATION_PERMISSIONS


@pytest.mark.parametrize('values', [
    {'category': 'owner'}, {'description': '   '}, {'vehicle_description': ' '},
    {'description': 'a' * 2001}, {'vehicle_plate': 'a' * 41}, {'description': 'x\x00y'},
    {'driver_id': 'someone-else'}, {'brand_id': 'someone-else'}, {'status': 'resolved'},
])
def test_report_input_fails_closed(values):
    with pytest.raises(ValidationError):
        maintenance.ReportCreate(**{**PAYLOAD, **values})


@pytest.mark.postgres
def test_driver_submits_without_order_or_shift_only_sees_own_and_replay_is_safe(client):
    driver = principal()
    login(client, driver)
    options = client.get(PREFIX + '/options').json()
    assert options['can_create'] is True and options['can_manage'] is False
    assert len(options['categories']) == 18
    key = uuid4().hex
    created = submit(client, key=key)
    assert created.status_code == 200, created.text
    row = created.json()['report']
    assert row['driver_id'] == driver and row['brand_id'] == 'kps' and row['status'] == 'open'
    assert submit(client, key=key).json() == created.json()
    assert submit(client, {**PAYLOAD, 'category': 'engine'}, key=key).status_code == 409
    assert submit(client, {**PAYLOAD, 'branch_id': 'kps-branch-9'}).status_code == 404
    assert submit(client, {**PAYLOAD, 'driver_id': driver}).status_code == 422
    assert change(client, row['id'], 'resolved', 1).status_code == 403
    listed = client.get(PREFIX + '/reports').json()
    assert listed['total'] == 1
    assert not ({'phone', 'email', 'customer', 'password_hash', 'total_halalas'} & set(listed['reports'][0]))
    with db.tx() as conn:
        for table in ('orders', 'driver_tasks', 'shifts', 'custody_events'):
            assert conn.execute('SELECT count(*) AS n FROM ' + table).fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) AS n FROM vehicle_report_events').fetchone()['n'] == 1
    login(client, principal())
    assert client.get(PREFIX + '/reports').json()['total'] == 0
    assert client.get(PREFIX + '/reports/' + row['id']).status_code == 404


@pytest.mark.postgres
def test_maintenance_admin_can_manage_only_reports_and_keeps_history(client):
    login(client, principal())
    row = submit(client).json()['report']
    admin = principal('admin', permissions=[maintenance.PERMISSION])
    login(client, admin)
    assert client.get(PREFIX + '/reports').json()['total'] == 1
    assert client.get(PREFIX + '/options').json()['can_manage'] is True
    for path in ('/api/admin/config', '/api/identity/staff', '/api/tasks', '/api/orders',
                 '/api/shifts', '/api/admin/export', '/api/identity/settings', '/api/reports'):
        assert client.get(path).status_code == 403, path
    assert submit(client).status_code == 403
    assert client.patch('/api/admin/settings', json={'theme': {'profile': 'CHARCOAL'}}).status_code == 403
    assert change(client, row['id'], 'in_progress', 1, note=' ').status_code == 422
    key = uuid4().hex
    started = change(client, row['id'], 'in_progress', 1, key=key)
    assert started.status_code == 200, started.text
    assert change(client, row['id'], 'in_progress', 1, key=key).json() == started.json()
    assert change(client, row['id'], 'resolved', 1).status_code == 409
    resolved = change(client, row['id'], 'resolved', 2, note='تم الإصلاح في اختبار فقط')
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()['report']['resolved_at'] and resolved.json()['report']['resolution_note']
    reopened = change(client, row['id'], 'open', 3, note='عاد العطل في الاختبار')
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()['report']['resolved_at'] is None
    assert change(client, row['id'], 'open', 4).status_code == 409
    detail = client.get(PREFIX + '/reports/' + row['id']).json()
    assert [x['event_type'] for x in detail['events']] == ['created', 'in_progress', 'resolved', 'reopened']
    assert detail['report']['version'] == 4
    with db.tx() as conn:
        before = db.preservation_snapshot(conn)
    db.migrate()
    with db.tx() as conn:
        after = db.preservation_snapshot(conn, before)
        assert before == after
        assert conn.execute("SELECT count(*) AS n FROM audit_events WHERE entity_id=%s AND action LIKE 'vehicle_report.%%'", (row['id'],)).fetchone()['n'] == 4


@pytest.mark.postgres
def test_scope_brand_permissions_and_replays_rechecked_on_every_request(client):
    driver = principal(branches=['kps-branch-1', 'kps-branch-9'])
    login(client, driver)
    key = uuid4().hex
    row = submit(client, key=key).json()['report']
    outside = submit(client, {**PAYLOAD, 'branch_id': 'kps-branch-9'}).json()['report']
    with db.tx() as conn:
        conn.execute('UPDATE users SET branch_ids=%s WHERE id=%s', (db.json(['kps-branch-9']), driver))
    assert submit(client, key=key).status_code == 404
    assert client.get(PREFIX + '/reports/' + row['id']).status_code == 404
    assert client.get(PREFIX + '/reports').json()['total'] == 1
    admin = principal('admin', permissions=[maintenance.PERMISSION])
    login(client, admin)
    assert client.get(PREFIX + '/reports/' + outside['id']).status_code == 404
    assert change(client, outside['id'], 'resolved', 1).status_code == 404
    key = uuid4().hex
    assert change(client, row['id'], 'in_progress', 1, key=key).status_code == 200
    with db.tx() as conn:
        conn.execute('UPDATE users SET branch_ids=%s WHERE id=%s', (db.json([]), admin))
    assert change(client, row['id'], 'in_progress', 1, key=key).status_code == 404
    with db.tx() as conn:
        conn.execute('UPDATE users SET permissions=%s WHERE id=%s', (db.json([]), admin))
    assert client.get(PREFIX + '/reports').status_code == 403
    assert change(client, row['id'], 'in_progress', 1, key=key).status_code == 403
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('vehicle-test-other-brand','Other synthetic brand')")
    login(client, principal('owner', brand='vehicle-test-other-brand'))
    assert client.get(PREFIX + '/reports').json()['total'] == 0
    assert client.get(PREFIX + '/reports/' + row['id']).status_code == 404
    assert change(client, row['id'], 'resolved', 2).status_code == 404


@pytest.mark.postgres
@pytest.mark.parametrize('role', ['delivery_staff', 'branch_manager', 'admin'])
def test_other_staff_have_no_implicit_maintenance_access(client, role):
    login(client, principal(role, permissions=['branches_manage'] if role == 'admin' else []))
    for path in (PREFIX + '/options', PREFIX + '/reports', PREFIX + '/reports/unknown'):
        assert client.get(path).status_code == 403
    assert submit(client).status_code == 403
    assert change(client, 'unknown', 'resolved', 1).status_code == 403


@pytest.mark.postgres
def test_maintenance_invitation_all_branches_stays_inactive_and_owner_can_revoke(owner):
    client, mutate = owner
    assert client.post('/api/identity/step-up', json={'password': 'TestOnly!PrivatePassword1'}).status_code == 200
    branches = [b['id'] for b in client.get('/api/admin/config').json()['branches']]
    invited = client.post('/api/identity/staff/invitations', json={
        'display_name': 'أدمن صيانة اختبار', 'email': 'maintenance@fixture.invalid', 'phone': '0500000072',
        'role': 'admin', 'branch_ids': branches, 'permissions': [maintenance.PERMISSION]})
    assert invited.status_code == 200, invited.text
    user = invited.json()['user']
    assert user['permissions'] == [maintenance.PERMISSION] and len(user['branch_ids']) == 20
    assert not user['active'] and not user['email_verified_at']
    blocked = mutate('PATCH', '/api/identity/staff/' + user['id'], {'active': True})
    assert blocked.status_code == 403
    assert blocked.json()['detail']['code'] == 'STAFF_EMAIL_VERIFICATION_REQUIRED'
    changed = mutate('PATCH', '/api/identity/staff/' + user['id'], {'permissions': []})
    assert changed.status_code == 200 and changed.json()['user']['permissions'] == []


@pytest.mark.postgres
def test_owner_can_filter_and_export_maintenance_without_cross_brand_leak(owner):
    from app.exports import build_archive, verify_archive
    import json

    client, _ = owner
    cookies, csrf = dict(client.cookies), client.headers['X-CSRF-Token']
    login(client, principal(branches=['kps-branch-1', 'kps-branch-9']))
    first = submit(client).json()['report']
    submit(client, {**PAYLOAD, 'branch_id': 'kps-branch-9'})
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('vehicle-export-other','Other test brand')")
        conn.execute("INSERT INTO branches(id,brand_id,branch_no,source_id,name,city) "
                     "VALUES('vehicle-export-branch','vehicle-export-other',1,'test-source','Other branch','Test city')")
    login(client, principal(brand='vehicle-export-other', branches=['vehicle-export-branch']))
    assert submit(client, {**PAYLOAD, 'branch_id': 'vehicle-export-branch'}).status_code == 200
    client.cookies.clear()
    client.cookies.update(cookies)
    client.headers['X-CSRF-Token'] = csrf
    assert client.get(PREFIX + '/reports?limit=1').json()['total'] == 2
    assert len(client.get(PREFIX + '/reports?limit=1&offset=1').json()['reports']) == 1
    assert client.get(PREFIX + '/reports?branch_id=kps-branch-1').json()['total'] == 1
    assert change(client, first['id'], 'resolved', 1).status_code == 200
    assert client.get(PREFIX + '/reports?status=resolved').json()['total'] == 1
    assert client.get(PREFIX + '/reports?status=bogus').status_code == 422
    assert client.get(PREFIX + '/reports?limit=500').status_code == 422
    with db.tx() as conn:
        contents, _ = build_archive(conn, brand_id='kps')
    archive, manifest = verify_archive(contents)
    assert {'vehicle_reports', 'vehicle_report_events'} <= set(manifest['table_order'])
    assert len(json.loads(archive.read('data/vehicle_reports.json'))) == 2
    assert len(json.loads(archive.read('data/vehicle_report_events.json'))) == 3


@pytest.mark.postgres
def test_synthetic_six_digit_invitation_activates_only_maintenance_permission(owner, monkeypatch):
    # Fixture-only encrypted mailbox; never dispatches or asserts real inbox delivery.
    from test_v5_identity import _mail_setup
    from test_final_identity import _code

    client, _ = owner
    _mail_setup(monkeypatch)
    assert client.post('/api/identity/step-up', json={'password': 'TestOnly!PrivatePassword1'}).status_code == 200
    created = client.post('/api/identity/staff/invitations', json={
        'display_name': 'أدمن صيانة اختبار رمز', 'email': 'maintenance-code@fixture.invalid', 'phone': '0500000073',
        'role': 'admin', 'branch_ids': ['kps-branch-1'], 'permissions': [maintenance.PERMISSION]})
    assert created.status_code == 200, created.text
    invited = created.json()
    assert not invited['user']['active'] and invited['invitation_status'] == 'pending_email'
    code = _code(invited['challenge_id'])
    assert len(code) == 6 and code.isdigit()
    client.cookies.clear()
    client.headers.pop('X-CSRF-Token', None)
    verified = client.post('/api/identity/staff/code/verify', json={'challenge_id': invited['challenge_id'], 'code': code})
    assert verified.status_code == 200, verified.text
    assert verified.json()['user']['permissions'] == [maintenance.PERMISSION]
    assert verified.json()['user']['active'] and verified.json()['user']['email_verified_at']
    client.headers['X-CSRF-Token'] = verified.json()['csrf_token']
    assert client.get(PREFIX + '/reports').status_code == 200
    assert client.get('/api/admin/config').status_code == 403
    assert client.get('/api/identity/staff').status_code == 403
    assert client.get('/api/orders').status_code == 403


@pytest.mark.postgres
def test_delivery_manager_only_sees_delivery_reports_not_vehicle_maintenance(client):
    login(client,principal())
    vehicle=submit(client).json()['report']
    operational=submit(client,{**PAYLOAD,'category':'delay'}).json()['report']
    login(client,principal('delivery_manager'))
    assert {r['id'] for r in client.get(PREFIX+'/reports').json()['reports']}=={operational['id']}
    assert client.get(PREFIX+'/reports/'+vehicle['id']).status_code==404
    assert change(client,vehicle['id'],'resolved',1).status_code==404
    assert submit(client).status_code==403
    assert {r['code'] for r in client.get(PREFIX+'/options').json()['categories']}==maintenance.DELIVERY_CATEGORIES
    assert change(client,operational['id'],'in_progress',1).status_code==200
