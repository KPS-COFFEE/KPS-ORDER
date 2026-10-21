"""Actual PostgreSQL/session/CSRF enforcement, negative IDOR and closure tests."""
from copy import deepcopy
from uuid import uuid4
import pytest
from psycopg.errors import RaiseException
from app import auth, db, employee_permissions, service_hours, shift_vehicle
from test_core import _authenticate
from test_operations import ops
from test_morning_delivery import at

pytestmark=pytest.mark.postgres
B2='kps-branch-2'
B3='kps-branch-3'
LEGACY={'orders_create_customer','orders_amend','orders_print','chat_view','chat_reply','chat_manage'}


@pytest.fixture
def morning_manager(ops,monkeypatch):
    actor,_,_,_=ops
    monkeypatch.setattr(service_hours,'now_riyadh',lambda:at('08:00:00'))
    with db.tx() as c:
        policy={'version':1,'enabled':True,'execution_branch_ids':[B2],
                'preparer_user_ids':['ops-branch'],'manager_user_ids':['ops-manager']}
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'",(db.json({'morning_delivery':policy}),))
        denials=sorted(employee_permissions.eligible('delivery_manager')-LEGACY)
        c.execute("UPDATE users SET branch_ids=%s,permission_denials=%s WHERE id='ops-manager'",(db.json([B2,B3]),db.json(denials)))
        c.execute("UPDATE users SET branch_ids=%s WHERE id IN ('ops-driver','ops-driver2','ops-branch')",(db.json([B2,B3]),))
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('context-other-manager','other','delivery_manager','kps',%s)",(db.json([B2]),))
        def shift(sid,kind,uid,branch=B2,period='morning',parent=None,status='open'):
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,status,delivery_window_snapshot) VALUES(%s,%s,%s,'kps',%s,%s,%s,%s)",
                      (sid,kind,uid,db.json([branch]),parent,status,db.json({'period':period})))
        shift('ctx-duty','manager','ops-manager')
        shift('ctx-driver','driver','ops-driver',parent='ctx-duty')
        shift('ctx-other-duty','manager','context-other-manager')
        shift('ctx-other-driver','driver','ops-driver2',parent='ctx-other-duty')
        shift('ctx-evening','manager','ops-manager',period='evening',status='closed')
        shift('ctx-evening-driver','driver','ops-driver2',period='evening',parent='ctx-evening',status='closed')
        shift('ctx-other-branch','driver','ops-driver2',branch=B3,parent='ctx-other-duty',status='closed')
        ids={}
        for label,branch,period,brand in [('morning',B2,'morning','kps'),('bound_other',B2,'morning','kps'),('evening',B2,'evening','kps'),('branch3',B3,'morning','kps'),('foreign','ops-branch-other','morning','ops-other')]:
            oid='ctx-order-'+label+'-'+uuid4().hex;ids[label]=oid
            c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,%s,%s,'ops-customer','delivery','ready','pending','cash',1000,0,1000,%s,%s)",
                      (oid,brand,branch,db.json({'items':[],'delivery_period':period,'delivery_workflow':'branch_fulfillment'}),db.json({'name':label})))
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by) VALUES('ctx-bound-task',%s,'ops-driver2','ctx-other-driver','assigned','context-other-manager')",(ids['bound_other'],))
        principal=c.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
    client=actor('manager');client.app.dependency_overrides.pop(auth.current_user,None)
    _authenticate(client,principal)
    client.headers['X-Delivery-Period']='morning'
    yield client,ids,deepcopy(principal)


