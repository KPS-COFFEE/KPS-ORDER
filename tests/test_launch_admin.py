"""Launch controls through real authorized sessions in isolated PostgreSQL only.

Locations, banners, identities and mail addresses here are synthetic fixtures;
none is evidence that a real branch or an external delivery channel is ready.
"""
import io
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image

from tests.sms_policy_helpers import sms_login, sms_step_up
from app import auth, db
from test_admin import owner


PASSWORD = 'TestOnly!LaunchAdministrator1'


def _administrator(permissions, branches=None, role='admin'):
    identifier = 'launch-test-' + uuid4().hex
    with db.tx() as conn:
        conn.execute(
            'INSERT INTO users(id,username,display_name,role,brand_id,branch_ids,password_hash,permissions) '
            'VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
            (identifier, identifier, 'اختبار صلاحيات الإطلاق', role, 'kps',
             db.json(branches or ['kps-branch-1']), auth.hash_password(PASSWORD), db.json(permissions)))
    return identifier


def _login(client, identifier, monkeypatch):
    client.cookies.clear()
    client.headers.pop('X-CSRF-Token', None)
    with db.tx() as conn:
        user = conn.execute('SELECT * FROM users WHERE id=%s',(identifier,)).fetchone()
    result = sms_login(client, user, monkeypatch)
    return result['user']


def _restore_owner(client, cookies, csrf):
    client.cookies.clear()
    client.cookies.update(cookies)
    client.headers['X-CSRF-Token'] = csrf


def test_administrator_permissions_are_explicit_and_cannot_name_owner_capabilities():
    assert auth.validate_admin_permissions(['branches_manage', 'banners_manage']) == ['branches_manage', 'banners_manage']
    for value in ('branches_manage', None, [True], ['owner'], ['users_manage'], ['banners_manage'] * 2):
        with pytest.raises(HTTPException) as caught:
            auth.validate_admin_permissions(value)
        assert caught.value.detail['code'] == 'ADMIN_PERMISSIONS_INVALID'
    assert auth.has_permission({'role': 'owner'}, 'appearance_manage')
    assert not auth.has_permission({'role': 'admin', 'permissions': []}, 'appearance_manage')
    assert not auth.has_permission({'role': 'driver', 'permissions': ['appearance_manage']}, 'appearance_manage')


@pytest.mark.postgres
def test_global_services_require_explicit_owner_delegation(owner, monkeypatch):
    client, mutate = owner
    body = {'services': {'delivery': True, 'pickup': False, 'car_pickup': False}, 'reason': 'Synthetic service policy test'}
    for permissions, role in [(['branches_manage'], 'admin'), (['brand_services_manage'], 'driver')]:
        _login(client, _administrator(permissions, role=role), monkeypatch)
        assert mutate('PATCH', '/api/admin/brand-services', body).status_code == 403
    _login(client, _administrator(['brand_services_manage']), monkeypatch)
    result = mutate('PATCH', '/api/admin/brand-services', body)
    assert result.status_code == 200, result.text
    config = client.get('/api/admin/config').json()
    assert config['brand']['settings']['services']['pickup'] is False
    assert config['users'] == []


