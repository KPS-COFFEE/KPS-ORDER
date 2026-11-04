"""Owner closure acceptance in isolated PostgreSQL; all accounts/orders are synthetic."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest

from app import db, catalog
from fastapi import HTTPException
from test_admin import owner
from test_launch_admin import _administrator, _login

ROOT = Path(__file__).resolve().parents[1]


def test_nearest_and_server_pricing_share_independent_branch_option_stock_policy():
    from test_launch_branches import branch
    product = {'id': 'test-option-stock', 'brand_id': 'launch-branches', 'name': 'TEST ONLY mug',
               'category': 'TEST', 'price_halalas': 1500, 'settings': {'source_verified': True,
               'option_groups': [{'code': 'COLOR', 'name': 'اللون', 'required': True, 'values': [
                   {'code': 'BLACK', 'name': 'أسود', 'price_delta_halalas': 100,
                    'unavailable_branch_ids': ['launch-branch-1']},
                   {'code': 'BLUE', 'name': 'أزرق', 'price_delta_halalas': 0}]}]}}
    rows = [branch(1, 28.0), branch(9, 28.1)]
    selected = {'test-option-stock': [{'options': {'COLOR': 'BLACK'}}]}
    ranked = catalog.rank_nearest_branches(rows, {'lat': 28, 'lng': 45}, 'pickup', {}, [product], {product['id']: product}, selected)
    assert [row['branch_no'] for row in ranked['branches']] == [9]
    assert ranked['excluded'][0]['unavailable_products'][0]['code'] == 'PRODUCT_VARIANT_UNAVAILABLE'
    item = catalog.OrderItem.model_validate({'product_id': product['id'], 'quantity': 1,
                                             'units': [{'options': {'COLOR': 'BLACK'}}]})
    with pytest.raises(HTTPException) as rejected:
        catalog._price_units(product, item, rows[0])
    assert rejected.value.status_code == 409
    amount, units = catalog._price_units(product, item, rows[1])
    assert amount == 1600 and units[0]['option_snapshot']['COLOR']['choices'][0]['name'] == 'أسود'
    assert catalog.product_availability(product, rows[0])[0] is True  # Blue remains available.


def _ready_branches(mutate):
    for number in (1, 9):
        result = mutate('PATCH', f'/api/admin/branches/kps-branch-{number}', {'settings': {
            'active': True, 'ordering_verified': True, 'pickup_enabled': True,
            'opening_hours': [{'days': list(range(7)), 'start': '00:00', 'end': '00:00'}],
            'payment_methods': ['cash']}})
        assert result.status_code == 200, result.text


def _body(color='BLK', number=1, product='kps-30'):
    return {'brand_id': 'kps', 'branch_id': f'kps-branch-{number}', 'service': 'pickup',
            'payment_method': 'cash', 'customer': {'name': 'اختبار إقفال المالك فقط', 'phone': '0500000021'},
            'items': [{'product_id': product, 'quantity': 1, 'units': [{'options': {'COLOR': color}}]}]}


def _product(client, identifier='kps-30'):
    return next(p for p in client.get('/api/admin/config').json()['products'] if p['id'] == identifier)


def _restore(client, cookies, csrf):
    client.cookies.clear()
    client.cookies.update(cookies)
    client.headers['X-CSRF-Token'] = csrf


@pytest.mark.postgres
def test_independent_color_stop_global_and_branch_rechecks_order_and_preserves_history(owner):
    client, mutate = owner
    _ready_branches(mutate)
    previous = _product(client)
    assert len(client.get('/api/catalog').json()['products']) == 31
    owner_cookies, owner_csrf = dict(client.cookies), client.headers['X-CSRF-Token']
    client.cookies.clear()
    client.headers.pop('X-CSRF-Token', None)
    guest = client.post('/api/identity/guest', json={'brand_id': 'kps', 'display_name': 'اختبار فقط', 'phone': '0500000021'})
    assert guest.status_code == 200, guest.text
    client.headers['X-CSRF-Token'] = guest.json()['csrf_token']
    guest_cookies, guest_csrf = dict(client.cookies), client.headers['X-CSRF-Token']
    created = mutate('POST', '/api/orders', _body())
    assert created.status_code == 200, created.text
    historical = created.json()['order']
    assert 'channel_commercial' not in historical['snapshot']
    with db.tx() as conn:
        original_internal_snapshot = conn.execute('SELECT snapshot FROM orders WHERE id=%s', (historical['id'],)).fetchone()['snapshot']
    assert 'channel_commercial' not in original_internal_snapshot
    assert original_internal_snapshot['receipt_profile']['entity_id'] is None
    assert original_internal_snapshot['receipt_profile']['issuance_status'] == 'seller_decision_pending'
    assert original_internal_snapshot['items'] == historical['snapshot']['items']
    _restore(client, owner_cookies, owner_csrf)

    groups = deepcopy(previous['settings']['option_groups'])
    black = next(value for value in groups[0]['values'] if value['code'] == 'BLK')
    black['enabled'] = False
    updated = mutate('PATCH', '/api/admin/products/kps-30', {'settings': {'option_groups': groups}})
    assert updated.status_code == 200, updated.text
    # Operational availability preserves the existing approved price and other colors.
    public = next(p for p in client.get('/api/catalog?branch_id=kps-branch-1').json()['products'] if p['id'] == 'kps-30')
    assert public['available'] and public['enabled'] and public['source_verified']
    assert public['price_halalas'] == previous['price_halalas']
    values = {v['code']: v for v in public['option_groups'][0]['values']}
    assert values['BLK']['available'] is False and values['BLU']['available'] is True
    assert client.post('/api/quote', json=_body()).json()['detail']['code'] == 'OPTION_VALUE_INVALID'
    assert client.post('/api/quote', json=_body('BLU')).status_code == 200

    black.update(enabled=True, unavailable_branch_ids=['kps-branch-1'])
    assert mutate('PATCH', '/api/admin/products/kps-30', {'settings': {'option_groups': groups}}).status_code == 200
    blocked = client.post('/api/quote', json=_body())
    assert blocked.status_code == 409 and blocked.json()['detail']['code'] == 'PRODUCT_VARIANT_UNAVAILABLE'
    assert client.post('/api/quote', json=_body(number=9)).status_code == 200
    assert client.post('/api/quote', json=_body('BLU')).status_code == 200
    _restore(client, guest_cookies, guest_csrf)
    stale_confirmation = mutate('POST', '/api/orders', _body())
    assert stale_confirmation.status_code == 409
    _restore(client, owner_cookies, owner_csrf)

    with db.tx() as conn:
        saved = conn.execute('SELECT * FROM orders WHERE id=%s', (historical['id'],)).fetchone()
        assert saved['snapshot'] == original_internal_snapshot
        assert saved['total_halalas'] == historical['total_halalas']
        assert saved['snapshot']['items'][0]['units'][0]['option_snapshot']['COLOR']['choices'][0]['code'] == 'BLK'
        assert conn.execute('SELECT count(*) n FROM orders').fetchone()['n'] == 1
    db.migrate()
    assert len(client.get('/api/catalog').json()['products']) == 31
    restarted = _product(client)
    assert restarted['settings']['option_groups'][0]['values'] == groups[0]['values']
    assert client.post('/api/quote', json=_body()).status_code == 409


@pytest.mark.postgres
def test_new_item_original_photo_draft_scope_publish_pause_resume(owner):
    client, mutate = owner
    source = ROOT / 'public/assets/kps-30-8037793c7cf2.jpg'
    uploaded = mutate('POST', '/api/admin/assets?purpose=product', files={'file': ('preserved-menu-image.jpg', source.read_bytes(), 'image/jpeg')})
    assert uploaded.status_code == 200, uploaded.text
    asset = uploaded.json()['id']
    assert client.get(uploaded.json()['preview_url']).status_code == 200
    created = mutate('POST', '/api/admin/products', {
        'name': 'صنف اختبار واضح', 'category': 'TEST ONLY', 'price_halalas': 1250,
        'settings': {'description': 'وصف تجريبي محفوظ', 'image_asset_id': asset,
                     'available_branch_ids': ['kps-branch-1'], 'option_groups': [
                         {'code': 'SIZE', 'name': 'المقاس', 'required': True, 'values': [
                             {'code': 'S', 'name': 'صغير', 'price_delta_halalas': 0},
                             {'code': 'L', 'name': 'كبير', 'price_delta_halalas': 250}]}]}})
    assert created.status_code == 200, created.text
    item = created.json()['product']
    assert item['settings']['lifecycle'] == 'draft' and item['settings']['enabled'] is False
    assert item['id'] not in {p['id'] for p in client.get('/api/catalog').json()['products']}
    assert client.get(item['settings']['image_url']).status_code == 404
    assert mutate('PATCH', '/api/admin/products/' + item['id'], {'settings': {'lifecycle': 'active'}}).status_code == 422
    published = mutate('PATCH', '/api/admin/products/' + item['id'], {'settings': {'lifecycle': 'active', 'source_verified': True}})
    assert published.status_code == 200, published.text
    assert client.get(item['settings']['image_url']).status_code == 200
    assert client.get(f"/api/products/{item['id']}/availability?branch_id=kps-branch-1").json()['available']
    assert not client.get(f"/api/products/{item['id']}/availability?branch_id=kps-branch-9").json()['available']
    assert mutate('PATCH', '/api/admin/products/' + item['id'], {'settings': {'lifecycle': 'paused'}}).status_code == 200
    assert client.get(item['settings']['image_url']).status_code == 404
    assert mutate('PATCH', '/api/admin/products/' + item['id'], {'settings': {'lifecycle': 'active', 'available_branch_ids': None}}).status_code == 200
    assert client.get(f"/api/products/{item['id']}/availability?branch_id=kps-branch-9").json()['available']
    db.migrate()
    saved = _product(client, item['id'])
    assert saved['settings']['description'] == 'وصف تجريبي محفوظ'
    assert saved['settings']['image_asset_id'] == asset and saved['settings']['option_groups'][0]['values'][1]['price_delta_halalas'] == 250


@pytest.mark.postgres
def test_variant_branch_scope_rejects_other_brand_and_malformed_scope(owner):
    client, mutate = owner
    groups = deepcopy(_product(client)['settings']['option_groups'])
    with db.tx() as conn:
        conn.execute("INSERT INTO brands(id,name) VALUES('owner-closure-other','Other fixture')")
        conn.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city) VALUES('owner-closure-other-branch',1,'other-fixture','owner-closure-other','Other','Other')")
    for scope in (['owner-closure-other-branch'], ['unknown'], ['kps-branch-1', 'kps-branch-1'], 'kps-branch-1', [True]):
        groups[0]['values'][0]['unavailable_branch_ids'] = scope
        rejected = mutate('PATCH', '/api/admin/products/kps-30', {'settings': {'option_groups': groups}})
        assert rejected.status_code == 422, rejected.text
    assert 'unavailable_branch_ids' not in _product(client)['settings']['option_groups'][0]['values'][0]


@pytest.mark.postgres
def test_delegated_product_admin_cannot_edit_global_or_expand_beyond_authorized_branches(owner, monkeypatch):
    client, mutate = owner
    identifier = _administrator(['products_manage'])
    _login(client, identifier, monkeypatch)
    assert client.get('/api/admin/config').json()['products'] == []
    assert mutate('PATCH', '/api/admin/products/kps-30', {'price_halalas': 1}).status_code == 403
    for scope in (None, ['kps-branch-9'], ['kps-branch-1', 'kps-branch-9']):
        denied = mutate('POST', '/api/admin/products', {'name': 'TEST', 'category': 'TEST', 'price_halalas': 100,
                         'settings': {'available_branch_ids': scope}})
        assert denied.status_code == 403, denied.text
    created = mutate('POST', '/api/admin/products', {'name': 'TEST', 'category': 'TEST', 'price_halalas': 100,
                     'settings': {'available_branch_ids': ['kps-branch-1']}})
    assert created.status_code == 200, created.text
    item = created.json()['product']['id']
    assert [p['id'] for p in client.get('/api/admin/config').json()['products']] == [item]
    assert mutate('PATCH', '/api/admin/products/' + item, {'name': 'TEST scoped edit'}).status_code == 200
    assert mutate('PATCH', '/api/admin/products/' + item, {'settings': {'available_branch_ids': None}}).status_code == 403


@pytest.mark.postgres
def test_legacy_admin_account_route_revokes_email_code_and_blocks_unverified_reactivation(client, monkeypatch):
    from test_final_identity import _staff, _request, _code, _verify
    from test_v5_identity import _legacy_mail_setup
    # This retained email-code contract belongs to the synthetic non-SMS tenant.
    # KPS keeps mandatory SMS; exercise real authentication and admin scope here.
    _legacy_mail_setup(monkeypatch)
    principal = _staff(role='owner', phone='+966501111222', email='owner@fixture.invalid')
    login = client.post('/api/auth/login', json={'username':'fixture-legacy-owner',
                                               'password':'Preserved!OwnerPassword1'})
    assert login.status_code == 200, login.text
    assert login.json()['user']['id'] == principal['id']
    client.headers['X-CSRF-Token'] = login.json()['csrf_token']
    client.headers['X-KPS-Context'] = 'work'
    def mutate(method, path, body):
        return client.request(method, path, json=body, headers={'Idempotency-Key':uuid4().hex})
    staff = _staff()
    challenge = _request(client)
    code = _code(challenge)
    paused = mutate('PATCH', '/api/admin/users/' + staff['id'], {'active': False})
    assert paused.status_code == 200, paused.text
    with db.tx() as conn:
        assert conn.execute('SELECT revoked_at FROM identity_tokens WHERE id=%s', (challenge,)).fetchone()['revoked_at']
        assert conn.execute('SELECT status FROM email_outbox WHERE dedupe_key=%s', ('identity:' + challenge,)).fetchone()['status'] == 'cancelled'
    assert _verify(client, challenge, code).status_code == 400
    assert mutate('PATCH', '/api/admin/users/' + staff['id'], {'active': True}).status_code == 200
    with db.tx() as conn:
        conn.execute("UPDATE users SET active=false,email_verified_at=NULL,identity_kind='pending_email',invitation_status='revoked' WHERE id=%s", (staff['id'],))
    blocked = mutate('PATCH', '/api/admin/users/' + staff['id'], {'active': True})
    assert blocked.status_code == 403 and blocked.json()['detail']['code'] == 'STAFF_EMAIL_VERIFICATION_REQUIRED'
