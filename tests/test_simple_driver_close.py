"""Actual receipt and closure are atomic, scoped and safe to retry."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from app import db, operations
from tests.test_operations import ops, delivered
from tests.test_hardening_operations import request, principal, outcome

pytestmark=pytest.mark.postgres

@pytest.mark.parametrize('counted',[900,1000,1100])
@pytest.mark.parametrize('transfer_exists',[False,True])
def test_submitted_receipt_and_approval_once(ops,counted,transfer_exists):
    actor,post,order,shifts=ops
    manager,driver=shifts();delivered(post,order,driver)
    post('driver',f'/api/shifts/{driver}/closing')
    post('driver',f'/api/shifts/{driver}/submit',{'counted_halalas':counted,'handover_halalas':counted})
    if transfer_exists:
        post('driver','/api/custody',{'from_shift_id':driver,'to_shift_id':manager,'amount_halalas':counted,'reference':'TEST cash','closure':True})
    body={'received_halalas':counted,'confirmed':True,'exception_reason':'TEST actual discrepancy' if counted!=1000 else ''}
    url=f'/api/shifts/{driver}/driver-close'
    if counted!=1000:post('manager',url,{**body,'exception_reason':''},status=422)
    post('manager',url,{**body,'received_halalas':counted+100},status=422)
    result=post('manager',url,body,key='TEST-single-close')
    assert post('manager',url,body,key='TEST-single-close')==result
    post('manager',url,body,status=409)
    with db.tx() as c:
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
        assert s['status']=='closed' and s['submission']['difference_halalas']==counted-1000
        assert c.execute('SELECT count(*) n FROM closure_reports WHERE shift_id=%s',(driver,)).fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM custody_events WHERE shift_id=%s AND kind='transfer_in'",(manager,)).fetchone()['n']==1
        assert c.execute("SELECT sum(amount_halalas) n FROM custody_events WHERE shift_id=%s AND kind='transfer_in'",(manager,)).fetchone()['n']==counted

@pytest.mark.parametrize('status',['open','closing'])
def test_delegated_close_and_notification(ops,status):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    if status=='closing':post('driver',f'/api/shifts/{driver}/closing')
    body={'counted_halalas':0,'received_halalas':0,'confirmed':True,'exception_reason':'TEST phone unavailable'}
    url=f'/api/shifts/{driver}/driver-close'
    post('manager',url,{**body,'exception_reason':''},status=422)
    post('driver',url,body,status=403)
    post('other',url,body,status=404)
    post('manager',url,{**body,'confirmed':False},status=422)
    result=post('manager',url,body,key='TEST-delegated-close')
    assert post('manager',url,body,key='TEST-delegated-close')==result
    with db.tx() as c:
        s=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
        assert s['status']=='closed'
        assert s['submission']['closure_exception']['delegated'] is True
        assert s['vehicle_log']['end_exception']['reason']==body['exception_reason']
    notices=actor('owner').get('/api/report-notifications').json()['notifications']
    assert len(notices)==1 and notices[0]['closure_exception']['delegated'] is True
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
    post('manager',url,body,key='TEST-delegated-close',status=403)

def test_unresolved_task_rolls_back_everything(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts();oid=order()
    post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})
    post('manager',f'/api/shifts/{driver}/driver-close',{'counted_halalas':0,'received_halalas':0,'confirmed':True,'exception_reason':'TEST unavailable'},status=409)
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(driver,)).fetchone()['status']=='open'
        assert c.execute('SELECT count(*) n FROM custody_transfers WHERE from_shift_id=%s',(driver,)).fetchone()['n']==0

def test_different_keys_race_cannot_repeat_financial_effects(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    user=principal('manager')
    body=operations.DriverClose(counted_halalas=0,received_halalas=0,confirmed=True,exception_reason='TEST unavailable')
    def close(_):return outcome(lambda:operations.simple_driver_close(driver,body,request(uuid4().hex),user))
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(close,range(2)))
    assert sorted(r[0] for r in results)==[200,409]
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM closure_reports WHERE shift_id=%s',(driver,)).fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM custody_events WHERE shift_id=%s AND kind='transfer_in'",(manager,)).fetchone()['n']==1