@pytest.mark.postgres
def test_delegated_branch_administrator_is_scoped_and_cannot_gain_owner_access(owner, monkeypatch):
    client, mutate = owner
    identifier = _administrator(['branches_manage'])
    user = _login(client, identifier, monkeypatch)
    assert user['role'] == 'admin' and user['permissions'] == ['branches_manage']
    configuration = client.get('/api/admin/config')
    assert configuration.status_code == 200, configuration.text
    config = configuration.json()
    assert [branch['id'] for branch in config['branches']] == ['kps-branch-1']
    assert config['users'] == [] and config['products'] == [] and config['banners'] == []
    assert config['settings'] == {} and set(config['brand']) == {'id', 'name'}
    allowed = mutate('PATCH', '/api/admin/branches/kps-branch-1', {'settings': {'active': False}})
    assert allowed.status_code == 200, allowed.text
    assert mutate('PATCH', '/api/admin/branches/kps-branch-9', {'settings': {'active': False}}).status_code == 404
    for method, path, body in [
        ('PATCH', '/api/admin/products/kps-01', {'price_halalas': 1}),
        ('PATCH', '/api/admin/settings', {'theme': {'profile': 'CHARCOAL'}}),
        ('POST', '/api/admin/banners', {'title': 'غير مخول'}),
        ('PATCH', '/api/admin/users/admin-test-owner', {'active': False}),
        ('POST', '/api/admin/branches', {'branch_no': 21, 'name': 'غير مخول', 'city': 'الخفجي'}),
        ('POST', '/api/identity/staff/invitations', {'display_name': 'مالك غير مخول', 'email': 'owner@fixture.invalid',
                                                  'phone': '0500000091', 'role': 'owner'}),
    ]:
        denied = mutate(method, path, body)
        assert denied.status_code == 403, (path, denied.text)
    assert client.get('/api/identity/staff').status_code == 403


@pytest.mark.postgres
def test_appearance_administrator_cannot_change_operating_settings_or_account_permissions(owner, monkeypatch):
    client, mutate = owner
    identifier = _administrator(['appearance_manage'])
    _login(client, identifier, monkeypatch)
    allowed = mutate('PATCH', '/api/admin/settings', {'theme': {'profile': 'CHARCOAL'}})
    assert allowed.status_code == 200, allowed.text
    restored = mutate('PATCH', '/api/admin/settings', {'theme': {'profile': 'ORIGINAL_Light'}})
    assert restored.status_code == 200 and restored.json()['settings']['theme']['profile'] == 'ORIGINAL_Light'
    assert mutate('PATCH', '/api/admin/settings', {'operational_day_start': '08:00'}).status_code == 403
    assert mutate('PATCH', '/api/admin/users/' + identifier, {'permissions': ['branches_manage']}).status_code == 403
    config = client.get('/api/admin/config').json()
    assert set(config['settings']) <= {'theme', 'homepage_presentation'}
    assert config['users'] == [] and config['products'] == [] and config['banners'] == []


@pytest.mark.postgres
@pytest.mark.parametrize('endpoint', ['/api/admin/users', '/api/identity/staff'])
def test_owner_permission_change_requires_step_up_revokes_sessions_and_audits(owner, endpoint, monkeypatch):
    client, mutate = owner
    owner_cookies, owner_csrf = dict(client.cookies), client.headers['X-CSRF-Token']
    identifier = _administrator(['branches_manage'])
    _login(client, identifier, monkeypatch)
    old_admin_cookies = dict(client.cookies)
    _restore_owner(client, owner_cookies, owner_csrf)
    denied = mutate('PATCH', endpoint + '/' + identifier, {'permissions': ['banners_manage']})
    assert denied.status_code == 403 and denied.json()['detail']['code'] == 'OWNER_STEP_UP_REQUIRED'
    with db.tx() as conn:
        owner_user = conn.execute("SELECT * FROM users WHERE id='admin-test-owner'").fetchone()
    sms_step_up(client, owner_user, monkeypatch)
    invalid = mutate('PATCH', endpoint + '/' + identifier, {'permissions': ['owner']})
    assert invalid.status_code == 422
    changed = mutate('PATCH', endpoint + '/' + identifier, {'permissions': ['banners_manage']})
    assert changed.status_code == 200, changed.text
    result = changed.json() if endpoint == '/api/admin/users' else changed.json()['user']
    assert result['permissions'] == ['banners_manage'] and result['role'] == 'admin'
    assert mutate('PATCH', '/api/admin/users/' + identifier, {'role': 'owner'}).status_code == 403
    with db.tx() as conn:
        assert not conn.execute('SELECT 1 FROM sessions WHERE user_id=%s AND revoked_at IS NULL', (identifier,)).fetchone()
        action = 'user.updated' if endpoint == '/api/admin/users' else 'staff.updated'
        evidence = conn.execute('SELECT details FROM audit_events WHERE action=%s AND entity_id=%s ORDER BY created_at DESC LIMIT 1', (action, identifier)).fetchone()
        assert evidence['details']['permissions'] == ['banners_manage']
    client.cookies.clear()
    client.cookies.update(old_admin_cookies)
    assert client.get('/api/session').json()['user'] is None
    assert client.get('/api/admin/config').status_code == 401
    _login(client, identifier, monkeypatch)
    assert client.get('/api/admin/config').status_code == 200
    assert mutate('PATCH', '/api/admin/branches/kps-branch-1', {'settings': {'active': False}}).status_code == 403


