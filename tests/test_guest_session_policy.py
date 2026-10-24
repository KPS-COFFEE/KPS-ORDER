"""Focused checks for browser-bound guest persistence; no phone-based recovery."""
from datetime import datetime, timezone
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from fastapi import Response
from starlette.requests import Request

from app import auth


@pytest.mark.parametrize('role,kind,hours', [
    ('customer', 'guest', 720),
    ('customer', 'phone_sms_verified', 12),
    ('driver', 'staff_email_verified', 12),
    ('owner', 'guest', 12),
])
def test_guest_persistence_does_not_extend_staff_sessions(monkeypatch, role, kind, hours):
    monkeypatch.setenv('SESSION_COOKIE_SECURE', 'true')
    # TTL is a legacy session serialization unit contract; KPS mandatory SMS
    # requires real proof and is covered through canonical endpoints separately.
    monkeypatch.setenv('SESSION_CONTEXT_COOKIES_ENABLED', 'false')
    monkeypatch.setattr(auth.db, 'audit', Mock())
    conn, response = Mock(), Response()
    before = datetime.now(timezone.utc)
    result = auth.establish_session(conn, {
        'id': 'synthetic-user', 'role': role, 'identity_kind': kind, 'brand_id': 'synthetic-legacy',
    }, response)
    inserted = [call.args for call in conn.execute.call_args_list
                if call.args[0].startswith('INSERT INTO sessions')]
    assert len(inserted) == 1
    sql, params = inserted[0]
    assert hours * 3600 <= (params[4] - before).total_seconds() < hours * 3600 + 5
    cookies = response.headers.getlist('set-cookie')
    session = next(c for c in cookies if c.startswith(auth.COOKIE + '='))
    assert f'Max-Age={hours * 3600}' in session
    assert 'HttpOnly' in session and 'Secure' in session and 'SameSite=strict' in session
    assert params[1] not in session  # only hashes are stored in the database
    assert result['user']['id'] == 'synthetic-user'


@pytest.mark.parametrize('valid_csrf', [True, False])
def test_only_existing_proven_browser_session_can_be_renewed(monkeypatch, valid_csrf):
    csrf = 'synthetic-csrf'
    user = {'id': 'guest-a', 'role': 'customer', 'identity_kind': 'guest',
            'session_id': 'session-a', 'csrf_hash': auth.token_hash(csrf)}
    monkeypatch.setattr(auth, '_find_session', lambda conn, request: user)
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = {'id': 'session-a'}

    @contextmanager
    def transaction():
        yield conn

    monkeypatch.setattr(auth.db, 'tx', transaction)
    request = Request({'type': 'http', 'method': 'GET', 'path': '/api/session',
                       'headers': [(b'cookie', (auth.COOKIE + '=browser-secret; ' +
                                    auth.CSRF_COOKIE + '=' + (csrf if valid_csrf else 'forged')).encode())]})
    response = Response()
    result = auth.session(request, response)
    assert response.headers['cache-control'] == 'no-store'
    if valid_csrf:
        sql, params = conn.execute.call_args.args
        assert 'revoked_at IS NULL AND expires_at>now()' in sql
        assert params == ('session-a',)
        assert 'Max-Age=2592000' in response.headers['set-cookie']
        assert result['user']['id'] == 'guest-a'
    else:
        conn.execute.assert_not_called()
        assert 'set-cookie' not in response.headers
        assert result['reauthentication_required'] is True


def test_guest_phone_cannot_grant_other_customer_order_or_chat_access():
    from app.chat import allowed
    user = {'id': 'guest-a', 'brand_id': 'kps', 'role': 'customer',
            'phone': '+966501112233', 'identity_kind': 'guest'}
    other = {'customer_id': 'guest-b', 'brand_id': 'kps', 'phone': user['phone']}
    assert auth.can_access_order(user, other, Mock()) is False
    assert allowed(user, other) is False
    assert auth.can_access_order(user, {**other, 'customer_id': 'guest-a'}, Mock()) is True
