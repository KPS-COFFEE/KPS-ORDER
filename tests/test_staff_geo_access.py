"""Owner-authorized foreign work access; all SMS/provider proof is synthetic."""
from __future__ import annotations

import json
import os
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import psycopg
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import ValidationError

from app import auth, db, employee_sms, exports, staff_geo, staff_geo_access as geo
from tests.test_core import _authenticate, _make_user
from tests.sms_policy_helpers import configure_sms, register_synthetic_mobile, sms_login, sms_step_up


def request(cookie=None):
    headers = [(b'host', b'testserver')]
    if cookie:
        headers.append((b'cookie', (geo.COOKIE + '=' + cookie).encode()))
    return Request({'type': 'http', 'method': 'GET', 'scheme': 'https', 'path': '/ops',
                    'headers': headers, 'query_string': b'', 'server': ('testserver', 443),
                    'client': ('10.0.0.2', 1234)})


@pytest.fixture
def network(monkeypatch):
    info = {'country_code': 'SA', 'client_ip': '37.224.1.1', 'source': 'synthetic-trusted-edge'}
    monkeypatch.setattr(staff_geo, 'enabled', lambda: True)
    monkeypatch.setattr(staff_geo, 'location', lambda request: dict(info))
    return info


@pytest.fixture
def geo_client(database):
    # Exercise the actual dependencies and authentication routers independently
    # of UI/ingress registration; those boundaries have separate tests.
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(employee_sms.router)
    app.include_router(geo.router)
    with TestClient(app, base_url='http://testserver', headers={'Origin': 'http://testserver'}) as client:
        yield client


def grant(client, monkeypatch, *, target=None, owner=None):
    target = target or _make_user('admin')
    owner = owner or _make_user('owner')
    _authenticate(client, owner)
    sms_step_up(client, owner, monkeypatch)
    result = client.post('/api/admin/staff-geo/grants',
                         json={'user_id': target['id'], 'hours': 24, 'reason': 'Approved temporary travel access'})
    assert result.status_code == 200, result.text
    payload = result.json()
    token = parse_qs(urlsplit(payload['entry_url']).fragment)['token'][0]
    return owner, target, payload['grant'], token


@pytest.mark.parametrize('hours', [0, 169, True, '24', 1.5])
def test_grant_duration_is_bounded_integer(hours):
    with pytest.raises(ValidationError):
        geo.GrantBody(user_id='staff', hours=hours, reason='Approved travel')


@pytest.mark.parametrize('reason', ['', '  ', ' a ', 'x' * 501])
def test_grants_and_revocations_require_real_reason(reason):
    with pytest.raises(ValidationError):
        geo.GrantBody(user_id='staff', reason=reason)
    with pytest.raises(ValidationError):
        geo.RevokeBody(reason=reason)


def test_domestic_and_customer_paths_do_not_read_grants(network, monkeypatch):
    monkeypatch.setattr(db, 'tx', lambda *a, **kw: pytest.fail('No grant lookup needed'))
    geo.require_entry(request())
    geo.require_user(None, request(), {'id': 'staff', 'role': 'admin'})
    network.update(country_code='ZZ', client_ip='1.1.1.1')
    geo.require_user(None, request(), {'id': 'customer', 'role': 'customer'})


@pytest.mark.parametrize('role', ['owner', 'admin', 'delivery_manager', 'delivery_staff', 'branch_manager', 'driver'])
def test_no_role_has_implicit_foreign_exception(network, role):
    network.update(country_code='ZZ', client_ip='1.1.1.1')
    with pytest.raises(HTTPException) as error:
        geo.require_user(None, request(), {'id': 'staff', 'role': role})
    assert error.value.status_code == 403
    assert error.value.detail['code'] == 'STAFF_GEO_RESTRICTED'


@pytest.mark.parametrize('cookie', [None, '', 'invalid', 'a' * 44, 'a' * 10000])
def test_anonymous_foreign_entry_rejects_before_database(network, monkeypatch, cookie):
    network.update(country_code='ZZ', client_ip='1.1.1.1')
    monkeypatch.setattr(db, 'tx', lambda *a, **kw: pytest.fail('Malformed cookie must not reach database'))
    with pytest.raises(HTTPException) as error:
        geo.require_entry(request(cookie))
    assert error.value.detail['code'] == 'STAFF_GEO_RESTRICTED'