@pytest.mark.postgres
def test_owner_and_admin_invitations_remain_inactive_until_secure_email_verification(owner, monkeypatch):
    client, mutate = owner
    from tests.legacy_identity_helpers import make_legacy_user, BRAND, BRANCHES
    from tests.test_core import _authenticate
    legacy_owner = make_legacy_user('owner', username='legacy-review-owner', password='TestOnly!PrivatePassword1')
    _authenticate(client, legacy_owner)
    payload = {'display_name': 'أدمن دعوة اختبار', 'email': 'admin@fixture.invalid', 'phone': '0500000081',
               'role': 'admin', 'branch_ids': [BRANCHES[0]], 'permissions': ['banners_manage']}
    denied = client.post('/api/identity/staff/invitations', json=payload)
    assert denied.status_code == 403 and denied.json()['detail']['code'] == 'OWNER_STEP_UP_REQUIRED'
    assert client.post('/api/identity/step-up', json={'password':'TestOnly!PrivatePassword1'}).status_code == 200
    invited = client.post('/api/identity/staff/invitations', json=payload)
    assert invited.status_code == 200, invited.text
    row = invited.json()['user']
    assert row['role'] == 'admin' and row['permissions'] == ['banners_manage']
    assert row['active'] is False and invited.json()['invitation_status'] == 'draft'
    blocked = mutate('PATCH', '/api/admin/users/' + row['id'], {'active': True})
    assert blocked.status_code == 403 and blocked.json()['detail']['code'] == 'STAFF_EMAIL_VERIFICATION_REQUIRED'
    other = client.post('/api/identity/staff/invitations', json={**payload, 'display_name': 'مالك دعوة اختبار',
                           'email': 'second-owner@fixture.invalid', 'phone': '0500000082', 'role': 'owner', 'permissions': []})
    assert other.status_code == 200, other.text
    assert other.json()['user']['role'] == 'owner' and other.json()['user']['active'] is False
    assert mutate('PATCH', '/api/admin/users/'+legacy_owner['id'], {'active': False}).status_code == 409
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) AS n FROM users WHERE brand_id='legacy-security-fixture' AND role='owner' AND active").fetchone()['n'] == 1


