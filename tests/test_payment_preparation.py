"""Disabled payment preparation and synthetic provider evidence only."""
from copy import deepcopy
from dataclasses import asdict
import json

from fastapi import HTTPException
import httpx
from pydantic import ValidationError
import pytest

from app import db, invoice_profiles, online_payments as setup, payment_gateways as gateways
from tests.test_core import _authenticate, _body, _create, _make_user, _prepare_catalog

PAYMENT_ID = '0e988881-245c-4612-b868-b1ef13216089'


@pytest.fixture
def expectation():
    return gateways.PaymentContext('kps', 'reem', 'order_fixture', 'attempt_fixture', 2500)


def paid_payment(context):
    return {'id': PAYMENT_ID, 'status': 'paid', 'amount': 2500, 'currency': 'SAR',
            'refunded': 0, 'metadata': context.metadata,
            'source': {'token': 'private-provider-token', 'number': 'masked-card'}}


def sandbox(monkeypatch, context, response):
    prefix = gateways.credential_prefix('moyasar', context.brand_id, context.entity_id)
    monkeypatch.setenv(prefix + '_TEST_SECRET_KEY', 'sk_test_synthetic_never_sent')
    monkeypatch.setenv('KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED', 'true')
    original = httpx.Client
    calls = []
    def handle(request):
        calls.append(request)
        return response
    monkeypatch.setattr(gateways.httpx, 'Client',
                        lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    return calls


def test_credentials_alone_never_enable_provider_or_customer_payments(monkeypatch, expectation):
    prefix = gateways.credential_prefix('moyasar', 'kps', 'reem')
    monkeypatch.setenv(prefix + '_TEST_SECRET_KEY', 'sk_test_synthetic_never_sent')
    monkeypatch.setenv(prefix + '_LIVE_SECRET_KEY', 'sk_live_synthetic_never_sent')
    monkeypatch.setattr(gateways.httpx, 'Client', lambda **kw: pytest.fail('Unexpected provider request'))
    with pytest.raises(HTTPException) as caught:
        gateways.adapter_for('moyasar').verify_sandbox_payment(expectation, PAYMENT_ID)
    assert caught.value.detail['code'] == 'PAYMENT_SANDBOX_DISABLED'
    profiles = {'entities': {'reem': {'legal_name': 'Fixture seller'}}, 'assignments': {'one': 'reem'}}
    view = setup._view('kps', profiles, {'enabled': True, 'accounts': {'reem': {'provider': 'moyasar', 'enabled': True}}})
    assert view['enabled'] is False and view['live_payments_available'] is False
    assert view['customer_payment_methods'] == [] and view['accounts'][0]['enabled'] is False
    assert view['accounts'][0]['sandbox_credentials_configured'] is True
    assert 'sk_test' not in json.dumps(view) and 'sk_live' not in json.dumps(view)


def test_read_only_provider_verification_and_private_response_projection(monkeypatch, expectation):
    calls = sandbox(monkeypatch, expectation, httpx.Response(200, json=paid_payment(expectation)))
    result = gateways.adapter_for('moyasar').verify_sandbox_payment(expectation, PAYMENT_ID)
    assert len(calls) == 1 and calls[0].method == 'GET'
    assert str(calls[0].url) == 'https://api.moyasar.com/v1/payments/' + PAYMENT_ID
    assert calls[0].headers['authorization'].startswith('Basic ')
    assert result.mode == 'test' and result.context == expectation
    serialized = json.dumps(asdict(result))
    assert 'private-provider-token' not in serialized and 'masked-card' not in serialized


@pytest.mark.parametrize('change', [
    {'id': 'another-payment'}, {'status': 'authorized'}, {'status': 'failed'},
    {'status': 'refunded'}, {'amount': 2499}, {'amount': True}, {'amount': '2500'},
    {'currency': 'USD'}, {'refunded': 1}, {'refunded': False}, {'refunded': None},
    {'live': True}, {'live': 'false'}, {'metadata': {}}, {'metadata': None},
    {'metadata': {'brand_id': 'other', 'entity_id': 'reem', 'order_id': 'order_fixture', 'attempt_id': 'attempt_fixture'}},
    {'metadata': {'brand_id': 'kps', 'entity_id': 'sami', 'order_id': 'order_fixture', 'attempt_id': 'attempt_fixture'}},
    {'metadata': {'brand_id': 'kps', 'entity_id': 'reem', 'order_id': 'another_order', 'attempt_id': 'attempt_fixture'}},
    {'metadata': {'brand_id': 'kps', 'entity_id': 'reem', 'order_id': 'order_fixture', 'attempt_id': 'other_attempt'}},
])
def test_wrong_seller_order_attempt_amount_mode_or_status_never_proves_payment(expectation, change):
    with pytest.raises(HTTPException) as caught:
        gateways.adapter_for('moyasar').validate(paid_payment(expectation) | change, PAYMENT_ID, expectation)
    assert caught.value.detail['code'] == 'PAYMENT_EVIDENCE_MISMATCH'


@pytest.mark.parametrize('status', [301, 401, 403, 404, 429, 500])
def test_provider_failure_is_sanitized_and_not_retried(monkeypatch, expectation, status):
    calls = sandbox(monkeypatch, expectation, httpx.Response(status, json={'secret': 'DO_NOT_EXPOSE'}))
    with pytest.raises(HTTPException) as caught:
        gateways.adapter_for('moyasar').verify_sandbox_payment(expectation, PAYMENT_ID)
    assert caught.value.detail['code'] == 'PAYMENT_PROVIDER_UNAVAILABLE'
    assert 'DO_NOT_EXPOSE' not in str(caught.value.detail) and len(calls) == 1


def test_malformed_provider_json_is_not_success(monkeypatch, expectation):
    sandbox(monkeypatch, expectation, httpx.Response(200, content=b'not-json'))
    with pytest.raises(HTTPException) as caught:
        gateways.adapter_for('moyasar').verify_sandbox_payment(expectation, PAYMENT_ID)
    assert caught.value.detail['code'] == 'PAYMENT_PROVIDER_UNAVAILABLE'


@pytest.mark.parametrize('key', ['sk_live_synthetic_secret', 'pk_test_synthetic_key', ''])
def test_live_or_public_key_cannot_make_sandbox_requests(monkeypatch, expectation, key):
    monkeypatch.setenv('KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED', 'true')
    monkeypatch.setenv(gateways.credential_prefix('moyasar', 'kps', 'reem') + '_TEST_SECRET_KEY', key)
    monkeypatch.setattr(gateways.httpx, 'Client', lambda **kw: pytest.fail('Unexpected provider request'))
    with pytest.raises(HTTPException) as caught:
        gateways.adapter_for('moyasar').verify_sandbox_payment(expectation, PAYMENT_ID)
    assert caught.value.detail['code'] == 'PAYMENT_TEST_CREDENTIALS_REQUIRED'


@pytest.mark.parametrize('reference', ['../payments', 'https://example.invalid/secret', '', None])
def test_provider_reference_cannot_change_network_destination(monkeypatch, expectation, reference):
    sandbox(monkeypatch, expectation, httpx.Response(200, json={}))
    with pytest.raises(HTTPException) as caught:
        gateways.adapter_for('moyasar').verify_sandbox_payment(expectation, reference)
    assert caught.value.detail['code'] == 'PAYMENT_REFERENCE_INVALID'


def test_credential_slots_are_isolated_and_replay_context_is_stable(expectation):
    scopes = [('moyasar', 'kps', 'reem'), ('moyasar', 'kps', 'sami'),
              ('moyasar', 'other', 'reem'), ('provider-a', 'kps', 'reem'),
              ('provider_a', 'kps', 'reem'), ('moyasar', 'kps-a', 'reem'),
              ('moyasar', 'kps_a', 'reem')]
    assert len({gateways.credential_prefix(*s) for s in scopes}) == len(scopes)
    assert expectation.idempotency_reference == gateways.PaymentContext(**asdict(expectation)).idempotency_reference
    changed = gateways.PaymentContext(**(asdict(expectation) | {'amount_halalas': 2600}))
    assert expectation.idempotency_reference != changed.idempotency_reference


@pytest.mark.parametrize('change', [{'amount_halalas': True}, {'amount_halalas': 99},
                                  {'amount_halalas': 1_000_000_001}, {'currency': 'USD'},
                                  {'brand_id': '../other'}, {'attempt_id': ''}])
def test_invalid_server_context_is_rejected(expectation, change):
    with pytest.raises(ValueError):
        gateways.PaymentContext(**(asdict(expectation) | change))


def test_new_provider_uses_registry_without_changing_order_contract(monkeypatch):
    monkeypatch.setattr(gateways, '_ADAPTERS', gateways._ADAPTERS.copy())
    class FixtureAdapter:
        provider_id = 'fixture_provider'
        def verify_sandbox_payment(self, context, payment_id):
            return gateways.VerifiedPayment(self.provider_id, payment_id, context, 'test')
    adapter = FixtureAdapter()
    gateways.register_adapter(adapter)
    assert gateways.adapter_for('fixture_provider') is adapter
    with pytest.raises(ValueError):
        gateways.register_adapter(adapter)
    with pytest.raises(HTTPException) as caught:
        gateways.adapter_for('not_integrated')
    assert caught.value.detail['code'] == 'PAYMENT_ADAPTER_REQUIRED'


@pytest.mark.parametrize('account', [{'provider': 'https://example.invalid'}, {'provider': 'sk_live_secret'},
                                     {'provider': 'moyasar', 'enabled': True},
                                     {'provider': 'moyasar', 'secret_key': 'DO_NOT_STORE'}])
def test_browser_cannot_supply_secrets_endpoints_or_activation(account):
    with pytest.raises(ValidationError):
        setup.PlannedAccount(**account)


@pytest.mark.postgres
def test_owner_saves_any_provider_only_as_a_disabled_plan(client):
    _authenticate(client, _make_user('owner'))
    before = client.get('/api/admin/online-payments').json()
    assert {a['entity_id'] for a in before['accounts']} == {'reem', 'sami'}
    with db.tx() as conn:
        branches = conn.execute('SELECT id,settings FROM branches ORDER BY id').fetchall()
        collections = conn.execute('SELECT count(*) AS n FROM collections').fetchone()['n']
    body = {'accounts': {'reem': {'provider': 'paytabs'}, 'sami': {'provider': 'another_gateway'}},
            'revision': before['revision'], 'reason': 'Synthetic disabled provider preparation'}
    headers = {'Idempotency-Key': 'prepare-provider-once'}
    first = client.put('/api/admin/online-payments', json=body, headers=headers)
    assert first.status_code == 200, first.text
    assert client.put('/api/admin/online-payments', json=body, headers=headers).json() == first.json()
    result = first.json()
    assert not result['enabled'] and not result['live_payments_available']
    assert result['customer_payment_methods'] == []
    assert all(not a['enabled'] and a['adapter_state'] == 'adapter_required' for a in result['accounts'])
    with db.tx() as conn:
        assert conn.execute('SELECT id,settings FROM branches ORDER BY id').fetchall() == branches
        assert conn.execute('SELECT count(*) AS n FROM collections').fetchone()['n'] == collections
        assert conn.execute("SELECT count(*) AS n FROM audit_events WHERE action='payment.preparation.updated'").fetchone()['n'] == 1
    # Both defaults and non-integrated providers remain ordinary receipt payment only.
    _prepare_catalog()
    _authenticate(client, _make_user())
    order = _create(client)
    assert order.status_code == 200, order.text
    assert order.json()['order']['payment_status'] == 'unpaid'
    for method in ['online', 'moyasar', 'paytabs', 'another_gateway']:
        body = _body() | {'payment_method': method}
        assert client.post('/api/orders', json=body, headers={'Idempotency-Key': 'reject-'+method}).status_code == 422


@pytest.mark.postgres
@pytest.mark.parametrize('role', ['customer', 'driver', 'delivery_manager', 'delivery_staff', 'branch_manager', 'admin'])
def test_preparation_is_owner_only(client, role):
    _authenticate(client, _make_user(role, branches=['kps-branch-1']))
    # A customer credential cannot authenticate this work route. Authenticated
    # work roles are denied authorization; neither may view or mutate the plan.
    expected = 401 if role == 'customer' else 403
    assert client.get('/api/admin/online-payments').status_code == expected
    assert client.put('/api/admin/online-payments', json={}).status_code == expected


@pytest.mark.postgres
def test_stale_seller_mapping_cross_brand_and_browser_activation_are_rejected(client):
    _authenticate(client, _make_user('owner'))
    initial = client.get('/api/admin/online-payments').json()
    body = {'accounts': {'foreign_entity': {'provider': 'moyasar'}},
            'revision': initial['revision'], 'reason': 'Synthetic scope rejection'}
    result = client.put('/api/admin/online-payments', json=body, headers={'Idempotency-Key': 'reject-foreign'})
    assert result.status_code == 422 and result.json()['detail']['code'] == 'PAYMENT_SELLER_INVALID'
    for extra in [{'enabled': True}, {'secret_key': 'SECRET'}, {'brand_id': 'other'}]:
        assert client.put('/api/admin/online-payments', json=body | extra,
                          headers={'Idempotency-Key': 'reject-extra'}).status_code == 422
    body['accounts'] = {'reem': {'provider': 'moyasar'}}
    with db.tx() as conn:
        profiles = deepcopy(invoice_profiles.defaults('kps'))
        profiles['assignments']['kps-branch-1'] = 'sami'
        conn.execute("UPDATE app_settings SET settings=settings||%s WHERE brand_id='kps'",
                     (db.json({'invoice_profiles': profiles}),))
    result = client.put('/api/admin/online-payments', json=body,
                        headers={'Idempotency-Key': 'reject-stale'})
    assert result.status_code == 409 and result.json()['detail']['code'] == 'PAYMENT_PREPARATION_CHANGED'


@pytest.mark.postgres
def test_preparation_keeps_current_csrf_and_idempotency_guards(client):
    _authenticate(client, _make_user('owner'))
    before = client.get('/api/admin/online-payments').json()
    body = {'accounts': {}, 'revision': before['revision'], 'reason': 'Synthetic guard test'}
    assert client.put('/api/admin/online-payments', json=body).status_code == 400
    client.headers.pop('X-CSRF-Token', None)
    assert client.put('/api/admin/online-payments', json=body,
                      headers={'Idempotency-Key': 'csrf-rejection'}).status_code == 403


@pytest.mark.postgres
def test_plans_and_seller_credentials_do_not_cross_brands(client, monkeypatch):
    prefix = gateways.credential_prefix('moyasar', 'kps', 'reem')
    monkeypatch.setenv(prefix + '_TEST_SECRET_KEY', 'sk_test_synthetic_never_sent')
    _authenticate(client, _make_user('owner'))
    before = client.get('/api/admin/online-payments').json()
    result = client.put('/api/admin/online-payments',
                        json={'accounts': {'reem': {'provider': 'moyasar'}},
                              'revision': before['revision'], 'reason': 'KPS-only synthetic plan'},
                        headers={'Idempotency-Key': 'kps-private-plan'})
    assert result.status_code == 200, result.text
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('other_brand','Synthetic brand')")
        conn.execute("INSERT INTO app_settings(brand_id,settings) VALUES('other_brand',%s)",
                     (db.json({'invoice_profiles': {'entities': {'reem': {'legal_name': 'Other seller'}},
                                                    'assignments': {}}}),))
    _authenticate(client, _make_user('owner', brand='other_brand'))
    other = client.get('/api/admin/online-payments').json()
    assert other['accounts'][0]['provider'] is None
    assert not other['accounts'][0]['sandbox_credentials_configured']
    foreign = client.put('/api/admin/online-payments',
                         json={'accounts': {'reem': {'provider': 'tap'}},
                               'revision': result.json()['revision'], 'reason': 'Reject foreign revision'},
                         headers={'Idempotency-Key': 'other-private-plan'})
    assert foreign.status_code == 409