@pytest.mark.parametrize('ip', [None, '', 'malformed', '127.0.0.1', '10.0.0.2', '203.0.113.1'])
def test_absent_trusted_public_ip_cannot_be_overridden(network, ip):
    network.update(country_code='SA', client_ip=ip)
    with pytest.raises(HTTPException) as error:
        geo.require_entry(request('a' * 43))
    assert error.value.detail['code'] == 'STAFF_GEO_LOCATION_UNVERIFIED'


@pytest.mark.postgres
def test_owner_stepup_scope_and_secret_handling(geo_client, network, monkeypatch):
    owner, target = _make_user('owner'), _make_user('admin')
    _authenticate(geo_client, owner)
    payload = {'user_id': target['id'], 'reason': 'Temporary owner approved access'}
    denied = geo_client.post('/api/admin/staff-geo/grants', json=payload)
    assert denied.status_code == 403 and denied.json()['detail']['code'] == 'OWNER_STEP_UP_REQUIRED'
    sms_step_up(geo_client, owner, monkeypatch)
    created = geo_client.post('/api/admin/staff-geo/grants', json=payload)
    assert created.status_code == 200, created.text
    body = created.json()
    token = parse_qs(urlsplit(body['entry_url']).fragment)['token'][0]
    assert body['grant']['user_id'] == target['id'] and body['grant']['approved_by'] == owner['id']
    listing = geo_client.get('/api/admin/staff-geo/grants', params={'user_id': target['id']})
    assert listing.status_code == 200 and len(listing.json()['grants']) == 1
    assert token not in listing.text and 'token_hash' not in listing.text
    with db.tx() as conn:
        row = conn.execute('SELECT * FROM staff_geo_access_grants WHERE id=%s', (body['grant']['id'],)).fetchone()
        assert row['entry_token_hash'] == auth.token_hash(token) and row['access_token_hash'] is None
        audit = conn.execute('SELECT details FROM audit_events WHERE entity_id=%s', (row['id'],)).fetchall()
        assert token not in json.dumps(audit, default=str) and row['entry_token_hash'] not in json.dumps(audit, default=str)
        conn.execute("INSERT INTO brands(id,name) VALUES('geo_other','Other synthetic brand')")
    other = _make_user('admin', brand='geo_other')
    assert geo_client.post('/api/admin/staff-geo/grants', json={**payload, 'user_id': other['id']}).status_code == 404
    assert geo_client.get('/api/admin/staff-geo/grants', params={'user_id': other['id']}).status_code == 404
    _authenticate(geo_client, target)
    assert geo_client.post('/api/admin/staff-geo/grants', json=payload).status_code == 403
    assert geo_client.get('/api/admin/staff-geo/grants').status_code == 403
    assert geo_client.post('/api/admin/staff-geo/grants/' + body['grant']['id'] + '/revoke', json={'reason': 'Cannot approve own grant'}).status_code == 403


@pytest.mark.postgres
def test_entry_is_single_use_and_cookie_is_not_a_login(geo_client, network, monkeypatch):
    owner, target, created, token = grant(geo_client, monkeypatch)
    network.update(country_code='ZZ', client_ip='1.1.1.1')
    no_origin = geo_client.headers.pop('Origin')
    assert geo_client.post('/api/staff-geo/redeem', json={'token': token}).status_code == 403
    geo_client.headers['Origin'] = no_origin
    redeemed = geo_client.post('/api/staff-geo/redeem', json={'token': token})
    assert redeemed.status_code == 200, redeemed.text
    assert token not in redeemed.text and redeemed.json()['redirect'] == '/ops'
    cookie = geo_client.cookies.get(geo.COOKIE)
    assert cookie and cookie != token
    header = next(h for h in redeemed.headers.get_list('set-cookie') if h.startswith(geo.COOKIE + '='))
    assert 'HttpOnly' in header and 'SameSite=strict' in header and 'Domain=' not in header
    # Redeeming gives no user session and clears an unrelated older work login.
    assert geo_client.get('/api/session').json()['user'] is None
    geo.require_entry(request(cookie))
    assert geo_client.post('/api/staff-geo/redeem', json={'token': token}).status_code == 403
    with db.tx() as conn:
        row = conn.execute('SELECT * FROM staff_geo_access_grants WHERE id=%s', (created['id'],)).fetchone()
        assert row['access_token_hash'] == auth.token_hash(cookie) and row['redeemed_at']
    # Authenticated wrong people, including an owner, cannot use this browser grant.
    _authenticate(geo_client, owner)
    geo_client.cookies.set(geo.COOKIE, cookie)
    assert geo_client.get('/api/session').status_code == 403
    _authenticate(geo_client, target)
    geo_client.cookies.set(geo.COOKIE, cookie)
    current = geo_client.get('/api/session')
    assert current.status_code == 200 and current.json()['user']['id'] == target['id']
    # The grant never grants the recipient owner/admin configuration authority.
    assert geo_client.get('/api/admin/staff-geo/grants').status_code == 403