def post(client,path,body=None,key=None):
    return client.post(path,json=body or {},headers={'Idempotency-Key':key or uuid4().hex})


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_context_view_and_unchanged_base_permissions(morning_manager):
    client,ids,before=morning_manager
    session=client.get('/api/session');assert session.status_code==200,session.text
    user=session.json()['user']
    assert user['branch_ids']==[B2] and user['delivery_context']['shift_id']=='ctx-duty'
    assert 'orders_view' in user['effective_permissions'] and 'orders_print' not in user['effective_permissions']
    rows=client.get('/api/orders');assert rows.status_code==200,rows.text
    assert {r['id'] for r in rows.json()['orders']}=={ids['morning']}
    with db.tx() as c:
        after=c.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
    assert after['permission_denials']==before['permission_denials'] and after['branch_ids']==before['branch_ids']
    assert employee_permissions.effective(after)==LEGACY
    del client.headers['X-Delivery-Period']
    assert client.get('/api/orders').status_code==403
    assert client.get('/api/session').json()['user']['branch_ids']==[B2,B3]


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
@pytest.mark.parametrize('label',['evening','branch3','foreign','bound_other'])
def test_context_order_idor_and_assignment_never_cross_period(morning_manager,label):
    client,ids,_=morning_manager
    assert client.get('/api/orders/'+ids[label]).status_code==404
    response=post(client,'/api/orders/'+ids[label]+'/assign',{'driver_shift_id':'ctx-driver'})
    assert response.status_code==404,response.text


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_assignment_and_tasks_are_scoped_to_own_morning_duty(morning_manager):
    client,ids,_=morning_manager;path='/api/orders/'+ids['morning']+'/assign'
    for sid in ('ctx-other-driver','ctx-evening-driver','ctx-other-branch'):
        r=post(client,path,{'driver_shift_id':sid});assert r.status_code in (403,404),r.text
    key=uuid4().hex;body={'driver_shift_id':'ctx-driver'}
    first=post(client,path,body,key);assert first.status_code==200,first.text
    assert post(client,path,body,key).status_code==200
    tasks=client.get('/api/tasks');assert tasks.status_code==200,tasks.text
    assert {r['order_id'] for r in tasks.json()['tasks']}=={ids['morning']}
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s',(ids['morning'],)).fetchone()['n']==1
    # Same idempotency key cannot replay an out-of-context driver target.
    assert post(client,path,{'driver_shift_id':'ctx-other-driver'},key).status_code in (403,404)


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_shift_lists_details_and_closure_idor_are_scoped(morning_manager):
    client,ids,_=morning_manager
    r=client.get('/api/shifts');assert r.status_code==200,r.text
    assert {s['id'] for s in r.json()['shifts']}=={'ctx-duty'}
    own=r.json()['shifts'][0]
    assert not {'opening_halalas','submission','vehicle_log','permanent_float'} & set(own)
    assert 'authorized_receivers' not in r.json() and 'available_manager_shifts' not in r.json()
    for sid in ('ctx-evening','ctx-evening-driver','ctx-other-duty','ctx-other-driver','ctx-other-branch'):
        assert client.get('/api/shifts/'+sid).status_code==403
        for action in ('closing','submit','approve','driver-close'):
            r=post(client,f'/api/shifts/{sid}/{action}',{'received_halalas':0,'confirmed':True})
            assert r.status_code==403,r.text
    assert client.get('/api/shifts/ctx-driver').status_code==403
    for path in ('/api/identity/staff','/api/reports','/api/custody','/api/admin/export','/api/manager-handovers'):
        assert client.get(path).status_code==403
    for path in ('/api/orders/'+ids['morning']+'/collect','/api/printing/orders/'+ids['morning']):
        assert post(client,path,{}).status_code==403


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
@pytest.mark.parametrize('change',['policy','branch','inactive','duty_closed','duty_evening','snapshot_parent','event_parent'])
def test_fresh_revocation_blocks_reads_and_saved_acceptance_replay(morning_manager,change):
    client,ids,_=morning_manager;path='/api/orders/'+ids['morning']+'/manager-accept';key=uuid4().hex
    with db.tx() as c:
        c.execute("UPDATE orders SET preparation_status='new' WHERE id=%s",(ids['morning'],))
        c.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,token_hash,created_by,approved_at) VALUES(%s,'kps',%s,'test',%s,'ops-owner',now())",('revocation-printer-'+uuid4().hex,B2,uuid4().hex))
    body={'version':1};first=post(client,path,body,key)
    assert first.status_code==200,first.text
    if change=='snapshot_parent':
        with pytest.raises(RaiseException,match='approved immutable revision'):
            with db.tx() as c:
                c.execute("UPDATE orders SET snapshot=jsonb_set(snapshot,'{manager_shift_id}',%s) WHERE id=%s",(db.json('ctx-other-duty'),ids['morning']))
    with db.tx() as c:
        if change=='policy':c.execute("UPDATE brands SET settings=jsonb_set(settings,'{morning_delivery,manager_user_ids}','[]') WHERE id='kps'")
        elif change=='branch':c.execute("UPDATE users SET branch_ids=%s WHERE id='ops-manager'",(db.json([B3]),))
        elif change=='inactive':c.execute("UPDATE users SET active=false WHERE id='ops-manager'")
        elif change=='duty_closed':c.execute("UPDATE shifts SET status='closed' WHERE id='ctx-duty'")
        elif change=='snapshot_parent':db.event(c,ids['morning'],'ops-manager','preparation.accepted',{'manager_shift_id':'ctx-other-duty'})
        elif change=='event_parent':db.event(c,ids['morning'],'ops-manager','preparation.accepted',{'manager_shift_id':'ctx-other-duty'})
        else:c.execute("UPDATE shifts SET delivery_window_snapshot=%s WHERE id='ctx-duty'",(db.json({'period':'evening'}),))
    assert client.get('/api/orders/'+ids['morning']).status_code in (401,403,404)
    assert post(client,path,body,key).status_code in (401,403,404)


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_foreign_role_header_and_owner_header_are_not_privilege_shortcuts(morning_manager):
    client,_,_=morning_manager
    for uid in ('ops-driver','ops-owner','ops-other'):
        with db.tx() as c:u=c.execute('SELECT * FROM users WHERE id=%s',(uid,)).fetchone()
        _authenticate(client,u)
        assert client.get('/api/session').status_code==403
    client.headers['X-Delivery-Period']='evening'
    assert client.get('/api/session').status_code==400


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
@pytest.mark.parametrize('paused',[False,True])
def test_minimal_role_cannot_close_driver_or_create_vehicle_shift(morning_manager,paused):
    client,_,_=morning_manager
    with db.tx() as c:
        c.execute("UPDATE shifts SET status='submitted',submission=%s WHERE id='ctx-driver'",(db.json({'counted_halalas':0,'handover_halalas':0,'retained_halalas':0,'expected_halalas':0,'difference_halalas':0,'terminal_total_halalas':0}),))
    if paused:
        with db.tx() as c:
            c.execute("UPDATE brands SET settings=jsonb_set(settings,'{morning_delivery,enabled}','false') WHERE id='kps'")
            c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('ctx-new-driver','new','driver','kps',%s)",(db.json([B2]),))
        denied_new=post(client,'/api/shifts',{'kind':'driver','user_id':'ctx-new-driver','branch_ids':[B2],'manager_shift_id':'ctx-duty'})
        assert denied_new.status_code==403,denied_new.text
    body={'received_halalas':0,'confirmed':True};key=uuid4().hex
    denied=post(client,'/api/shifts/ctx-driver/driver-close',body|{'confirmed':False})
    assert denied.status_code==403,denied.text
    r=post(client,'/api/shifts/ctx-driver/driver-close',body,key);assert r.status_code==403,r.text
    assert post(client,'/api/shifts/ctx-driver/driver-close',body,key).status_code==403
    with db.tx() as c:
        assert c.execute("SELECT status FROM shifts WHERE id='ctx-driver'").fetchone()['status']=='submitted'
        assert c.execute("SELECT count(*) n FROM custody_transfers WHERE from_shift_id='ctx-driver' AND status='confirmed'").fetchone()['n']==0
        assert c.execute("SELECT count(*) n FROM closure_reports WHERE shift_id='ctx-driver'").fetchone()['n']==0
