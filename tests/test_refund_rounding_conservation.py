"""Discounted item allocations preserve exact money through repeated returns."""
from copy import deepcopy
from itertools import permutations
import random
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.refund_line_values import ceiling,item_allocations
from app.delivery_partial_returns import value,Line
from test_operations import ops


def sale(items=None,net=2000,delivery=0):
    return dict(total_halalas=net+delivery,delivery_fee_halalas=delivery,
                snapshot=dict(items=items or [dict(product_id=p,quantity=1,line_total_halalas=1000)
                                             for p in ('a','b','c')]))


def test_whole_discounted_order_returns_exact_net_and_preserves_input():
    order=sale();before=deepcopy(order)
    assert ceiling(order,dict(a=1,b=1,c=1),{})==2000
    assert order==before


def test_every_request_and_return_sequence_consumes_same_fixed_budgets():
    order=sale()
    assert [ceiling(order,{p:1},{}) for p in ('a','b','c')]==[666,667,667]
    for sequence in permutations(('a','b','c')):
        previous={};total=0
        for p in sequence:
            total+=ceiling(order,{p:1},previous)
            previous[p]=1
        assert total==2000
        assert ceiling(order,dict.fromkeys(sequence,1),{})==2000


def test_multiple_quantities_telescope_without_reallocating_other_products():
    order=sale([dict(product_id='a',quantity=3,line_total_halalas=3000),
                dict(product_id='b',quantity=2,line_total_halalas=2000)],net=3333)
    assert sum(ceiling(order,{'a':1},{'a':n}) for n in range(3))==1999
    assert sum(ceiling(order,{'b':1},{'b':n}) for n in range(2))==1334
    assert ceiling(order,{'b':2,'a':3},{})==3333


def test_gifts_and_delivery_never_receive_rounding_remainder():
    order=sale([dict(product_id='gift',quantity=2,line_total_halalas=0,is_gift=True),
                dict(product_id='a',quantity=1,line_total_halalas=1000),
                dict(product_id='b',quantity=1,line_total_halalas=1000),
                dict(product_id='c',quantity=1,line_total_halalas=1000)],delivery=500)
    assert ceiling(order,{'gift':2},{})==0
    assert ceiling(order,dict(gift=2,a=1,b=1,c=1),{})==2000


def test_duplicate_product_rows_use_one_budget_for_both_return_paths():
    order=sale([dict(product_id='a',quantity=1,line_total_halalas=1000),
                dict(product_id='b',quantity=1,line_total_halalas=1000),
                dict(product_id='a',quantity=1,line_total_halalas=1000)])
    first=value(order,[Line(line_index=2,quantity=1)],{})
    assert first==ceiling(order,{'a':1},{})==666
    rest=ceiling(order,{'a':1,'b':1},{'a':1})
    assert first+rest==2000
    assert value(order,[Line(line_index=0,quantity=1)],{2:1})==667


def test_physical_then_money_return_preserves_original_discount_once():
    order=sale(delivery=300)
    physical=value(order,[Line(line_index=1,quantity=1)],{})
    assert physical==667
    # valuation_order restores physical credits for unit valuation. It does not
    # invent a new receipt or lower the remaining units a second time.
    remaining=ceiling(order,{'a':1,'c':1},{'b':1})
    assert physical+remaining==2000
    assert order['total_halalas']-(physical+remaining)==300


def test_physical_request_order_does_not_change_combined_value():
    order=sale()
    lines=[Line(line_index=i,quantity=1) for i in (0,1)]
    assert value(order,lines,{})==value(order,list(reversed(lines)),{})==1333


def test_mixed_price_product_keeps_actual_line_values_and_requires_money_review():
    order=sale([dict(product_id='a',quantity=1,line_total_halalas=1000),
                dict(product_id='a',quantity=1,line_total_halalas=2000),
                dict(product_id='b',quantity=1,line_total_halalas=1000)],net=3000)
    assert value(order,[Line(line_index=0,quantity=1)],{})==750
    assert value(order,[Line(line_index=1,quantity=1)],{})==1500
    assert ceiling(order,{'a':2},{})==2250
    for selected,previous in [({'a':1},{}),({'a':1},{'a':1})]:
        with pytest.raises(HTTPException) as error:ceiling(order,selected,previous)
        assert error.value.detail['code']=='REFUND_LINE_AMBIGUOUS'


@pytest.mark.parametrize('prior',[-1,True,'1'])
def test_invalid_prior_quantities_do_not_create_extra_entitlement(prior):
    with pytest.raises(HTTPException):ceiling(sale(),{'a':1},{'a':prior})


def test_generated_full_and_split_returns_conserve_every_halalah():
    rng=random.Random(20261028)
    for _ in range(200):
        rows=[dict(product_id=f'p{i}',quantity=rng.randrange(1,6),
                   line_total_halalas=rng.randrange(0,5001)) for i in range(rng.randrange(1,10))]
        gross=sum(i['line_total_halalas'] for i in rows)
        net=rng.randrange(gross+1);order=sale(rows,net=net,delivery=rng.randrange(1001))
        before=deepcopy(order)
        _,products=item_allocations(order)
        assert sum(p['net_halalas'] for p in products.values())==net
        assert ceiling(order,{i['product_id']:i['quantity'] for i in rows},{})==net
        total=0
        for row in rows:
            pid=row['product_id']
            total+=sum(ceiling(order,{pid:1},{pid:n}) for n in range(row['quantity']))
        assert total==net and order==before


