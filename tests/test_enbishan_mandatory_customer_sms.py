"""Enbishan browsing stays public; only current SMS proof permits checkout.

Unit certificates need no database/provider. PostgreSQL cases use the existing
explicit local UUID-schema fixture and never use a runtime DSN or send an SMS.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import auth, db, orders


class SettingsConnection:
    def __init__(self, settings=None):
        self.settings = settings

    def execute(self, statement, params):
        assert statement.startswith('SELECT settings FROM app_settings')
        return SimpleNamespace(fetchone=lambda: None if self.settings is None else {'settings': self.settings})


def customer(**changes):
    return {'id': 'synthetic-enbishan-customer', 'brand_id': 'enbishan',
            'role': 'customer', 'active': True, 'display_name': 'Synthetic Customer',
            'phone': '+966500000001', 'identity_kind': 'guest', **changes}


@pytest.mark.parametrize('settings', [None, {}, {'customer_phone_required': False}, {'customer_phone_required': True}])
def test_enbishan_checkout_requires_sms_even_with_old_guest_setting(settings):
    assert auth.customer_phone_required(SettingsConnection(settings), 'enbishan') is True


@pytest.mark.parametrize('brand,settings,expected', [
    ('kps', None, True), ('kps', {'customer_phone_required': False}, False),
    ('kps', {'customer_phone_required': True}, True),
    ('synthetic-other-brand', None, False),
    ('synthetic-other-brand', {'customer_phone_required': True}, True),
])
def test_other_brand_policies_are_preserved(brand, settings, expected):
    assert auth.customer_phone_required(SettingsConnection(settings), brand) is expected


@pytest.mark.parametrize('override', [None, False, True])
@pytest.mark.parametrize('proof', [
    {},
    {'email_verified_at': datetime.now(timezone.utc), 'identity_kind': 'email_verified'},
    {'phone_verified_at': datetime.now(timezone.utc), 'identity_kind': 'phone_whatsapp_verified'},
    {'phone_sms_verified_at': datetime.now(timezone.utc)},
    {'phone_sms_verified_at': datetime.now(timezone.utc),
     'customer_verified_at': datetime.now(timezone.utc) - timedelta(days=31)},
])
def test_contact_or_other_identity_cannot_replace_current_sms_session(override, proof):
    with pytest.raises(HTTPException) as error:
        auth.assert_customer_order_eligible(SettingsConnection({'customer_phone_required': False}),
                                            customer(**proof), phone_required=override)
    assert error.value.status_code == 403
    assert error.value.detail['code'] == 'CUSTOMER_PHONE_VERIFICATION_REQUIRED'


def test_current_sms_proof_allows_checkout_even_when_provider_is_unavailable(monkeypatch):
    monkeypatch.setenv('OTP_PROVIDER', 'disabled')
    now = datetime.now(timezone.utc)
    auth.assert_customer_order_eligible(SettingsConnection({'customer_phone_required': False}),
        customer(identity_kind='phone_sms_verified', phone_sms_verified_at=now,
                 phone_verified_at=now, customer_verified_at=now), phone_required=False)


def test_cached_guest_session_cannot_reach_order_creation_or_idempotent_replay(monkeypatch):
    @contextmanager
    def tx(**kwargs):
        yield SettingsConnection({'customer_phone_required': False})
    monkeypatch.setattr(db, 'tx', tx)
    monkeypatch.setattr(db, 'idempotent', lambda *a, **kw: pytest.fail('Unverified order reached idempotency'))
    body = orders.catalog.QuoteBody(brand_id='enbishan', branch_id='synthetic-branch', service='delivery',
        payment_method='cash', items=[{'product_id': 'synthetic-product', 'quantity': 1}],
        customer={'name': 'Synthetic Customer', 'phone': '0500000001', 'neighborhood': 'Synthetic area',
                  'address': 'Synthetic address', 'location': {'lat': 28.4, 'lng': 45.9}})
    with pytest.raises(HTTPException) as error:
        orders._create_order(body, None, customer(customer_phone_required=False), 'synthetic-replay-key')
    assert error.value.detail['code'] == 'CUSTOMER_PHONE_VERIFICATION_REQUIRED'


def test_delivery_manager_flow_keeps_its_operational_scope_check(monkeypatch):
    @contextmanager
    def tx(**kwargs):
        yield SettingsConnection({'customer_phone_required': False})
    monkeypatch.setattr(db, 'tx', tx)
    marker = RuntimeError('Reached operational scope; no SMS or order created')
    seen = []
    def scope(conn, actor, branch):
        seen.append((actor['role'], branch))
        raise marker
    monkeypatch.setattr(orders, 'delivery_center_scope', scope)
    body = orders.DeliveryCenterOrder(brand_id='enbishan', branch_id='synthetic-branch', service='delivery',
        payment_method='cash', items=[{'product_id': 'synthetic-product', 'quantity': 1}],
        customer={'name': 'Synthetic Customer', 'phone': '0500000001', 'neighborhood': 'Synthetic area',
                  'address': 'Synthetic address', 'location': {'lat': 28.4, 'lng': 45.9}})
    with pytest.raises(RuntimeError) as result:
        orders.create_delivery_center_order(body, None,
            {'id': 'synthetic-manager', 'brand_id': 'enbishan', 'role': 'delivery_manager',
             'branch_ids': ['synthetic-branch']}, 'synthetic-manager-key')
    assert result.value is marker
    assert seen == [('delivery_manager', 'synthetic-branch')]


def seed_enbishan():
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('enbishan','Synthetic Enbishan') ON CONFLICT(id) DO NOTHING")
        conn.execute("INSERT INTO app_settings(brand_id,settings) VALUES('enbishan','{\"customer_phone_required\":false,\"sentinel\":\"preserve\"}') ON CONFLICT(brand_id) DO UPDATE SET settings=EXCLUDED.settings")
        conn.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled) VALUES('enbishan','guest','active',true) ON CONFLICT(brand_id,channel) DO UPDATE SET state='active',enabled=true")


@pytest.mark.postgres
def test_public_browsing_requires_no_account_or_code(client):
    seed_enbishan()
    with db.tx() as conn:
        before = conn.execute('SELECT count(*) n FROM users').fetchone()['n']
    assert client.get('/api/catalog?brand_id=enbishan').status_code == 200
    policy = client.get('/api/identity/channels?brand_id=enbishan')
    assert policy.status_code == 200
    assert policy.json()['customer_phone_required'] is True
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM users').fetchone()['n'] == before
        assert conn.execute('SELECT count(*) n FROM sessions').fetchone()['n'] == 0


@pytest.mark.postgres
def test_existing_guest_contact_cannot_submit_without_sms(client, monkeypatch):
    seed_enbishan()
    guest = client.post('/api/identity/guest', json={'brand_id': 'enbishan',
                        'display_name': 'Synthetic Contact', 'phone': '0500000001'})
    assert guest.status_code == 200, guest.text
    assert guest.json()['customer_verified'] is False
    csrf = guest.json()['csrf_token']
    captured = []
    public_user = auth.public_user
    def observe_policy(user, *args, **kwargs):
        captured.append(user.get('customer_phone_required'))
        return public_user(user, *args, **kwargs)
    monkeypatch.setattr(auth, 'public_user', observe_policy)
    session = client.get('/api/session', headers={'X-KPS-Context': 'customer'})
    assert session.status_code == 200, session.text
    assert captured == [True], 'Fresh SQL session policy must override the old false brand setting'
    body = {'brand_id': 'enbishan', 'branch_id': 'synthetic-branch', 'service': 'delivery',
            'payment_method': 'cash', 'items': [{'product_id': 'synthetic-product', 'quantity': 1}],
            'customer': {'name': 'Synthetic Contact', 'phone': '0500000001', 'neighborhood': 'Synthetic area',
                         'address': 'Synthetic address', 'location': {'lat': 28.4, 'lng': 45.9}}}
    response = client.post('/api/orders', json=body,
                           headers={'X-CSRF-Token': csrf, 'Idempotency-Key': 'synthetic-guest-key'})
    assert response.status_code == 403, response.text
    assert response.json()['detail']['code'] == 'CUSTOMER_PHONE_VERIFICATION_REQUIRED'
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM orders').fetchone()['n'] == 0


@pytest.mark.postgres
def test_policy_migration_changes_only_enbishan_checkout_setting(database):
    seed_enbishan()
    with db.tx() as conn:
        old_kps = conn.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings']
        before_channels = conn.execute('SELECT * FROM identity_channels ORDER BY brand_id,channel').fetchall()
        conn.execute((Path(__file__).resolve().parents[1] / 'migrations/126_enbishan_customer_sms_required.sql').read_text())
        assert conn.execute("SELECT settings FROM app_settings WHERE brand_id='enbishan'").fetchone()['settings'] == {
            'customer_phone_required': True, 'sentinel': 'preserve'}
        assert conn.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings'] == old_kps
        assert conn.execute('SELECT * FROM identity_channels ORDER BY brand_id,channel').fetchall() == before_channels