@pytest.mark.postgres
def test_live_revoke_expiry_and_owner_deactivation_stop_existing_sessions(geo_client, network, monkeypatch):
    owner, target, created, token = grant(geo_client, monkeypatch)
    assert geo_client.post('/api/staff-geo/redeem', json={'token': token}).status_code == 200
    cookie = geo_client.cookies.get(geo.COOKIE)
    _authenticate(geo_client, target)
    geo_client.cookies.set(geo.COOKIE, cookie)
    network.update(country_code='ZZ', client_ip='1.1.1.1')
    assert geo_client.get('/api/session').status_code == 200
    with db.tx() as conn:
        conn.execute('UPDATE users SET active=false WHERE id=%s', (owner['id'],))
    assert geo_client.get('/api/session').status_code == 403
    with db.tx() as conn:
        conn.execute('UPDATE users SET active=true WHERE id=%s', (owner['id'],))
        conn.execute("UPDATE staff_geo_access_grants SET created_at=now()-interval '2 hours',expires_at=now()-interval '1 hour' WHERE id=%s", (created['id'],))
    assert geo_client.get('/api/session').status_code == 403
    network.update(country_code='SA', client_ip='37.224.1.1')
    owner, target, current, token = grant(geo_client, monkeypatch, owner=owner, target=target)
    owner_cookies = dict(geo_client.cookies)
    owner_csrf = geo_client.headers['X-CSRF-Token']
    assert geo_client.post('/api/staff-geo/redeem', json={'token': token}).status_code == 200
    cookie = geo_client.cookies.get(geo.COOKIE)
    geo_client.cookies.clear()
    geo_client.cookies.update(owner_cookies)
    geo_client.headers['X-CSRF-Token'] = owner_csrf
    revoked = geo_client.post('/api/admin/staff-geo/grants/' + current['id'] + '/revoke', json={'reason': 'Travel authorization ended'})
    assert revoked.status_code == 200 and revoked.json()['grant']['status'] == 'revoked'
    _authenticate(geo_client, target)
    geo_client.cookies.set(geo.COOKIE, cookie)
    network.update(country_code='ZZ', client_ip='1.1.1.1')
    assert geo_client.get('/api/session').status_code == 403
    with pytest.raises(HTTPException):
        geo.require_entry(request(cookie))


@pytest.mark.postgres
def test_sms_sends_and_verification_bind_exact_exception_person(geo_client, network, monkeypatch):
    target = register_synthetic_mobile(_make_user('admin'))
    other = register_synthetic_mobile(_make_user('admin'))
    _, target, _, token = grant(geo_client, monkeypatch, target=target)
    network.update(country_code='ZZ', client_ip='1.1.1.1')
    assert geo_client.post('/api/staff-geo/redeem', json={'token': token}).status_code == 200
    calls = configure_sms(monkeypatch)
    blocked = geo_client.post('/api/identity/staff/code/request', json={'brand_id': 'kps', 'phone': other['phone']})
    assert blocked.status_code == 403 and not calls
    allowed = geo_client.post('/api/identity/staff/code/request', json={'brand_id': 'kps', 'phone': target['phone']})
    assert allowed.status_code == 200 and calls
    verified = geo_client.post('/api/identity/staff/code/verify', json={'challenge_id': allowed.json()['challenge_id'], 'code': '0123'})
    assert verified.status_code == 200, verified.text
    assert verified.json()['user']['id'] == target['id']
    # Replaying an unapproved person's valid challenge from a domestic request
    # must still be rejected before calling the provider's verify operation.
    network.update(country_code='SA', client_ip='37.224.1.1')
    challenge = geo_client.post('/api/identity/staff/code/request', json={'brand_id': 'kps', 'phone': other['phone']}).json()
    network.update(country_code='ZZ', client_ip='1.1.1.1')
    before = len(calls)
    blocked = geo_client.post('/api/identity/staff/code/verify', json={'challenge_id': challenge['challenge_id'], 'code': '0123'})
    assert blocked.status_code == 403 and len(calls) == before


