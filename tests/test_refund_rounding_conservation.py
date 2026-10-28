"""Discounted item allocations preserve exact money through repeated returns."""
from copy import deepcopy
from itertools import permutations
import random

import pytest
from fastapi import HTTPException

from app.refund_line_values import ceiling,item_allocations
from app.delivery_partial_returns import value,Line


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
