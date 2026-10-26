from copy import deepcopy
import pytest
from fastapi import HTTPException
from app.financial_integrity import inspect_snapshot, require_verified


def evidence():
    return dict(shift=dict(id='shift',opening_halalas=0,submission=dict(expected_halalas=19750,counted_halalas=19750,difference_halalas=0)),
        custody_events=[dict(id='event',shift_id='shift',kind='collection',reference_id='cash',amount_halalas=19750)],transfers=[],orders=[dict(id='order', fulfilled_in_report=True,total_halalas=68350)],
        collections=[dict(id='cash',method='cash',amount_halalas=19750),dict(id='card',method='card',amount_halalas=48600)],
        refunds=[], summary=dict(unique_orders=1,completed_orders=1,gross_completed_halalas=68350,
            cash_collected_halalas=19750,card_collected_halalas=48600,
            cash_refunded_halalas=0,card_refunded_halalas=0,net_collections_halalas=68350))


def test_valid_evidence_is_read_only():
    snap=evidence(); before=deepcopy(snap)
    assert require_verified(snap)['verified']
    assert snap==before


def test_offsetting_channel_errors_are_not_hidden_by_matching_total():
    snap=evidence();snap['summary']['cash_collected_halalas']+=100
    snap['summary']['card_collected_halalas']-=100
    assert {r['code'] for r in inspect_snapshot(snap)['issues']}=={'cash_collected_halalas','card_collected_halalas'}


def test_recorded_refund_missing_from_summary_blocks_approval():
    snap=evidence();snap['refunds']=[dict(id='refund',method='cash',amount_halalas=2350)]
    with pytest.raises(HTTPException) as error:require_verified(snap)
    assert error.value.status_code==409
    assert {i['code'] for i in error.value.detail['integrity']['issues']}=={'cash_refunded_halalas','net_collections_halalas'}
    snap['summary'].update(cash_refunded_halalas=2350,net_collections_halalas=66000)
    assert require_verified(snap)['verified']


def test_replayed_collection_detected_even_if_summary_was_inflated():
    snap=evidence();snap['collections'].append(deepcopy(snap['collections'][0]))
    snap['summary']['cash_collected_halalas']+=19750;snap['summary']['net_collections_halalas']+=19750
    assert not inspect_snapshot(snap)['verified']


def test_duplicate_historical_brand_summary_is_detected():
    snap=evidence();snap['external_enbishan']=[dict(shift_id='old',cash_halalas=21550,card_halalas=0,total_halalas=21550,order_count=8)]*2
    snap['external_enbishan_totals']=dict(cash_halalas=43100,card_halalas=0,total_halalas=43100,order_count=16)
    assert 'DUPLICATE_EXTERNAL_SHIFT' in {r['code'] for r in inspect_snapshot(snap)['issues']}


def test_cancelled_order_retained_but_not_counted_as_completed():
    snap=evidence();snap['orders'].append(dict(id='cancelled',fulfilled_in_report=False,total_halalas=3150))
    snap['summary']['unique_orders']=2
    assert require_verified(snap)['verified']
    snap['summary']['gross_completed_halalas']+=3150
    assert not inspect_snapshot(snap)['verified']


@pytest.mark.parametrize('amount',[-1,0.5,'100',True])
def test_money_requires_nonnegative_integer_halalas(amount):
    snap=evidence();snap['collections'][0]['amount_halalas']=amount
    assert not inspect_snapshot(snap)['verified']


def test_missing_evidence_never_claims_verified():
    assert not inspect_snapshot({'summary':{}})['verified']
    with pytest.raises(HTTPException):require_verified({'summary':{}})


@pytest.mark.parametrize('value',[False,True,None,'0',0.0])
def test_summary_values_require_documented_integer_not_missing_or_coerced_zero(value):
    snap=evidence();snap['summary']['cash_refunded_halalas']=value
    assert not inspect_snapshot(snap)['verified']


@pytest.mark.parametrize('value',[None,{},'bad',[None],[{'id':[]}],[{'id':'o','total_halalas':False}]])
def test_malformed_order_evidence_fails_closed_without_crashing(value):
    snap=evidence();snap['orders']=value
    assert not inspect_snapshot(snap)['verified']
