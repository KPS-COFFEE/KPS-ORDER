from copy import deepcopy
import pytest
from app.financial_integrity import inspect_snapshot
from test_financial_integrity import evidence


def codes(snap):
    return {r['code'] for r in inspect_snapshot(snap)['issues']}


def test_cash_expected_is_rebuilt_without_legacy_close_difference_or_final_transfer():
    snap=evidence();sub=snap['shift']['submission']
    sub.update(counted_halalas=19500,difference_halalas=-250,actual_handover_halalas=19300)
    snap['transfers']=[dict(id='transfer',from_shift_id='shift',to_shift_id='manager',closure=True,
        status='confirmed',amount_halalas=19300,recipient_user_id='manager-user',confirmed_by='manager-user',confirmed_at='2026-10-26T08:00:00Z')]
    snap['custody_events'] += [dict(id='adjustment',shift_id='shift',kind='adjustment',reference_id='closure-difference:shift',amount_halalas=-250),
        dict(id='out',shift_id='shift',kind='transfer_out',reference_id='transfer',amount_halalas=-19300)]
    before=deepcopy(snap)
    assert inspect_snapshot(snap)['verified']
    assert before==snap
    snap['custody_events'][0]['amount_halalas']-=100
    assert 'CUSTODY_EXPECTED_MISMATCH' in codes(snap)


@pytest.mark.parametrize('field',['shift','custody_events','transfers'])
def test_missing_custody_is_undocumented_and_not_assumed_zero(field):
    snap=evidence();snap.pop(field)
    assert 'CUSTODY_EVIDENCE_MISSING' in codes(snap)


def test_cash_receipt_requires_actual_confirmed_transfer_evidence():
    snap=evidence();snap['shift']['submission']['actual_handover_halalas']=0
    assert 'CUSTODY_ACTUAL_RECEIPT_MISMATCH' in codes(snap)


def test_cash_collection_requires_its_exact_source_effect():
    snap=evidence();snap['collections'][0]['shift_id']='shift'
    assert inspect_snapshot(snap)['verified']
    snap['custody_events'][0]['reference_id']='unrelated'
    assert 'CASH_SOURCE_EFFECT_MISMATCH' in codes(snap)


def test_custody_duplicate_effect_cannot_inflate_ledger():
    snap=evidence();snap['custody_events'].append({**snap['custody_events'][0],'id':'another'})
    assert 'DUPLICATE_CUSTODY_EFFECT' in codes(snap)


def test_evidenced_driver_attribution_uses_linked_adjustment_not_a_second_collection():
    snap=evidence();cash=snap['collections'][0]
    cash.update(order_id='order',shift_id='shift')
    snap['custody_events'][0].update(kind='adjustment',reference_id='correction')
    snap['driver_attribution_corrections']=[dict(id='correction',order_id='order',source_shift_id='old-driver',target_shift_id='shift',cash_halalas=19750)]
    assert inspect_snapshot(snap)['verified']
    snap['driver_attribution_corrections'][0]['cash_halalas']-=1
    assert 'ATTRIBUTION_CASH_EFFECT_MISMATCH' in codes(snap)
