"""Current checkout policy, separate from retained historical cash fixtures."""
from uuid import uuid4
import pytest
from cryptography.fernet import Fernet
from app import db, catalog, payment_gateways
from app.branch_payment_policy import required, blockers
from test_core import _branch, _body, _prepare_catalog, _make_user, _authenticate
from test_branch_payments import provider


def prepare(monkeypatch, seller='sami'):
    for key in ('KPS_BRANCH_PAYMENT_SANDBOX_ENABLED','KPS_BRANCH_PAYMENT_CHECKOUT_ENABLED',
                'KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED','KPS_BRANCH_PAYMENT_REFUNDS_ENABLED',
                'KPS_BRANCH_PAYMENT_RECONCILIATION_ENABLED'):
        monkeypatch.setenv(key, 'true')
    monkeypatch.setenv('RUNTIME_REVISION', 'a'*40)
    monkeypatch.setenv('KPS_BRANCH_PAYMENT_ACCEPTANCE_SHA', 'a'*40)
    monkeypatch.setenv('KPS_PAYMENT_CHECKOUT_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('PUBLIC_BASE_URL', 'https://fixture.invalid')
    prefix = payment_gateways.credential_prefix('moyasar', 'kps', seller)
    monkeypatch.setenv(prefix+'_TEST_SECRET_KEY', 'sk_test_synthetic_policy')
    monkeypatch.setenv(prefix+'_TEST_PUBLIC_KEY', 'pk_test_synthetic_policy')


def test_kps_new_branch_policy_is_off_without_both_gates(monkeypatch):
    branch = _branch(online_payment_seller='sami')
    assert required('kps', {}) is False
    assert required('kps', {'branch_online_required': True}) is True
    assert required('other-brand', {}) is False
    assert catalog.branch_service(branch, 'pickup', brand_settings={'services': {'pickup': False}})[1] == 'BRAND_SERVICE_DISABLED'
    settings = {'branch_online_required': True, 'services': {'pickup': True, 'car_pickup': True}}
    assert catalog.branch_service(branch, 'pickup', brand_settings=settings)[1] == 'BRANCH_PAYMENT_NOT_READY'
    prepare(monkeypatch)
    assert blockers(branch) == []
    assert catalog.branch_service(branch, 'pickup', brand_settings=settings)[0]
    branch['settings']['pickup_enabled'] = False
    assert catalog.branch_service(branch, 'pickup', brand_settings=settings)[1] == 'SERVICE_DISABLED'
    branch['settings']['pickup_enabled'] = True
    monkeypatch.setenv('KPS_BRANCH_PAYMENT_ACCEPTANCE_SHA', 'b'*40)
    assert 'exact_release_payment_acceptance_required' in blockers(branch)
    assert catalog.branch_service(branch, 'car', brand_settings=settings)[1] == 'BRANCH_PAYMENT_NOT_READY'


@pytest.mark.postgres
@pytest.mark.parametrize('service', ['pickup', 'car'])
def test_current_order_quote_accept_pay_and_handover(client, monkeypatch, service):
    _prepare_catalog()
    with db.tx() as c:
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'", (db.json({'branch_online_required': True, 'services': {'pickup': True, 'car_pickup': True, 'delivery': True}}),))
    body = _body(service)
    body['payment_method'] = 'card'
    body['customer']['phone'] = '+966500000002'
    # Branch is enabled but merchant selection and verified readiness are not.
    assert client.post('/api/quote', json=body).json()['detail']['code'] == 'BRANCH_PAYMENT_NOT_READY'
    customer = _make_user()
    owner = _make_user('owner', branches=['kps-branch-1'])
    _authenticate(client, owner)
    selected = client.patch('/api/admin/branches/kps-branch-1', json={'settings': {'online_payment_seller': 'sami'}, 'reason': 'Explicit synthetic merchant choice'}, headers={'Idempotency-Key': uuid4().hex})
    assert selected.status_code == 200, selected.text
    prepare(monkeypatch)
    quote = client.post('/api/quote', json=body)
    assert quote.status_code == 200, quote.text
    assert quote.json()['quote']['branch_payment_seller'] == 'sami'
    assert client.post('/api/quote', json=body | {'payment_method': 'cash'}).status_code == 422
    _authenticate(client, customer)
    created = client.post('/api/orders', json=body, headers={'Idempotency-Key': uuid4().hex})
    assert created.status_code == 200, created.text
    order = created.json()['order']; oid = order['id']
    assert order['snapshot']['branch_payment_mode'] == 'test'
    assert order['preparation_status'] == 'new' and order['payment_status'] == 'unpaid'
    assert client.post('/api/branch-payments/orders/'+oid+'/attempt', json={'version': order['version']}, headers={'Idempotency-Key': uuid4().hex}).status_code == 409
    _authenticate(client, owner)
    def transition(status, version):
        response = client.post('/api/orders/'+oid+'/preparation', json={'status': status, 'version': version}, headers={'Idempotency-Key': uuid4().hex})
        assert response.status_code == 200, response.text
        return response.json()['order']
    order = transition('accepted', order['version'])
    assert client.post('/api/orders/'+oid+'/preparation', json={'status': 'preparing', 'version': order['version']}, headers={'Idempotency-Key': uuid4().hex}).status_code == 409
    _authenticate(client, customer)
    attempt = client.post('/api/branch-payments/orders/'+oid+'/attempt', json={'version': order['version']}, headers={'Idempotency-Key': uuid4().hex})
    assert attempt.status_code == 200, attempt.text
    aid = attempt.json()['attempt']['id']
    provider(monkeypatch, aid)
    assert client.post('/api/branch-payments/attempts/'+aid+'/reconcile').status_code == 200
    # Closing the service blocks new orders, not paid orders already accepted.
    with db.tx() as c:
        c.execute("UPDATE branches SET settings=settings||'{\"pickup_enabled\":false,\"car_enabled\":false}'::jsonb WHERE id='kps-branch-1'")
        version = c.execute('SELECT version FROM orders WHERE id=%s', (oid,)).fetchone()['version']
    assert client.post('/api/quote', json=body).status_code == 409
    _authenticate(client, owner)
    for step in ('preparing','ready','completed'):
        order = transition(step, version); version = order['version']
    assert order['completed_at'] and order['payment_status'] == 'paid'
