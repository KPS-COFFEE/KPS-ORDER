"""Completion bridges menu placements without widening purchased-unit custody."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app import catalog, db
from app.complimentary_authorizations import completion_aliases
from app.orders import ComplimentaryOrder
from test_core import _body, _prepare_catalog
from test_operations import ops


def original_and_request(quantity=2):
    _prepare_catalog()
    identifier = 'completion-alias-' + uuid4().hex
    snapshot = {'order_reference': identifier, 'items': [
        {'product_id': 'kps-01', 'name': 'كولد برو', 'quantity': quantity,
         'unit_price_halalas': 950, 'line_total_halalas': 950*quantity,
         'units': [{'options': {}} for _ in range(quantity)]}]}
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,"
                  "delivery_status,payment_status,payment_method,subtotal_halalas,delivery_fee_halalas,"
                  "total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-1','ops-customer',"
                  "'delivery','ready','delivered','paid','cash',%s,500,%s,%s,'{}')",
                  (identifier, 950*quantity, 950*quantity+500, db.json(snapshot)))
    body = _body('delivery', product='kps-08')
    body['items'][0]['quantity'] = 1
    body.update(kind='completion', reason='Synthetic missing paid unit',
                original_order_id=identifier, original_invoice_number=identifier)
    return identifier, snapshot, body


def authorize(actor, body):
    return actor('owner').post('/api/orders/complimentary/authorizations', json=body,
                               headers={'Idempotency-Key': uuid4().hex})


@pytest.mark.postgres
@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_equivalent_current_placement_completion_keeps_payload_and_reserves_both_ids(ops):
    actor, _, _, _ = ops
    oid, snapshot, body = original_and_request()
    first = authorize(actor, body)
    assert first.status_code == 200, first.text
    assert first.json()['authorization']['payload']['items'][0]['product_id'] == 'kps-08'
    source = deepcopy(body)
    source['items'][0]['product_id'] = 'kps-01'
    second = authorize(actor, source)
    assert second.status_code == 200, second.text
    for request in (body, source):
        response = authorize(actor, request)
        assert response.status_code == 422, response.text
        assert response.json()['detail']['code'] == 'COMPLETION_ITEMS_INVALID'
    with db.tx() as c:
        assert c.execute('SELECT snapshot FROM orders WHERE id=%s', (oid,)).fetchone()['snapshot'] == snapshot


@pytest.mark.postgres
@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
@pytest.mark.parametrize('difference', ['price', 'stock', 'options', 'source'])
def test_current_non_equivalent_placement_never_substitutes_for_original(ops, difference):
    actor, _, _, _ = ops
    _, _, body = original_and_request()
    with db.tx() as c:
        if difference == 'price':
            c.execute("UPDATE products SET price_halalas=price_halalas+100 WHERE id='kps-01'")
        elif difference == 'stock':
            c.execute("UPDATE branches SET settings=settings||%s WHERE id='kps-branch-1'",
                      (db.json({'product_availability': {'kps-01': False}}),))
        elif difference == 'options':
            c.execute("UPDATE products SET settings=settings||%s WHERE id='kps-01'",
                      (db.json({'option_groups': [{'code': 'FLAVOUR', 'required': False, 'min_select': 0, 'values': []}]}),))
        else:
            c.execute("UPDATE products SET settings=settings||%s WHERE id='kps-01'",
                      (db.json({'source_verified': False}),))
    response = authorize(actor, body)
    assert response.status_code == 422, response.text
    assert response.json()['detail']['code'] == 'COMPLETION_ITEMS_INVALID'


@pytest.mark.postgres
@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_changed_terms_cannot_forget_an_alias_reservation(ops):
    actor, _, _, _ = ops
    _, _, body = original_and_request()
    assert authorize(actor, body).status_code == 200
    with db.tx() as c:
        c.execute("UPDATE products SET price_halalas=price_halalas+100 WHERE id='kps-08'")
    source = deepcopy(body)
    source['items'][0].update(product_id='kps-01', quantity=2)
    response = authorize(actor, source)
    assert response.status_code == 422, response.text
    source['items'][0]['quantity'] = 1
    response = authorize(actor, source)
    assert response.status_code == 200, response.text
    assert authorize(actor, source).status_code == 422


@pytest.mark.postgres
@pytest.mark.parametrize('ops', ['fiscal-kps'], indirect=True)
def test_equivalence_uses_the_priced_execution_branch_not_selected_branch(ops):
    _, _, body = original_and_request()
    with db.tx() as c:
        c.execute("UPDATE branches SET settings=settings||%s WHERE id='kps-branch-2'",
                  (db.json({'product_availability': {'kps-01': False}}),))
        parsed = ComplimentaryOrder(**body)
        selected = completion_aliases(c, parsed, {'branch_id': 'kps-branch-1'})
        execution = completion_aliases(c, parsed, {'branch_id': 'kps-branch-2'})
    assert selected['kps-01'] == 'kps-08'
    assert 'kps-01' not in execution