@pytest.mark.postgres
def test_verified_admin_invitation_retains_only_owner_selected_permissions(owner, monkeypatch):
    from test_v5_identity import _legacy_mail_setup as _mail_setup, _secret
    client, mutate = owner
    from tests.legacy_identity_helpers import make_legacy_user, BRAND, BRANCHES
    from tests.test_core import _authenticate
    legacy_owner = make_legacy_user('owner', username='legacy-review-owner', password='TestOnly!PrivatePassword1')
    _authenticate(client, legacy_owner)
    _mail_setup(monkeypatch)
    assert client.post('/api/identity/step-up', json={'password':'TestOnly!PrivatePassword1'}).status_code == 200
    invitation = client.post('/api/identity/staff/invitations', json={
        'display_name': 'أدمن بريد اختبار', 'email': 'verified-admin@fixture.invalid', 'phone': '0500000071',
        'role': 'admin', 'branch_ids': [BRANCHES[0]], 'permissions': ['banners_manage']})
    assert invitation.status_code == 200, invitation.text
    pending = invitation.json()
    assert pending['invitation_status'] == 'pending_email' and pending['user']['active'] is False
    proof = {'challenge_id': pending['challenge_id'], 'token': _secret(pending['challenge_id'])}
    client.cookies.clear()
    client.headers.pop('X-CSRF-Token', None)
    verified = client.post('/api/identity/email/verify', json=proof)
    assert verified.status_code == 200, verified.text
    user = verified.json()['user']
    assert user['id'] == pending['user']['id'] and user['role'] == 'admin'
    assert user['permissions'] == ['banners_manage'] and user['branch_ids'] == [BRANCHES[0]]
    assert user['active'] is True and user['email_verified_at']
    client.headers['X-CSRF-Token'] = verified.json()['csrf_token']
    assert client.get('/api/admin/config').status_code == 200
    assert client.get('/api/identity/staff').status_code == 403
    assert mutate('PATCH', '/api/admin/users/'+legacy_owner['id'], {'active': False}).status_code == 403
    assert mutate('PATCH', '/api/admin/settings', {'theme': {'profile': 'CHARCOAL'}}).status_code == 403
    banner = mutate('POST', '/api/admin/banners', {'title': 'إعلان مخول بعد التحقق'})
    assert banner.status_code == 200 and banner.json()['branch_ids'] == [BRANCHES[0]]
    assert client.post('/api/identity/email/verify', json=proof).status_code == 400


@pytest.mark.postgres
def test_city_scoped_banner_asset_schedule_speed_and_owner_edits_persist_without_data_changes(owner, monkeypatch):
    client, mutate = owner
    from tests.test_homepage_presentation import _approve_advertising
    _approve_advertising(client, mutate, monkeypatch)
    image = io.BytesIO()
    Image.new('RGB', (60, 24), 'orange').save(image, format='PNG')
    uploaded = mutate('POST', '/api/admin/assets', files={'file': ('synthetic-banner.png', image.getvalue(), 'image/png')})
    assert uploaded.status_code == 200, uploaded.text
    asset_id = uploaded.json()['id']
    before = client.get('/api/admin/config').json()
    with db.tx() as conn:
        fingerprint = db.preservation_snapshot(conn)
    payload = {'title': 'افتتاح تجريبي واضح', 'active': True, 'cities': ['حفرالباطن'],
               'rotation_ms': 4000, 'sort_order': 3, 'desktop_asset_id': asset_id,
               'destination': {'kind': 'link', 'id': 'https://www.kps.sa/branches?city=khafji'}}
    created = mutate('POST', '/api/admin/banners', payload)
    assert created.status_code == 200, created.text
    banner_id = created.json()['id']
    assert created.json()['cities'] == ['حفر الباطن']
    assert client.get('/api/banners').json()['banners'] == []
    assert client.get('/api/banners?city=الدمام').json()['banners'] == []
    visible = client.get('/api/banners?city=حفرالباطن').json()['banners']
    assert len(visible) == 1 and visible[0]['rotation_ms'] == 4000
    image_url = visible[0]['desktop_image_url']
    assert client.get(image_url).status_code == 200
    assert client.get('/api/banner-assets/' + asset_id).status_code == 404
    assert client.get('/api/banner-assets/' + asset_id + '?city=الدمام').status_code == 404
    assert client.get('/api/banners?branch_id=kps-branch-1&city=الدمام').json()['banners'][0]['id'] == banner_id
    updated = mutate('PATCH', '/api/admin/banners/' + banner_id, {'title': 'افتتاح جديد تجريبي', 'rotation_ms': 90000, 'sort_order': 1})
    assert updated.status_code == 200, updated.text
    db.migrate()
    visible_after = client.get('/api/banners?branch_id=kps-branch-1').json()['banners']
    assert visible_after[0]['title'] == 'افتتاح جديد تجريبي' and visible_after[0]['rotation_ms'] == 90000
    after = client.get('/api/admin/config').json()
    assert before['products'] == after['products'] and before['branches'] == after['branches']
    with db.tx() as conn:
        assert db.preservation_snapshot(conn) == fingerprint
    assert mutate('PATCH', '/api/admin/banners/' + banner_id, {'starts_at': '2099-01-01T00:00:00+03:00'}).status_code == 200
    assert client.get('/api/banners?city=حفرالباطن').json()['banners'] == []
    assert client.get(image_url).status_code == 404
    assert mutate('PATCH', '/api/admin/banners/' + banner_id, {'starts_at': None, 'active': False}).status_code == 200
    assert client.get('/api/banners?branch_id=kps-branch-1').json()['banners'] == []


