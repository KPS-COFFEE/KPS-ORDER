"""Real PostgreSQL sessions sharing one browser cookie jar, without SMS sends."""
from http.cookies import SimpleCookie
import pytest
from fastapi import Response
from app import auth,db
from tests.sms_policy_helpers import sms_login
from tests.test_core import _make_user,_authenticate
from tests.test_employee_sms_portals import request


@pytest.fixture(autouse=True)
def context_mode(monkeypatch):
    monkeypatch.setenv('SESSION_CONTEXT_COOKIES_ENABLED','true')
    monkeypatch.setenv('KPS_PORTAL_HOST_ROUTING_ENABLED','false')


@pytest.fixture
def client(database):
    # Exercise real auth endpoints and PostgreSQL, independent of concurrently
    # authored optional UI modules in the integration application.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app=FastAPI();app.include_router(auth.router)
    with TestClient(app,base_url='http://testserver',headers={'Origin':'http://testserver'}) as browser:
        yield browser


def establish(client,user,monkeypatch):
    if user['role'] != 'customer':
        with db.tx() as conn:
            user = conn.execute('SELECT * FROM users WHERE id=%s',(user['id'],)).fetchone()
        return sms_login(client, user, monkeypatch)
    response=Response()
    with db.tx() as conn:
        result=auth.establish_session(conn,user,response,customer_verified=user['role']=='customer')
    for header in response.headers.getlist('set-cookie'):
        values=SimpleCookie();values.load(header)
        for name,morsel in values.items():
            assert not morsel['domain']
            client.cookies.set(name,morsel.value,domain='testserver.local',path='/')
    return result


def who(client,context):
    return client.get('/api/session',headers={'X-KPS-Context':context}).json()


@pytest.mark.postgres
@pytest.mark.parametrize('role',['owner','delivery_manager','driver'])
def test_same_phone_one_jar_separate_sessions_and_logout(client,role,monkeypatch):
    worker=_make_user(role,branches=['kps-branch-1'])
    customer=_make_user('customer')
    with db.tx() as conn:
        conn.execute("UPDATE users SET phone='+966507778899' WHERE id=ANY(%s)",([worker['id'],customer['id']],))
        if role=='driver':
            conn.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES('synthetic_context_shift','driver',%s,'kps','[\"kps-branch-1\"]')",(worker['id'],))
    work=establish(client,worker,monkeypatch)
    work_cookie=client.cookies['kps_work_session']
    personal=establish(client,customer,monkeypatch)
    assert client.cookies['kps_work_session']==work_cookie
    assert who(client,'work')['user']['id']==worker['id']
    assert who(client,'customer')['user']['id']==customer['id']
    # Customer CSRF cannot terminate the independently authenticated work session.
    assert client.post('/api/auth/logout',headers={'X-KPS-Context':'work','X-CSRF-Token':personal['csrf_token']}).status_code==403
    assert client.post('/api/auth/logout',headers={'X-KPS-Context':'customer','X-CSRF-Token':personal['csrf_token']}).status_code==200
    assert who(client,'customer')['user'] is None
    assert who(client,'work')['user']['id']==worker['id']
    if role=='driver':
        with db.tx() as conn:
            assert conn.execute("SELECT status FROM shifts WHERE id='synthetic_context_shift'").fetchone()['status']=='open'
    personal=establish(client,customer,monkeypatch)
    assert client.post('/api/auth/logout',headers={'X-KPS-Context':'work','X-CSRF-Token':work['csrf_token']}).status_code==200
    assert who(client,'work')['user'] is None
    assert who(client,'customer')['user']['id']==customer['id']
    with db.tx() as conn:
        assert conn.execute('SELECT role FROM users WHERE id=%s',(worker['id'],)).fetchone()['role']==role


@pytest.mark.postgres
def test_legacy_owner_access_survives_customer_login_and_logout(client,monkeypatch):
    owner=_make_user('owner')
    legacy_token,legacy_csrf=_authenticate(client,owner)
    customer=_make_user('customer')
    personal=establish(client,customer,monkeypatch)
    assert client.cookies[auth.COOKIE]==legacy_token
    assert who(client,'work')['user']['id']==owner['id']
    assert client.post('/api/auth/logout',headers={'X-KPS-Context':'customer','X-CSRF-Token':personal['csrf_token']}).status_code==200
    assert client.cookies[auth.COOKIE]==legacy_token
    assert who(client,'work')['user']['id']==owner['id']
    # Work login replacement plus logout must not revive the legacy credential.
    work=establish(client,owner,monkeypatch)
    assert client.post('/api/auth/logout',headers={'X-KPS-Context':'work','X-CSRF-Token':work['csrf_token']}).status_code==200
    assert who(client,'work')['user'] is None
    assert who(client,'work')['user'] is None


@pytest.mark.postgres
def test_copied_work_credential_cannot_be_used_as_customer(client,monkeypatch):
    owner=_make_user('owner')
    establish(client,owner,monkeypatch)
    client.cookies.set('kps_customer_session',client.cookies['kps_work_session'],domain='testserver.local',path='/')
    client.cookies.set('kps_customer_csrf',client.cookies['kps_work_csrf'],domain='testserver.local',path='/')
    assert who(client,'customer')['user'] is None
    assert who(client,'work')['user']['id']==owner['id']


@pytest.mark.parametrize('path,header,expected',[
    ('/api/auth/otp/verify','work','customer'),('/api/identity/staff/code/verify','customer','work'),
    ('/api/identity/owner/sms/verify','customer','work'),('/api/identity/owner-phone/verify','customer','work'),
    ('/sms-login-test','customer','work'),('/ops','customer','work'),('/api/session','customer','customer')])
def test_server_route_context_overrides_ui_hint(path,header,expected):
    scope=dict(request('order.kps.sa').scope)
    scope['path']=path
    scope['headers']=scope['headers']+[(b'x-kps-context',header.encode())]
    from fastapi import Request
    assert auth.session_context(Request(scope))==expected
