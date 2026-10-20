"""Owner decisions exercised together on disposable PostgreSQL records only."""
from copy import deepcopy

import pytest

from app import db
from app.enbishan_operations_preparation import apply_isolated
from tests.test_core import _branch
from tests.test_enbishan_dual_brand import dual
from tests.test_enbishan_owner_menu_decisions import reviewed
from tools.enbishan_workbook import reference_menu


@pytest.mark.postgres
def test_approved_menu_and_four_branch_owner_journey(dual):
    client, actor, post, _, _ = dual
    branch_ids = [f'enbishan-test-{number}' for number in range(1, 5)]
    # Explicit synthetic source values. No operational database or real provider.
    source_settings = _branch(minimum_order_halalas=1000)['settings']
    destination = {key: deepcopy(value) for key, value in source_settings.items()
                   if key not in {'opening_hours', 'delivery_hours',
                                  'delivery_fee_halalas', 'minimum_order_halalas'}}
    destination.update(delivery_enabled=False, pickup_enabled=False, car_enabled=False)
    with db.tx() as conn:
        conn.execute('UPDATE branches SET settings=%s WHERE id=%s',
                     (db.json(source_settings), 'kps-branch-1'))
        conn.execute("UPDATE branches SET settings=%s WHERE brand_id='enbishan'",
                     (db.json(destination),))
        for number, city in [(3, 'الدمام'), (4, 'القيصومة')]:
            conn.execute('INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) '
                         "VALUES(%s,%s,%s,'enbishan','SYNTHETIC TEST ONLY',%s,%s)",
                         (f'enbishan-test-{number}', number, f'test-{number}', city, db.json(destination)))
        conn.execute("UPDATE staff_brand_memberships SET branch_ids=%s WHERE user_id='dual-owner' AND brand_id='enbishan'",
                     (db.json(branch_ids),))
        patches = apply_isolated(conn, 'dual-owner', ['kps-branch-1'], branch_ids)
        assert len(patches) == 4
        prepared = conn.execute("SELECT settings FROM branches WHERE brand_id='enbishan'").fetchall()
        assert len(prepared) == 4
        assert all(row['settings']['delivery_fee_halalas'] == 500
                   and row['settings']['minimum_order_halalas'] == 1000
                   and row['settings']['delivery_enabled'] is False
                   and row['settings']['pickup_enabled'] is False for row in prepared)
        assert conn.execute("SELECT settings FROM branches WHERE id='kps-branch-1'").fetchone()['settings'] == source_settings

    # Existing shared owner API operates on the explicitly selected brand only.
    post('owner', '/api/admin/branches/enbishan-test-3',
         {'settings': {'delivery_enabled': True, 'delivery_fee_halalas': 700},
          'reason': 'SYNTHETIC local acceptance only'}, brand='enbishan', method='patch')
    post('owner', '/api/admin/branches/kps-branch-1',
         {'settings': {'delivery_fee_halalas': 1}, 'reason': 'SYNTHETIC cross-brand rejection'},
         brand='enbishan', method='patch', status=404)

    approved = reference_menu(reviewed(), owner_approved=True)
    body = {'source_sha256': approved['source_sha256'], 'rows': approved['rows']}
    preview = post('owner', '/api/admin/enbishan-menu/preview', body, brand='enbishan')
    result = post('owner', '/api/admin/enbishan-menu/apply',
                  {**body, 'reviewed_digest': preview['catalog_digest'],
                   'expected_catalog_digest': preview['expected_catalog_digest']}, brand='enbishan')
    assert result['imported_drafts'] == 31 and result['ordering_enabled'] is False
    product = next(p for p in preview['products'] if p['settings']['source_id'].endswith('-choose3'))
    post('owner', '/api/admin/products/' + product['id'],
         {'settings': {'source_verified': True, 'lifecycle': 'active'}}, brand='enbishan', method='patch')
    selected = deepcopy(product['settings']['variant_price_matrix'][0]['options'])
    quote_body = {'brand_id': 'enbishan', 'branch_id': 'enbishan-test-3',
                  'service': 'delivery', 'payment_method': 'cash',
                  'items': [{'product_id': product['id'], 'quantity': 1, 'units': [{'options': selected}]}],
                  'customer': {'name': 'SYNTHETIC TEST ONLY', 'phone': '0500000001',
                               'location': {'lat': 28.4, 'lng': 45.9},
                               'address': 'SYNTHETIC ADDRESS', 'neighborhood': 'اختبار فقط'}}
    actor('enbishan-customer')
    response = client.post('/api/quote', json=quote_body)
    assert response.status_code == 200, response.text
    assert response.json()['quote']['subtotal_halalas'] == 2000
    assert response.json()['quote']['delivery_fee_halalas'] == 700
    assert response.json()['quote']['total_halalas'] == 2700
    quote_body['items'][0]['units'][0]['options']['CHOICE_2'] = selected['CHOICE_1']
    invalid = client.post('/api/quote', json=quote_body)
    assert invalid.status_code == 409, invalid.text
    assert invalid.json()['detail']['code'] == 'PRODUCT_VARIANT_UNAVAILABLE'
    quote_body['items'] = [{'product_id': 'dual-kps', 'quantity': 1}]
    assert client.post('/api/quote', json=quote_body).status_code == 422

    # Owner can rename/stop/reactivate the seasonal box without a new product.
    box = next(p for p in preview['products'] if p['name'].startswith('بوكس رمضان'))
    renamed = post('owner', '/api/admin/products/' + box['id'],
                   {'name': 'بوكس اختبار موسمي', 'settings': {'source_verified': True, 'lifecycle': 'active'}},
                   brand='enbishan', method='patch')
    assert renamed['id'] == box['id'] and renamed['settings']['enabled'] is True
    stopped = post('owner', '/api/admin/products/' + box['id'],
                   {'settings': {'lifecycle': 'paused'}}, brand='enbishan', method='patch')
    assert stopped['settings']['enabled'] is False
    resumed = post('owner', '/api/admin/products/' + box['id'],
                   {'settings': {'lifecycle': 'active'}}, brand='enbishan', method='patch')
    assert resumed['name'] == 'بوكس اختبار موسمي' and resumed['settings']['enabled'] is True
    with db.tx() as conn:
        assert conn.execute("SELECT settings FROM branches WHERE id='kps-branch-1'").fetchone()['settings'] == source_settings
        assert conn.execute("SELECT count(*) n FROM products WHERE brand_id='enbishan' AND id=%s", (box['id'],)).fetchone()['n'] == 1

