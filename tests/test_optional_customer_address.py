"""Optional address text must never bypass delivery location or coverage guards."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from app import auth, catalog, customer_profile, db, orders, service_hours
from tests.test_core import _body, _make_user, _prepare_catalog


TEXT_CASES = [('', ''), ('حي اختبار', ''), ('', 'مدخل اختبار'), ('حي اختبار', 'مدخل اختبار')]
LOCATION = {'lat': 28.4, 'lng': 45.9}


@pytest.mark.parametrize('neighborhood,address', TEXT_CASES)
def test_checkout_models_allow_optional_text(neighborhood, address):
    payload = _body('delivery')
    payload['customer'].update(neighborhood=neighborhood, address=address)
    body = catalog.QuoteBody.model_validate(payload)
    assert body.customer.neighborhood == neighborhood
    assert body.customer.address == address
    assert body.customer.location.model_dump() == LOCATION
    saved = customer_profile.Address(label='المنزل', neighborhood=neighborhood,
                                     address=address, **LOCATION)
    assert saved.neighborhood == neighborhood and saved.address == address
    changed = orders.DeliveryLocationChange(neighborhood=neighborhood, address=address, location=LOCATION, expected_location=LOCATION, reason='اختبار تغيير الموقع')
    assert changed.neighborhood == neighborhood and changed.address == address


@pytest.mark.parametrize('values', [{}, {'address': None, 'neighborhood': None},
                                    {'address': '', 'neighborhood': ''}])
def test_missing_and_null_text_normalizes_without_fabricated_data(values):
    payload = _body('delivery')
    payload['customer'].pop('address')
    payload['customer'].pop('neighborhood')
    payload['customer'].update(values)
    body = catalog.QuoteBody.model_validate(payload)
    assert body.customer.address == body.customer.neighborhood == ''
    saved = customer_profile.Address(label='المنزل', **LOCATION, **values)
    assert saved.address == saved.neighborhood == ''


@pytest.fixture
def optional_address_customer(client, monkeypatch):
    _prepare_catalog()
    # Hold the isolated fixture in the evening; no morning policy is changed.
    monkeypatch.setattr(service_hours, 'now_riyadh',
                        lambda: datetime(2026, 10, 21, 20, tzinfo=catalog.RIYADH))
    user = _make_user()
    manager = _make_user('delivery_manager', branches=['kps-branch-1'])
    with db.tx() as conn:
        conn.execute("UPDATE app_settings SET settings=settings||'{\"customer_phone_required\":true}'::jsonb WHERE brand_id='kps'")
        conn.execute("UPDATE users SET phone='+966512345678',phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s", (user['id'],))
        user = conn.execute('SELECT * FROM users WHERE id=%s', (user['id'],)).fetchone()
        conn.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES(%s,'manager',%s,'kps',%s)",
                     (db.new_id('ms_'), manager['id'], db.json(['kps-branch-1'])))
    user.update(customer_verified_at=datetime.now(timezone.utc), customer_phone_required=True)
    # Exercise real routers/transactions with an explicit test-owned identity.
    # No SMS provider, production account, payment, or remote write is involved.
    client.app.dependency_overrides[auth.current_user] = lambda: user
    return client, user


@pytest.mark.postgres
@pytest.mark.parametrize('neighborhood,address', TEXT_CASES)
def test_quote_order_and_saved_address_keep_optional_text_and_exact_pin(optional_address_customer,
                                                                       neighborhood, address):
    client, user = optional_address_customer
    payload = _body('delivery')
    payload['customer'].update(neighborhood=neighborhood, address=address)
    quote = client.post('/api/quote', json=payload)
    assert quote.status_code == 200, quote.text
    created = client.post('/api/orders', json=payload, headers={'Idempotency-Key': uuid4().hex})
    assert created.status_code == 200, created.text
    order = created.json()['order']
    assert order['customer']['neighborhood'] == neighborhood
    assert order['customer']['address'] == address
    assert order['customer']['location'] == LOCATION
    with db.tx() as conn:
        stored = conn.execute('SELECT customer FROM orders WHERE id=%s', (order['id'],)).fetchone()['customer']
        assert stored['neighborhood'] == neighborhood and stored['address'] == address
        assert stored['location'] == LOCATION
        saved = conn.execute('SELECT address,neighborhood,lat,lng FROM customer_addresses WHERE customer_id=%s',
                             (user['id'],)).fetchone()
        assert saved['address'] == address and saved['neighborhood'] == neighborhood
        assert {'lat': saved['lat'], 'lng': saved['lng']} == LOCATION
    fetched = client.get('/api/orders/' + order['id'])
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()['order']['customer']['location'] == LOCATION


@pytest.mark.postgres
@pytest.mark.parametrize('neighborhood,address', TEXT_CASES)
def test_address_add_and_edit_allow_blank_text(optional_address_customer, neighborhood, address):
    client, _ = optional_address_customer
    body = dict(label='المنزل', neighborhood=neighborhood, address=address, **LOCATION)
    created = client.post('/api/customer/addresses', json=body, headers={'Idempotency-Key': uuid4().hex})
    assert created.status_code == 200, created.text
    saved = created.json()['address']
    assert saved['neighborhood'] == neighborhood and saved['address'] == address
    # Editing an existing populated address down to empty is supported too.
    body.update(neighborhood='', address='')
    changed = client.patch('/api/customer/addresses/' + saved['id'], json=body,
                           headers={'Idempotency-Key': uuid4().hex})
    assert changed.status_code == 200, changed.text
    updated = changed.json()['address']
    assert updated['neighborhood'] == updated['address'] == ''
    assert {'lat': updated['lat'], 'lng': updated['lng']} == LOCATION


@pytest.mark.postgres
@pytest.mark.parametrize('location,status,code', [
    (None, 422, 'DELIVERY_ADDRESS_REQUIRED'),
    ({'lat': 28.8, 'lng': 45.9}, 409, 'OUTSIDE_COVERAGE'),
])
def test_blank_text_never_bypasses_required_location_or_coverage(optional_address_customer, location, status, code):
    client, _ = optional_address_customer
    body = _body('delivery')
    body['customer'].update(neighborhood='', address='', location=location)
    for path in ('/api/quote', '/api/orders'):
        response = client.post(path, json=body, headers={'Idempotency-Key': uuid4().hex})
        assert response.status_code == status, response.text
        assert response.json()['detail']['code'] == code
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) AS n FROM orders').fetchone()['n'] == 0