@pytest.mark.postgres
def test_customer_shared_session_stays_available_abroad(geo_client, network):
    customer = _make_user('customer')
    _authenticate(geo_client, customer)
    network.update(country_code=None, client_ip=None)
    result = geo_client.get('/api/session')
    assert result.status_code == 200 and result.json()['user']['id'] == customer['id']


@pytest.mark.postgres
def test_exports_exclude_credentials_and_restores_retire_them(geo_client, network, monkeypatch):
    _, _, created, token = grant(geo_client, monkeypatch)
    assert geo_client.post('/api/staff-geo/redeem', json={'token': token}).status_code == 200
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        business, bm = exports.build_archive(conn, brand_id='kps')
        full, fm = exports.build_archive(conn, full=True)
    table = 'staff_geo_access_grants'
    assert table in bm['excluded_tables'] and table not in bm['table_order']
    assert table in fm['table_order'] and fm['counts'][table] == 1
    assert exports._scoped_sql(table, 'kps') == ('brand_id=%s', ('kps',))
    schema, base = 'kps_restore_' + uuid4().hex, os.environ['TEST_DATABASE_URL']
    try:
        result = exports.restore_archive(full, target_url=base, target_schema=schema)
        assert result['verified'] and result['staff_geo_grants_revoked'] == 1
        with psycopg.connect(base) as conn:
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            row = conn.execute('SELECT revoked_at,revoked_by,revocation_source FROM staff_geo_access_grants WHERE id=%s', (created['id'],)).fetchone()
            assert row[0] and row[1] is None and row[2] == 'restore'
        with db.tx() as conn:
            assert conn.execute('SELECT revoked_at FROM staff_geo_access_grants WHERE id=%s', (created['id'],)).fetchone()['revoked_at'] is None
    finally:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))


@pytest.mark.postgres
def test_full_application_foreign_entry_otp_and_revocation(database, monkeypatch):
    """Exercise real ingress metadata, middleware, static shell and live DB auth."""
    monkeypatch.setenv('STAFF_GEO_MODE', 'enforce')
    monkeypatch.setenv('STAFF_GEO_SOURCE', 'render-cloudflare')
    monkeypatch.setenv('STAFF_GEO_TRUSTED_PROXY_CIDRS', '10.0.0.0/8')
    from app.main import app
    headers = {'Origin': 'http://testserver', 'CF-Ray': 'a4ff521a6e78c090-FRA',
               'CF-Connecting-IP': '1.1.1.1', 'CF-IPCountry': 'US'}
    with TestClient(app, base_url='http://testserver', headers=headers,
                    client=('10.195.1.2', 1234)) as client:
        blocked = client.get('/ops')
        assert blocked.status_code == 403 and 'text/html' in blocked.headers['content-type']
        assert client.get('/').status_code == 200
        assert client.get('/api/catalog').status_code == 200
        client.headers.update({'CF-IPCountry': 'SA', 'CF-Connecting-IP': '37.224.1.1'})
        owner, target, created, token = grant(client, monkeypatch)
        owner_cookies, owner_csrf = dict(client.cookies), client.headers['X-CSRF-Token']
        client.headers.update({'CF-IPCountry': 'US', 'CF-Connecting-IP': '1.1.1.1'})
        # Even the current owner's domestic session is not a foreign exemption.
        assert client.get('/ops').status_code == 403
        redeemed = client.post('/api/staff-geo/redeem', json={'token': token})
        assert redeemed.status_code == 200, redeemed.text
        assert client.get('/ops').status_code == 200
        assert client.get('/api/session').json()['user'] is None
        sms_login(client, target, monkeypatch)
        current = client.get('/api/session')
        assert current.status_code == 200 and current.json()['user']['id'] == target['id']
        target_cookies, target_csrf = dict(client.cookies), client.headers['X-CSRF-Token']
        client.headers.update({'CF-IPCountry': 'SA', 'CF-Connecting-IP': '37.224.1.1'})
        client.cookies.clear()
        client.cookies.update(owner_cookies)
        client.headers['X-CSRF-Token'] = owner_csrf
        revoked = client.post('/api/admin/staff-geo/grants/' + created['id'] + '/revoke',
                              json={'reason': 'Owner ended the foreign authorization'})
        assert revoked.status_code == 200, revoked.text
        client.headers.update({'CF-IPCountry': 'US', 'CF-Connecting-IP': '1.1.1.1'})
        client.cookies.clear()
        client.cookies.update(target_cookies)
        client.headers['X-CSRF-Token'] = target_csrf
        assert client.get('/ops').status_code == 403
        assert client.get('/api/session').status_code == 403
        assert client.get('/').status_code == 200
        assert client.get('/api/catalog', headers={'X-KPS-Context': 'customer'}).status_code == 200
        customer = _make_user('customer')
        _authenticate(client, customer)
        result = client.get('/api/session')
        assert result.status_code == 200 and result.json()['user']['id'] == customer['id']


