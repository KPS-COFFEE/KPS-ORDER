"""Synthetic identities; real isolated PostgreSQL and independent cookie jars."""
import pytest
from fastapi.testclient import TestClient
from app import auth, db
from tests.test_employee_sms_portals import sms_setup
from tests.test_customer_experience import verify


@pytest.mark.postgres
@pytest.mark.parametrize('role', ['owner', 'delivery_manager', 'driver'])
def test_same_mobile_preserves_work_identity_and_independent_logout(client, monkeypatch, role):
    from app.main import app
    client.headers['X-KPS-Context'] = 'work'
    user, calls = sms_setup(monkeypatch)
    with db.tx() as conn:
        conn.execute('UPDATE users SET role=%s,email_verified_at=NULL WHERE id=%s', (role,user['id']))
        before = conn.execute('SELECT * FROM users WHERE id=%s',(user['id'],)).fetchone()
        shift_before = None
        if role == 'driver':
            conn.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES('same_mobile_driver_shift','driver',%s,'kps','[]')",(user['id'],))
            shift_before = conn.execute("SELECT * FROM shifts WHERE id='same_mobile_driver_shift'").fetchone()
    requested = client.post('/api/identity/staff/code/request', json={'phone':'0507778899'})
    assert requested.status_code == 200, requested.text
    signed = client.post('/api/identity/staff/code/verify', json={'challenge_id':requested.json()['challenge_id'],'code':'٠١٢٣'})
    assert signed.status_code == 200, signed.text
    client.headers['X-CSRF-Token'] = signed.json()['csrf_token']
    work_token = client.cookies.get(auth.context_cookie_names('work')[0])
    assert work_token
    assert 'domain=' not in signed.headers.get('set-cookie','').lower()
    with TestClient(app) as shopper:
        shopper.headers['Origin'] = 'http://testserver'
        shopper.headers['X-KPS-Context'] = 'customer'
        first = verify(shopper,monkeypatch,phone='0507778899')
        assert first['user']['role'] == 'customer' and first['user']['id'] != user['id']
        assert client.get('/api/session').json()['user']['id'] == user['id']
        assert shopper.post('/api/auth/logout').status_code == 200
        assert client.get('/api/session').json()['user']['id'] == user['id']
        second = verify(shopper,monkeypatch,phone='0507778899')
        assert second['user']['id'] == first['user']['id']
        assert client.post('/api/auth/logout').status_code == 200
        assert shopper.get('/api/session').json()['user']['id'] == first['user']['id']
    with db.tx() as conn:
        assert conn.execute('SELECT * FROM users WHERE id=%s',(user['id'],)).fetchone() == before
        assert conn.execute("SELECT count(*) n FROM users WHERE role='customer' AND phone='+966507778899'").fetchone()['n'] == 1
        assert conn.execute('SELECT step_up_at FROM sessions WHERE token_hash=%s',(auth.token_hash(work_token),)).fetchone()['step_up_at'] is None
        if shift_before:
            assert conn.execute("SELECT * FROM shifts WHERE id='same_mobile_driver_shift'").fetchone() == shift_before

