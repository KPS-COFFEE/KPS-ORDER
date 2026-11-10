from datetime import datetime
from uuid import uuid4

import pytest

from app import db, service_hours
from app.enbishan_operations_preparation import prepared_hours
from tests.test_enbishan_dual_brand import dual


@pytest.mark.postgres
def test_enbishan_morning_requires_approved_duty_and_keeps_evening_independent(dual, monkeypatch):
    client, actor, post, _, _ = dual
    branch_one, branch_two = 'enbishan-test-1', 'enbishan-test-2'
    clock = [datetime(2026, 11, 8, 8, 45, tzinfo=service_hours.RIYADH)]
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: clock[0])
    with db.tx() as conn:
        conn.execute("UPDATE branches SET settings=settings||%s WHERE id=%s",
                     (db.json({'opening_hours': prepared_hours(1), 'delivery_hours': prepared_hours(1)}), branch_one))
        conn.execute("UPDATE branches SET settings=settings||%s WHERE id=%s",
                     (db.json({'opening_hours': prepared_hours(2), 'delivery_hours': prepared_hours(2)}), branch_two))

    configuration = actor('owner', 'enbishan').get('/api/morning-delivery/policy').json()
    assert [row['id'] for row in configuration['branches']] == [branch_one]
    assert 'dual-manager' in {user['id'] for user in configuration['users']}
    policy = {'enabled': True, 'execution_branch_ids': [branch_one], 'manager_user_ids': ['dual-manager']}
    request = {'policy': policy, 'expected_version': 0, 'reason': 'Isolated morning admission test'}
    post('owner', '/api/morning-delivery/policy',
         request | {'policy': policy | {'execution_branch_ids': [branch_two]}},
         brand='enbishan', method='put', status=422)
    post('owner', '/api/morning-delivery/policy', request, brand='enbishan', method='put')

    body = {'brand_id': 'enbishan', 'branch_id': branch_one, 'service': 'delivery',
            'payment_method': 'cash', 'items': [{'product_id': 'dual-enbishan', 'quantity': 1}],
            'customer': {'name': 'SYNTHETIC', 'phone': '0500000001', 'location': {'lat': 28.4, 'lng': 45.9},
                         'address': 'SYNTHETIC', 'neighborhood': 'اختبار'}}
    assert actor('enbishan-customer').post('/api/quote', json=body).status_code == 409
    clock[0] = clock[0].replace(hour=9, minute=0)
    response = actor('enbishan-customer').post('/api/orders', json=body, headers={'Idempotency-Key': uuid4().hex})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'MORNING_DUTY_UNAVAILABLE', response.text

    clock[0] = clock[0].replace(hour=8, minute=45)
    header = {'X-Delivery-Period': 'morning'}
    response = actor('manager', 'enbishan').get('/api/session', headers=header)
    assert response.status_code == 200, response.text
    assert response.json()['user']['delivery_context']['bootstrap']
    response = actor('manager', 'enbishan').post('/api/shifts', headers=header | {'Idempotency-Key': uuid4().hex},
        json={'kind': 'manager', 'delivery_period': 'morning', 'branch_ids': [branch_one]})
    assert response.status_code == 200, response.text
    duty = response.json()['shift']
    assert duty['delivery_window_snapshot']['period'] == 'morning'

    clock[0] = clock[0].replace(hour=9, minute=0)
    response = actor('enbishan-customer').post('/api/orders', json=body, headers={'Idempotency-Key': uuid4().hex})
    assert response.status_code == 200, response.text
    order = response.json()['order']
    assert 'manager_shift_id' not in order['snapshot']
    with db.tx() as conn:
        snapshot = conn.execute('SELECT snapshot FROM orders WHERE id=%s', (order['id'],)).fetchone()['snapshot']
        assert snapshot['delivery_period'] == 'morning'
        assert snapshot['manager_shift_id'] == duty['id']
        assert snapshot['delivery_workflow'] == 'manager_dispatch'
    path = '/api/orders/' + order['id'] + '/manager-accept'
    response = actor('manager', 'enbishan').post(path, json={'version': order['version']},
        headers=header | {'Idempotency-Key': uuid4().hex})
    assert response.status_code == 200, response.text

    clock[0] = clock[0].replace(hour=12)
    assert actor('enbishan-customer').post('/api/quote', json=body).status_code == 409
    clock[0] = clock[0].replace(hour=15)
    response = actor('enbishan-customer').post('/api/quote', json=body)
    assert response.status_code == 200 and 'delivery_period' not in response.json()['quote'], response.text
    response = actor('enbishan-customer').post('/api/orders', json=body, headers={'Idempotency-Key': uuid4().hex})
    assert response.status_code == 409 and response.json()['detail']['code'] == 'DELIVERY_DUTY_UNAVAILABLE', response.text