@pytest.mark.postgres
def test_multibrand_grants_bind_canonical_identity_and_live_owner_scope(geo_client, network, monkeypatch):
    from app import multibrand
    from psycopg.conninfo import conninfo_to_dict
    binding = conninfo_to_dict(os.environ['DATABASE_URL'])
    for key, value in {
        'ENBISHAN_ISOLATED_ENABLED': 'false', 'KPS_MULTIBRAND_ENABLED': 'true',
        'RUNTIME_REVISION': 'a' * 40, 'KPS_MULTIBRAND_ACCEPTANCE_SHA': 'a' * 40,
        'KPS_MULTIBRAND_OWNER_APPROVAL_SHA': 'a' * 40,
        'KPS_MULTIBRAND_DATABASE_NAME': binding['dbname'],
        'KPS_MULTIBRAND_DATABASE_HOST': binding['host'],
    }.items():
        monkeypatch.setenv(key, value)
    with db.tx() as conn:
        multibrand.migrate(conn)
        conn.execute("INSERT INTO brands(id,name) VALUES('enbishan','Synthetic Enbishan')")
        conn.execute("INSERT INTO app_settings(brand_id) VALUES('enbishan')")
        conn.execute("INSERT INTO branches(id,brand_id,branch_no,source_id,name,city) VALUES('geo-enbishan','enbishan',1,'geo-test','Synthetic branch','Test')")
    owner, shared = _make_user('owner'), _make_user('admin', branches=['kps-branch-1'])
    native = _make_user('admin', brand='enbishan', branches=['geo-enbishan'])
    with db.tx() as conn:
        for user in (owner, shared):
            conn.execute("INSERT INTO staff_brand_memberships(user_id,brand_id,branch_ids,granted_by) VALUES(%s,'enbishan','[\"geo-enbishan\"]',%s)", (user['id'], owner['id']))
    _authenticate(geo_client, owner)
    sms_step_up(geo_client, owner, monkeypatch)
    geo_client.headers['X-Operations-Brand'] = 'enbishan'
    # The owner is projected into Enbishan, but the granted account stays the
    # canonical KPS user ID and home brand in the stored approval.
    result = geo_client.post('/api/admin/staff-geo/grants', json={'user_id': shared['id'], 'reason': 'Shared staff approved travel'})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['grant']['brand_id'] == 'kps'
    token = parse_qs(urlsplit(body['entry_url']).fragment)['token'][0]
    network.update(country_code='ZZ', client_ip='1.1.1.1')
    assert geo_client.post('/api/staff-geo/redeem', json={'token': token}).status_code == 200
    cookie = geo_client.cookies.get(geo.COOKIE)
    _authenticate(geo_client, shared)
    geo_client.cookies.set(geo.COOKIE, cookie)
    current = geo_client.get('/api/session')
    assert current.status_code == 200, current.text
    assert current.json()['user']['id'] == shared['id'] and current.json()['user']['brand_id'] == 'enbishan'
    assert geo_client.get('/api/session', headers={'X-Operations-Brand': 'unauthorized'}).status_code == 403
    with db.tx() as conn:
        conn.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id=%s AND brand_id='enbishan'", (shared['id'],))
    assert geo_client.get('/api/session').status_code == 403
    geo_client.headers.pop('X-Operations-Brand')
    assert geo_client.get('/api/session').status_code == 200
    # A grant for an Enbishan-native identity also depends on the issuer's
    # continuing authority in that brand, even after redemption and login.
    network.update(country_code='SA', client_ip='37.224.1.1')
    _, _, _, token = grant(geo_client, monkeypatch, owner=owner, target=native)
    assert geo_client.post('/api/staff-geo/redeem', json={'token': token}).status_code == 200
    cookie = geo_client.cookies.get(geo.COOKIE)
    _authenticate(geo_client, native)
    geo_client.cookies.set(geo.COOKIE, cookie)
    network.update(country_code='ZZ', client_ip='1.1.1.1')
    assert geo_client.get('/api/session').status_code == 200
    with db.tx() as conn:
        conn.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id=%s AND brand_id='enbishan'", (owner['id'],))
    assert geo_client.get('/api/session').status_code == 403