@pytest.mark.postgres
def test_banner_rejects_unsafe_links_unregistered_cities_and_invalid_rotation(owner):
    _, mutate = owner
    for body, expected in [
        ({'cities': ['مدينة غير مسجلة']}, 'BANNER_CITIES_INVALID'),
        ({'cities': 'الخفجي'}, 'BANNER_CITIES_INVALID'),
        ({'rotation_ms': 3999}, 'BANNER_SPEED_INVALID'),
        ({'rotation_ms': 90001}, 'BANNER_SPEED_INVALID'),
        ({'rotation_ms': True}, 'BANNER_SPEED_INVALID'),
    ]:
        result = mutate('POST', '/api/admin/banners', {'title': 'إعلان تحقق تجريبي', **body})
        assert result.status_code == 422, result.text
        assert result.json()['detail']['code'] == expected
    for value in ('http://www.kps.sa/', 'javascript:alert(1)', 'https://name:secret@example.com/', 'https://example.com/\nunsafe'):
        result = mutate('POST', '/api/admin/banners', {'title': 'رابط مرفوض', 'destination': {'kind': 'link', 'id': value}})
        assert result.status_code == 422, (value, result.text)
    default = mutate('POST', '/api/admin/banners', {'title': 'سرعة افتراضية تجريبية'})
    assert default.status_code == 200 and default.json()['rotation_ms'] == 6500


@pytest.mark.postgres
def test_banner_administrator_cannot_edit_global_or_other_branch_campaigns(owner, monkeypatch):
    client, mutate = owner
    global_banner = mutate('POST', '/api/admin/banners', {'title': 'إعلان المالك العام'}).json()['id']
    scoped_banner = mutate('POST', '/api/admin/banners', {'title': 'إعلان المالك لفرع آخر', 'branch_ids': ['kps-branch-9']}).json()['id']
    identifier = _administrator(['banners_manage'])
    _login(client, identifier, monkeypatch)
    result = mutate('POST', '/api/admin/banners', {'title': 'إعلان مخول تجريبي', 'branch_ids': [], 'active': True})
    assert result.status_code == 200, result.text
    assert result.json()['branch_ids'] == ['kps-branch-1']
    configuration = client.get('/api/admin/config').json()
    assert {banner['id'] for banner in configuration['banners']} == {result.json()['id']}
    assert all(banner['branch_ids'] and set(banner['branch_ids']) <= {'kps-branch-1'} for banner in configuration['banners'])
    denied = mutate('POST', '/api/admin/banners', {'title': 'إعلان خارج النطاق', 'branch_ids': ['kps-branch-9']})
    assert denied.status_code == 403
    for banner_id in (global_banner, scoped_banner):
        assert mutate('PATCH', '/api/admin/banners/' + banner_id, {'active': True}).status_code == 403
    assert mutate('PATCH', '/api/admin/banners/' + result.json()['id'], {'body': 'تعديل مخول'}).status_code == 200


