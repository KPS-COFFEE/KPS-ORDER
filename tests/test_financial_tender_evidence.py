from copy import deepcopy
import pytest
from app.financial_integrity import inspect_snapshot
from test_financial_integrity import evidence


def tender_snapshot():
    snap=evidence()
    snap['collections'][0].update(shift_id='shift',tendered_halalas=19750,change_halalas=0,reference='')
    snap['custody_events'][0].update(kind='adjustment',reference_id='correction',actor_id='accountant')
    snap['collection_tender_corrections']=[dict(id='correction',proposal_id='proposal',collection_id='cash',
        source_correction_id=None,revision=1,effective_shift_id='shift',original_shift_id='shift',raw_method='card',
        original_collector_id='driver',source_method='card',target_method='cash',amount_halalas=19750,
        target_tendered_halalas=19750,target_change_halalas=0,target_reference='',cash_delta_halalas=19750,
        custody_event_id='event',approved_by='accountant',evidence_reference='Verified original tender receipt')]
    return snap


def test_approved_card_to_cash_has_one_adjustment_and_no_fictitious_collection():
    snap=tender_snapshot();before=deepcopy(snap)
    assert inspect_snapshot(snap)['verified']
    assert snap==before and len(snap['collections'])==2 and len(snap['custody_events'])==1


@pytest.mark.parametrize('field,value',[
    ('revision',2),('source_correction_id','unrelated'),('cash_delta_halalas',-19750),
    ('amount_halalas',19749),('effective_shift_id','other'),('original_shift_id','other'),
    ('target_method','card'),('approved_by','driver'),('custody_event_id','missing')])
def test_payment_reclassification_needs_exact_approved_chain_and_cash_effect(field,value):
    snap=tender_snapshot();snap['collection_tender_corrections'][0][field]=value
    assert not inspect_snapshot(snap)['verified']


def test_unapproved_card_to_cash_still_fails_source_evidence():
    snap=tender_snapshot();snap.pop('collection_tender_corrections')
    assert 'CASH_SOURCE_EFFECT_MISMATCH' in {r['code'] for r in inspect_snapshot(snap)['issues']}


def test_cash_to_card_requires_original_cash_effect_and_negative_reclassification():
    snap=tender_snapshot();source=snap['collections'][0];source.update(method='card',tendered_halalas=None,change_halalas=0,reference='terminal-001')
    row=snap['collection_tender_corrections'][0];row.update(raw_method='cash',source_method='cash',target_method='card',
        target_tendered_halalas=None,target_change_halalas=0,target_reference='terminal-001',cash_delta_halalas=-19750)
    snap['custody_events'][0]['amount_halalas']=-19750
    snap['custody_events'].append(dict(id='original',shift_id='shift',kind='collection',reference_id='cash',amount_halalas=19750,actor_id='driver'))
    snap['summary'].update(cash_collected_halalas=0,card_collected_halalas=68350)
    snap['shift']['submission'].update(expected_halalas=0,counted_halalas=0,difference_halalas=0)
    assert inspect_snapshot(snap)['verified']
    snap['custody_events'].pop()
    assert not inspect_snapshot(snap)['verified']


@pytest.mark.parametrize('tendered,change',[(True,0),(19749,-1),(19750,True),(20000,0)])
def test_reclassified_cash_tender_and_change_are_documented_integer_net(tendered,change):
    snap=tender_snapshot();snap['collections'][0].update(tendered_halalas=tendered,change_halalas=change)
    snap['collection_tender_corrections'][0].update(target_tendered_halalas=tendered,target_change_halalas=change)
    assert not inspect_snapshot(snap)['verified']
