from uuid import uuid4

import pytest

from app import db
from test_aggregate_driver_closure import rows
from test_central_workdays import central, inspection, open_days
from test_enbishan_dual_brand import dual

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize('allocated_before', [False, True])
def test_matched_physical_bag_closes_both_sellers_once(central, monkeypatch, allocated_before):
    from app import reporting
    monkeypatch.setattr(reporting, 'generate_files', lambda *_: None)
    _, actor, post, _, _ = central
    manager, driver = open_days(central)
    path = '/api/central-operations/workdays/' + driver['shift']['id']
    requested = post('driver', path + '/request', {'reason':'Finished synthetic shift'})
    post('manager', path + '/decision', {'request_id':requested['shift']['closure_authorization']['id'],
         'decision':'approved','reason':'Verified end of duty'})
    post('driver', path + '/closing', {})
    declaration = post('driver', path + '/physical-submit', {'cash_halalas':0,'card_halalas':0,
        'vehicle_inspection':inspection(1010),'handover_branch_id':'kps-branch-1',
        'bag_reference':'Test zero collection bag','bag_delivered':True})
    did = declaration['declaration_id']
    receipt = post('manager', path + '/physical-receipt', {'declaration_id':did,'received_halalas':0,
        'confirmed':True,'reference':'Observed empty bag at handover'})
    bid = declaration['cash_bag']['id']
    counted = post('accountant','/api/cash-bags/'+bid+'/count',{'received_cash_halalas':0,'card_halalas':0,
        'reference':'Verified zero counted cash and card','confirmed':True})
    data = {'declaration_id':did,'receipt_id':receipt['receipt_id'],'legs':rows(driver['shifts'],(0,0),(0,0)),
            'confirmed':True,'reason':'Independent review of both seller ledgers'}
    post('manager','/api/cash-bags/workdays/'+manager['shift']['id']+'/vault-close',
         {'confirmed':True,'note':'Confirmed physical deposit of individual bags','allow_incomplete':True})
    key = uuid4().hex
    target = path + '/physical-allocation'
    if allocated_before:
        previous = post('accountant',target,data)
        target = path + '/accounting-close'
        data = {'count_id':counted['count_id'],'allocation_id':previous['allocation_id'],'confirmed':True}
    else:
        data['finalize_count_id'] = counted['count_id']
    post('accountant',target,data,key=key)
    post('accountant',target,data,key=key)
    state = actor('accountant').get(path+'/physical-closure').json()
    assert state['channels_closed']
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM driver_accounting_settlements").fetchone()['n']==4
        assert c.execute("SELECT count(*) n FROM driver_cash_bag_counts").fetchone()['n']==1


def test_counted_bag_difference_cannot_finalize_or_partially_post(central, monkeypatch):
    from test_cash_bag_custody import prepared, count_bag
    manager, driver, path, declaration, bid, review, allocation = prepared(central,monkeypatch)
    _,actor,post,_,_ = central
    count = count_bag(central,bid,700)
    post('manager','/api/cash-bags/workdays/'+manager['shift']['id']+'/vault-close',
         {'confirmed':True,'note':'Independent manager ended the duty','allow_incomplete':True})
    payload = dict(allocation, finalize_count_id=count['count_id'])
    result = post('accountant',path+'/physical-allocation',payload,status=409)
    assert result['detail']['code']=='CHANNEL_REVIEW_REQUIRED'
    with db.tx() as c:
        assert not c.execute('SELECT id FROM shared_driver_closure_allocations').fetchone()
        assert not c.execute('SELECT id FROM driver_accounting_settlements').fetchone()
    post('accountant',path+'/physical-allocation',dict(payload,record_for_review=True))
    card=actor('accountant').get(path+'/physical-closure').json()
    assert not card['channels_closed']
    assert all(r['accounting_report']['report_id'] for r in card['legs'])