@pytest.mark.postgres
def test_changed_branch_coordinates_and_map_identifiers_need_fresh_owner_verification(owner):
    client, mutate = owner
    branch_path = '/api/admin/branches/kps-branch-1'
    verified = {'location': {'lat': 28.4, 'lng': 45.9}, 'location_verified': True,
                'map_url': 'https://www.google.com/maps/dir/?api=1&destination=28.4,45.9', 'map_url_verified': True}
    result = mutate('PATCH', branch_path, {'settings': verified})
    assert result.status_code == 200, result.text
    changed = mutate('PATCH', branch_path, {'settings': {'location': {'lat': 28.41, 'lng': 45.91}}})
    assert changed.status_code == 200 and changed.json()['settings']['location_verified'] is False
    changed_map = mutate('PATCH', branch_path, {'settings': {'map_url': 'https://www.google.com/maps/dir/?api=1&destination=28.41,45.91'}})
    assert changed_map.status_code == 200 and changed_map.json()['settings']['map_url_verified'] is False
    public = next(branch for branch in client.get('/api/branches').json()['branches'] if branch['id'] == 'kps-branch-1')
    assert public['directions_url'] is None
    assert mutate('PATCH', branch_path, {'settings': {'map_url_verified': True}}).status_code == 200
    place = mutate('PATCH', branch_path, {'settings': {'place_id': 'ChIJSyntheticPlaceIdOnly0001'}})
    assert place.status_code == 200 and place.json()['settings']['map_url_verified'] is False
    approved = mutate('PATCH', branch_path, {'settings': {'location_verified': True, 'map_url_verified': True}})
    assert approved.status_code == 200, approved.text
    branch = next(branch for branch in client.get('/api/branches').json()['branches'] if branch['id'] == 'kps-branch-1')
    assert 'destination=28.41%2C45.91' in branch['directions_url']
    assert branch['branch_no'] == 1


@pytest.mark.postgres
def test_branch_pickup_car_and_delivery_toggles_are_independent(owner):
    _, mutate = owner
    operating = {'active': True, 'ordering_verified': True, 'location_verified': True,
                 'location': {'lat': 28.4, 'lng': 45.9}, 'payment_methods': ['cash'],
                 'opening_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
                 'delivery_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
                 'coverage': {'type': 'radius', 'center': {'lat': 28.4, 'lng': 45.9}, 'radius_meters': 2000},
                 'delivery_fee_halalas': 500, 'pickup_enabled': True, 'car_enabled': True, 'delivery_enabled': True}
    configured = mutate('PATCH', '/api/admin/branches/kps-branch-1', {'settings': operating})
    assert configured.status_code == 200, configured.text
    for disabled in ('pickup_enabled', 'car_enabled', 'delivery_enabled'):
        settings = mutate('PATCH', '/api/admin/branches/kps-branch-1', {'settings': {disabled: False}}).json()['settings']
        assert settings[disabled] is False
        assert all(settings[other] is True for other in ('pickup_enabled', 'car_enabled', 'delivery_enabled') if other != disabled)
        assert settings['delivery_fee_halalas'] == 500 and settings['coverage'] == operating['coverage']
        assert mutate('PATCH', '/api/admin/branches/kps-branch-1', {'settings': {disabled: True}}).status_code == 200


@pytest.mark.postgres
def test_one_branch_delivery_pause_records_owner_reason_without_touching_other_branch(owner):
    client,mutate=owner
    result=mutate('PATCH','/api/admin/branches/kps-branch-1',
                  {'settings':{'delivery_enabled':False},'reason':'أوقف المالك توصيل الفرع الأول'})
    assert result.status_code==200,result.text
    assert result.json()['settings']['delivery_enabled'] is False
    with db.tx() as conn:
        audit=conn.execute("SELECT details FROM audit_events WHERE action='branch.updated' AND entity_id='kps-branch-1' ORDER BY created_at DESC LIMIT 1").fetchone()
        assert audit['details']['reason']=='أوقف المالك توصيل الفرع الأول'
        other=conn.execute("SELECT settings FROM branches WHERE id='kps-branch-9'").fetchone()
        assert other['settings']['delivery_enabled'] is False
    assert mutate('PATCH','/api/admin/branches/kps-branch-1',
                  {'settings':{'delivery_enabled':False},'reason':'x'*501}).status_code==422
