"""Permanent float journeys in isolated PostgreSQL; never use operational data."""
import os
from uuid import uuid4
import psycopg
from psycopg import sql
import pytest
from app import db
from app.exports import build_archive, restore_archive
from test_operations import ops, delivered, begin_driver_closure

pytestmark = pytest.mark.postgres


def accountant():
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions) VALUES('ops-accountant','محاسب اختبار','admin','ops-brand','[\"ops-branch\"]','[\"accounting_review\"]')")


def approve_float(post, amount=20000, base=None):
    row=post('manager','/api/driver-floats',{'driver_id':'ops-driver','amount_halalas':amount,'base_request_id':base,'reference':'TEST-ACTUAL-CUSTODY','reason':'عهدة دائمة مسلمة فعليا'})
    post('accountant','/api/driver-floats/'+row['id']+'/decision',{'approved':True,'confirmed':True,'reference':'TEST-ACCOUNTANT-VERIFICATION'})
    return row['id']


def submit(post, driver, manager, counted, retained):
    begin_driver_closure(post,driver)
    return post('driver','/api/shifts/'+driver+'/submit',{'terminal_total_halalas':0,'counted_halalas':counted,'handover_halalas':counted-retained,'retained_halalas':retained,'handover':{'recipient_user_id':'ops-manager','to_shift_id':manager,'reference':'TEST-RECEIPT'}})


def test_carry_forward_never_reissues_and_completed_orders_disappear(ops):
    actor,post,order,shifts=ops
    accountant();approval=approve_float(post)
    manager,driver=shifts()
    current=actor('driver').get('/api/shifts/'+driver).json()
    assert current['shift']['permanent_float']=={'request_id':approval,'amount':20000}
    assert 'opening_halalas' not in current['shift']
    oid=delivered(post,order,driver)
    active=order();post('manager','/api/orders/'+active+'/assign',{'driver_shift_id':driver})
    tasks=actor('driver').get('/api/tasks').json()
    assert tasks['counts']['completed']==1
    assert [t['order_id'] for t in tasks['tasks']]==[active]
    assert oid not in str(actor('driver').get('/api/shifts/'+driver).json())
    assert actor('driver').get('/api/orders/'+oid).status_code==404
    assert actor('driver').get('/api/driver-incentives').json()['entries']==[]
    assert actor('driver').get('/api/driver-incentives/shifts/'+driver).json()['entries']==[]
    post('driver','/api/orders/'+oid+'/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':2000},key='same-collection-key',status=404)
    # Finish second task through the real delivery transitions.
    tid=tasks['tasks'][0]['id']
    post('driver','/api/tasks/'+tid+'/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(active,))
    post('driver','/api/tasks/'+tid+'/transition',{'status':'picked_up'})
    post('driver','/api/orders/'+active+'/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':1000})
    post('driver','/api/tasks/'+tid+'/transition',{'status':'delivered','proof':'TEST delivery'})
    assert actor('driver').get('/api/tasks').json()=={'tasks':[],'counts':{'completed':2}}
    # The driver cannot choose a different retained amount, including in a draft.
    post('driver','/api/shifts/'+driver+'/draft',{'terminal_total_halalas':0,'counted_halalas':22000,'handover_halalas':22000,'retained_halalas':0},status=422)
    post('driver','/api/shifts/'+driver+'/draft',{'terminal_total_halalas':0,'counted_halalas':22000,'handover_halalas':2000,'retained_halalas':20000})
    result=submit(post,driver,manager,22000,20000)
    assert 'difference_halalas' not in str(result) and 'expected_halalas' not in str(result)
    post('manager','/api/shifts/'+driver+'/driver-close',{'confirmed':True,'received_halalas':2000})
    with db.tx() as c:
        old=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
        assert not old['submission'].get('closure_exception')
    next_shift=post('driver','/api/shifts',{'kind':'driver','branch_ids':['ops-branch'],'manager_shift_id':manager})['shift']
    assert next_shift['permanent_float']['amount']==20000
    with db.tx() as c:
        assert c.execute('SELECT opening_halalas FROM shifts WHERE id=%s',(next_shift['id'],)).fetchone()['opening_halalas']==20000
    assert actor('driver').get('/api/tasks').json()['counts']['completed']==0