def legacy_sale():
    return sale([dict(product_id='a',quantity=10,line_total_halalas=1000),
                 dict(product_id='b',quantity=1,line_total_halalas=2000)],net=2000,delivery=500)


def test_actual_legacy_item_money_prevents_rounding_from_consuming_delivery():
    order=legacy_sale()
    # Earlier flooring really paid 600 for nine a units. Do not recalculate
    # that source amount from a newer allocation convention.
    assert ceiling(order,{'a':1,'b':1},{'a':9})==1401
    valued={**order,'_prior_item_effect_halalas':600}
    assert ceiling(valued,{'a':1,'b':1},{'a':9})==1400
    assert order['total_halalas']-(600+1400)==order['delivery_fee_halalas']==500


def test_legacy_documentation_accepts_actual_old_floor_without_new_money():
    order=legacy_sale()
    assert ceiling(order,{'a':9},{})==599
    documented={**order,'_prior_item_effect_halalas':0,'_legacy_refund_documentation':True}
    assert ceiling(documented,{'a':9},{})==600
    assert ceiling({**documented,'_prior_item_effect_halalas':1500},{'a':9},{})==500
    assert ceiling({**order,'_prior_item_effect_halalas':0,'_legacy_refund_documentation':False},{'a':9},{})==599


@pytest.mark.parametrize('prior',[-1,True,'600',None])
def test_missing_or_malformed_actual_history_cannot_increase_money(prior):
    with pytest.raises(HTTPException) as error:
        ceiling({**legacy_sale(),'_prior_item_effect_halalas':prior},{'a':1,'b':1},{'a':9})
    assert error.value.detail['code']=='REFUND_LINE_HISTORY'


def test_physical_credit_caps_actual_prior_credit_without_touching_history():
    order=legacy_sale()
    # The remaining a unit would now receive 67. Actual previous credits have
    # already consumed all but 66 of the entire item budget.
    previous={0:9};before=deepcopy(previous)
    assert value(order,[Line(line_index=0,quantity=1)],previous)==67
    assert value(order,[Line(line_index=0,quantity=1)],previous,actual_prior_credit_halalas=1934)==66
    assert previous==before


@pytest.mark.postgres
@pytest.mark.parametrize('documented',[True,False])
def test_legacy_refund_actual_amount_binds_next_refund_without_spending_fees(ops,documented):
    from app import db
    from test_accounting import accountant
    actor,post,order,_=ops
    source=legacy_sale();oid='rounding-source-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,discount_halalas,total_halalas,snapshot,customer) VALUES(%s,'ops-brand','ops-branch','ops-customer','pickup','ready','not_applicable','cash',3000,500,1000,2500,%s,'{}')",(oid,db.json(source['snapshot'])))
    post('branch',f'/api/orders/{oid}/collect',dict(method='card',amount_halalas=2500,reference='Actual original terminal receipt'))
    with db.tx() as c:
        collection=c.execute('SELECT id FROM collections WHERE order_id=%s',(oid,)).fetchone()['id']
        c.execute("INSERT INTO refunds(id,collection_id,order_id,method,amount_halalas,reason,authorized_by,line_quantities) VALUES('legacy-rounding-refund',%s,%s,'card',600,'Original pre-fix allocation, nine units','ops-owner',%s)",
                  (collection,oid,db.json({'a':9} if documented else {})))
        before=c.execute('SELECT * FROM refunds WHERE order_id=%s',(oid,)).fetchall()
    if not documented:
        accountant()
        url='/api/accounting/refund-item-allocations/orders/'+oid
        proposal=post('accountant',url,dict(refund_ids=['legacy-rounding-refund'],line_quantities={'a':9},
                      evidence_reference='Original return form for nine units',note='Document original 600 refund without another payment'))
        decision=post('owner',url+'/'+proposal['id']+'/approve',dict(confirmed=True,
                      evidence_reference='Independent signed return form verified',note='Verify original legacy refund and actual nine returned units'))
        assert decision['no_money_posted'] is True
        with db.tx() as c:assert c.execute('SELECT * FROM refunds WHERE order_id=%s',(oid,)).fetchall()==before
    payload=dict(method='card',amount_halalas=1401,reason='Remaining actual item units',line_quantities={'a':1,'b':1})
    result=post('owner',f'/api/orders/{oid}/refund',payload,status=422)
    assert result['detail']['code']=='REFUND_LINE_VALUE_EXCEEDED'
    with db.tx() as c:assert c.execute('SELECT * FROM refunds WHERE order_id=%s',(oid,)).fetchall()==before
    post('owner',f'/api/orders/{oid}/refund',{**payload,'amount_halalas':1400})
    with db.tx() as c:
        actual=c.execute('SELECT sum(amount_halalas) amount FROM refunds WHERE order_id=%s',(oid,)).fetchone()['amount']
        assert actual==2000
        assert c.execute('SELECT total_halalas-%s remaining FROM orders WHERE id=%s',(actual,oid)).fetchone()['remaining']==500
        assert c.execute("SELECT * FROM refunds WHERE id='legacy-rounding-refund'").fetchone()==before[0]
