"""Real transactions in isolated PostgreSQL; live flags are never changed."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from app import db, smart_dispatch as dispatch
from test_operations import ops


def test_default_flags_and_strict_policy(monkeypatch):
    from pydantic import ValidationError
    assert dispatch.flags()=={'OFFER_ACCEPT':False,'AUTO_ASSIGN':False}
    assert dispatch.DispatchPolicy().mode=='MANUAL'
    with pytest.raises(HTTPException):dispatch.enabled(dispatch.DispatchPolicy(mode='OFFER_ACCEPT'),'OFFER_ACCEPT')
    for config in ({'max_active_tasks':0},{'offer_timeout_seconds':1},{'mode':'other'},{'unknown':True}):
        with pytest.raises(ValidationError):dispatch.DispatchPolicy.model_validate(config)


def enable(monkeypatch,mode='OFFER_ACCEPT',**settings):
    monkeypatch.setenv('DISPATCH_'+mode+'_ENABLED','true')
    with db.tx() as c:c.execute("INSERT INTO app_settings(brand_id,settings) VALUES('ops-brand',%s) ON CONFLICT(brand_id) DO UPDATE SET settings=EXCLUDED.settings",(db.json({'dispatch':{'mode':mode,**settings}}),))


def request(key):return Request({'type':'http','headers':[(b'idempotency-key',key.encode())]})


@pytest.mark.postgres
def test_manual_unchanged_and_smart_modes_disabled(ops):
    actor,post,order,shifts=ops;manager,driver=shifts();oid=order()
    assert actor('driver').get('/api/dispatch/policy').json()['mode']=='MANUAL'
    assert actor('driver').get('/api/dispatch/offers').json()=={'offers':[]}
    post('manager','/api/dispatch/offers',{'order_id':oid,'driver_shift_id':driver},status=409)
    post('manager','/api/dispatch/orders/'+oid+'/auto-assign',status=409)
    post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':driver})
    assert len(actor('driver').get('/api/tasks').json()['tasks'])==1


@pytest.mark.postgres
def test_atomic_accept_reconnect_and_private_offer(ops,monkeypatch):
    actor,post,order,shifts=ops;manager,driver=shifts();enable(monkeypatch)
    driver2=post('driver2','/api/shifts',{'kind':'driver','branch_ids':['ops-branch'],'manager_shift_id':manager})['shift']['id']
    oid=order(notes='PRIVATE NOTE')
    offers=[post('manager','/api/dispatch/offers',{'order_id':oid,'driver_shift_id':s})['offer_id'] for s in (driver,driver2)]
    inbox=actor('driver').get('/api/dispatch/offers').json()['offers']
    assert len(inbox)==1 and set(inbox[0])=={'id','expires_at','order_no','branch','preparation_minutes','delivery_zone'}
    assert 'PRIVATE' not in str(inbox)
    post('driver2','/api/dispatch/offers/'+offers[0]+'/decision',{'decision':'accept'},status=404)
    with db.tx() as c:users=[c.execute('SELECT * FROM users WHERE id=%s',('ops-'+n,)).fetchone() for n in ('driver','driver2')]
    def accept(i):
        try:return dispatch.decide(offers[i],dispatch.Decision(decision='accept'),request('race-'+str(i)),users[i])
        except HTTPException as e:return e.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(accept,[0,1]))
    assert sum(isinstance(r,dict) and r.get('status')=='accepted' for r in results)==1
    assert 409 in results
    winner=next(i for i,r in enumerate(results) if isinstance(r,dict))
    assert accept(winner)==results[winner] # lost response / reconnect replay
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['n']==1
        assert {r['action'] for r in c.execute('SELECT action FROM dispatch_offer_events').fetchall()}=={'offered','accepted','superseded'}
    post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':driver},status=409)
    assert actor('driver').get('/api/dispatch/offers').json()=={'offers':[]}


@pytest.mark.postgres
def test_expiry_decline_reoffer_scope_capacity(ops,monkeypatch):
    actor,post,order,shifts=ops;manager,driver=shifts();enable(monkeypatch,max_active_tasks=1)
    oid=order();body={'order_id':oid,'driver_shift_id':driver}
    fid=post('manager','/api/dispatch/offers',body)['offer_id']
    assert post('manager','/api/dispatch/offers',body)['offer_id']==fid
    post('other','/api/dispatch/offers',body,status=409) # other brand remains MANUAL
    with db.tx() as c:c.execute("UPDATE dispatch_offers SET expires_at=now()-interval '1 second' WHERE id=%s",(fid,))
    assert post('driver','/api/dispatch/offers/'+fid+'/decision',{'decision':'accept'})['status']=='expired'
    fid2=post('manager','/api/dispatch/offers',body)['offer_id'];assert fid2!=fid
    assert post('driver','/api/dispatch/offers/'+fid2+'/decision',{'decision':'decline'})['status']=='declined'
    fid3=post('manager','/api/dispatch/offers',body)['offer_id']
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-driver'")
    post('driver','/api/dispatch/offers/'+fid3+'/decision',{'decision':'accept'},status=404)
    with db.tx() as c:
        c.execute("UPDATE users SET branch_ids='[\"ops-branch\"]' WHERE id='ops-driver'")
        assert c.execute('SELECT delivery_status FROM orders WHERE id=%s',(oid,)).fetchone()['delivery_status']=='pending'
    post('driver','/api/dispatch/offers/'+fid3+'/decision',{'decision':'accept'})
    post('manager','/api/dispatch/offers',{'order_id':order(),'driver_shift_id':driver},status=409)


@pytest.mark.postgres
def test_auto_assignment_capacity_and_manual_fallback(ops,monkeypatch):
    actor,post,order,shifts=ops;manager,driver=shifts();enable(monkeypatch,'AUTO_ASSIGN',max_active_tasks=1)
    oid=order();result=post('manager','/api/dispatch/orders/'+oid+'/auto-assign',key='auto-reconnect')
    assert result['task']['shift_id']==driver
    assert post('manager','/api/dispatch/orders/'+oid+'/auto-assign',key='auto-reconnect')==result
    assert post('manager','/api/dispatch/orders/'+order()+'/auto-assign')['status']=='manager_required'
    post('driver','/api/dispatch/orders/'+order()+'/auto-assign',status=403)