def test_shortage_keeps_entitlement_and_requires_review(ops):
    actor,post,order,shifts=ops;accountant();approve_float(post)
    manager,driver=shifts()
    submit(post,driver,manager,15000,15000)
    post('manager','/api/shifts/'+driver+'/driver-close',{'confirmed':True,'received_halalas':0})
    with db.tx() as c:
        assert c.execute('SELECT submission FROM shifts WHERE id=%s',(driver,)).fetchone()['submission']['financial_review_required'] is True
        assert c.execute("SELECT 1 FROM custody_events WHERE shift_id=%s AND kind='adjustment'",(driver,)).fetchone() is None
    next_shift=post('driver','/api/shifts',{'kind':'driver','branch_ids':['ops-branch'],'manager_shift_id':manager})['shift']
    assert next_shift['permanent_float']['amount']==20000
    with db.tx() as c:
        assert c.execute('SELECT opening_halalas FROM shifts WHERE id=%s',(next_shift['id'],)).fetchone()['opening_halalas']==15000
    for route in ['/api/shifts/current','/api/shifts/'+driver,'/api/shifts/'+driver+'/draft','/api/tasks']:
        visible=actor('driver').get(route)
        assert visible.status_code==200
        for hidden in ['difference_halalas','expected_halalas','card_difference_halalas','عجز فعلي']:
            assert hidden not in visible.text


def test_authorization_scope_stale_replay_and_immutable_records(ops):
    actor,post,order,shifts=ops;accountant()
    body={'driver_id':'ops-driver','amount_halalas':20000,'reference':'TEST','reason':'TEST actual custody'}
    post('driver','/api/driver-floats',body,status=403)
    assert actor('driver').get('/api/driver-floats').status_code==403
    post('manager','/api/driver-floats',{**body,'driver_id':'ops-other'},status=404)
    row=post('manager','/api/driver-floats',body,key='float-request')
    assert post('manager','/api/driver-floats',body,key='float-request')==row
    post('manager','/api/driver-floats',body,status=409)
    path='/api/driver-floats/'+row['id']+'/decision'
    decision={'approved':True,'confirmed':True,'reference':'TEST accountant'}
    post('manager',path,decision,status=403)
    post('other',path,decision,status=404)
    post('accountant',path,{**decision,'confirmed':False},status=422)
    with db.tx() as c:c.execute("UPDATE users SET permission_denials='[\"accounting_review\"]' WHERE id='ops-accountant'")
    post('accountant',path,decision,status=403)
    with db.tx() as c:c.execute("UPDATE users SET permission_denials='[]' WHERE id='ops-accountant'")
    result=post('accountant',path,decision,key='float-decision')
    assert post('accountant',path,decision,key='float-decision')==result
    post('accountant',path,decision,status=409)
    post('manager','/api/driver-floats',body,status=409)
    with pytest.raises(psycopg.Error),db.tx() as c:
        c.execute('UPDATE driver_float_requests SET amount_halalas=1 WHERE id=%s',(row['id'],))
    with pytest.raises(psycopg.Error),db.tx() as c:
        c.execute('DELETE FROM driver_float_decisions WHERE request_id=%s',(row['id'],))
    # Existing approval remains current when a proposed change has not been approved.
    pending=post('manager','/api/driver-floats',{**body,'base_request_id':row['id'],'amount_halalas':20000})
    manager,driver=shifts()
    post('accountant','/api/driver-floats/'+pending['id']+'/decision',decision,status=409)
    assert actor('driver').get('/api/shifts/'+driver).json()['shift']['permanent_float']['amount']==20000


def test_full_restore_preserves_float_approval_history(ops):
    actor,post,order,shifts=ops;accountant()
    first=approve_float(post);second='synthetic-historical-float-300'
    # Historical denomination is restored unchanged; it is never newly issued.
    with db.tx() as c:
        c.execute("INSERT INTO driver_float_requests(id,brand_id,driver_id,branch_ids,base_request_id,amount_halalas,reference,reason,requested_by) VALUES(%s,'ops-brand','ops-driver','[\"ops-branch\"]',%s,30000,'SYNTHETIC historical original','Historical prior denomination evidence','ops-manager')",(second,first))
        c.execute("INSERT INTO driver_float_decisions(request_id,brand_id,approved,reviewed_by,reference) VALUES(%s,'ops-brand',true,'ops-accountant','Historical independent certification')",(second,))
    with db.tx() as c:contents,manifest=build_archive(c,full=True)
    target='kps_restore_'+uuid4().hex
    url=os.environ['TEST_DATABASE_URL']
    try:
        restore_archive(contents,target_url=url,target_schema=target)
        with psycopg.connect(url) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
            assert c.execute('SELECT base_request_id FROM driver_float_requests WHERE id=%s',(second,)).fetchone()[0]==first
            assert c.execute('SELECT count(*) FROM driver_float_decisions WHERE approved').fetchone()[0]==2
    finally:
        with psycopg.connect(url,autocommit=True) as c:c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(target)))
