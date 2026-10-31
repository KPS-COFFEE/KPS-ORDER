"""Notification recipients cannot change login identities or retain revoked scope."""
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app import auth, db, email_delivery, manager_mail
from tests.test_core import _make_user
from tests.test_v5_identity import _authenticate, _mail_setup


@pytest.mark.parametrize('value', ['yes', 1, None])
def test_preferences_require_explicit_boolean(value):
    with pytest.raises(ValidationError):
        manager_mail.Preferences(email='manager@fixture.invalid', enabled=value, reason='TEST ONLY')


@pytest.mark.parametrize('base', ['http://example.test', 'https://user:pass@example.test',
    'https://example.test/?token=secret', 'https://example.test/#secret', ''])
def test_mail_links_reject_credentials_or_unsecured_destinations(monkeypatch, base):
    monkeypatch.setenv('PUBLIC_BASE_URL', base)
    assert manager_mail.base_url() is None


@pytest.mark.postgres
def test_owner_settings_are_independent_of_login_and_cannot_target_driver(client, monkeypatch):
    owner = _make_user('owner')
    manager = _make_user('delivery_manager', branches=['kps-branch-1'])
    driver = _make_user('driver', branches=['kps-branch-1'])
    _authenticate(client, owner)
    monkeypatch.setattr(auth, 'require_owner_sms_step_up', lambda *_: None)
    body = dict(email='manager@fixture.invalid', enabled=True, reason='TEST ONLY notification address')
    response = client.put('/api/manager-mail/staff/' + manager['id'], json=body)
    assert response.status_code == 200, response.text
    assert client.get('/api/manager-mail/staff/' + manager['id']).json()['preferences']['email'] == body['email']
    with db.tx() as c:
        current = c.execute('SELECT * FROM users WHERE id=%s', (manager['id'],)).fetchone()
    for key in ('email', 'email_verified_at', 'phone', 'role', 'branch_ids', 'permission_denials', 'password_hash'):
        assert current[key] == manager[key]
    assert client.put('/api/manager-mail/staff/' + driver['id'], json=body).status_code == 404
    _authenticate(client, manager)
    assert client.get('/api/manager-mail/staff/' + manager['id']).status_code == 403
    assert client.put('/api/manager-mail/staff/' + manager['id'], json=body).status_code == 403


@pytest.mark.postgres
def test_queued_mail_is_deduplicated_and_cancelled_on_live_revocation(client, monkeypatch):
    _mail_setup(monkeypatch, state='active')
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://fixture.invalid')
    owner = _make_user('owner')
    manager = _make_user('delivery_manager', branches=['kps-branch-1'])
    monkeypatch.setattr(auth, 'require_owner_sms_step_up', lambda *_: None)
    body = manager_mail.Preferences(email='manager@fixture.invalid', enabled=True, reason='TEST ONLY')
    manager_mail.update(manager['id'], body, user=owner)
    with db.tx() as c:
        user = manager_mail.principal(c, manager['id'], 'kps')
        pref = manager_mail.preferences(c, manager['id'], 'kps')
        first = manager_mail.enqueue(c, user, pref, 'reports', '2026-10-30', '2026-10-30', 'Test', 'Synthetic only')
        assert manager_mail.enqueue(c, user, pref, 'reports', '2026-10-30', '2026-10-30', 'Test', 'Synthetic only') == first
        row = c.execute('SELECT * FROM email_outbox WHERE id=%s', (first,)).fetchone()
        assert manager_mail.authorized(c, row, email_delivery._payload(row))
        c.execute('UPDATE users SET branch_ids=%s WHERE id=%s', (db.json(['kps-branch-2']), manager['id']))
        assert not manager_mail.authorized(c, row, email_delivery._payload(row))
    sent = []
    monkeypatch.setattr(email_delivery, '_send', lambda row: sent.append(row['id']) or 'synthetic')
    assert email_delivery.dispatch_once()['attempted'] == 0
    assert sent == []
    with db.tx() as c:
        assert c.execute('SELECT status FROM email_outbox WHERE id=%s', (first,)).fetchone()['status'] == 'cancelled'


@pytest.mark.postgres
def test_address_change_cancels_old_recipient_and_disabled_user_is_ineligible(client, monkeypatch):
    _mail_setup(monkeypatch, state='active')
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://fixture.invalid')
    owner = _make_user('owner')
    manager = _make_user('delivery_manager', branches=['kps-branch-1'])
    monkeypatch.setattr(auth, 'require_owner_sms_step_up', lambda *_: None)
    body = manager_mail.Preferences(email='first@fixture.invalid', enabled=True, reason='TEST ONLY')
    manager_mail.update(manager['id'], body, user=owner)
    with db.tx() as c:
        user = manager_mail.principal(c, manager['id'], 'kps')
        mid = manager_mail.enqueue(c, user, manager_mail.preferences(c, user['id'], 'kps'),
            'reports', '2026-10-29', '2026-10-29', 'Test', 'Synthetic only')
        row = c.execute('SELECT * FROM email_outbox WHERE id=%s', (mid,)).fetchone()
    manager_mail.update(manager['id'], body.model_copy(update={'email':'second@fixture.invalid'}), user=owner)
    with db.tx() as c:
        assert not manager_mail.authorized(c, row, email_delivery._payload(row))
        c.execute('UPDATE users SET active=false WHERE id=%s', (manager['id'],))
        assert manager_mail.principal(c, manager['id'], 'kps') is None


@pytest.mark.postgres
def test_worker_enqueues_real_scoped_conversation_and_daily_report_only_once(client, monkeypatch):
    _mail_setup(monkeypatch, state='active')
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://fixture.invalid')
    owner = _make_user('owner')
    manager = _make_user('delivery_manager', branches=['kps-branch-1'])
    customer = _make_user('customer')
    monkeypatch.setattr(auth, 'require_owner_sms_step_up', lambda *_: None)
    manager_mail.update(manager['id'], manager_mail.Preferences(
        email='manager@fixture.invalid', enabled=True, reason='TEST ONLY'), user=owner)
    with db.tx() as c:
        for cid, branch in [('mail-inside', 'kps-branch-1'), ('mail-outside', 'kps-branch-2')]:
            c.execute("INSERT INTO conversations(id,brand_id,branch_id,customer_id,route,context_service,category) VALUES(%s,'kps',%s,%s,'delivery','delivery','help')", (cid, branch, customer['id']))
            c.execute('INSERT INTO chat_messages(id,conversation_id,sender_id,text) VALUES(%s,%s,%s,%s)',
                      (cid + '-message', cid, customer['id'], 'PRIVATE customer text must stay in app'))
    manager_mail.process_once()
    manager_mail.process_once()
    with db.tx() as c:
        rows = c.execute("SELECT * FROM email_outbox WHERE kind='manager_notice'").fetchall()
    assert len(rows) == 2
    assert all(row['recipient'] == 'manager@fixture.invalid' for row in rows)
    payloads = [email_delivery._payload(row) for row in rows]
    assert {p['_manager_mail']['category'] for p in payloads} == {'chats', 'reports'}
    assert next(p for p in payloads if p['_manager_mail']['category'] == 'chats')['_manager_mail']['resource'] == 'mail-inside'
    assert all('PRIVATE customer' not in p['text'] for p in payloads)
    assert all('mail-outside' not in p['text'] for p in payloads)
