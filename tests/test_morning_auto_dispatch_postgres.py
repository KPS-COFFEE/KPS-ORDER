"""Actual accepted→dispatch→driver acceptance→preparation morning contracts."""
import base64
from concurrent.futures import ThreadPoolExecutor
import io
from uuid import uuid4

from PIL import Image
import pytest

from app import auth, db, morning_dispatch, service_hours
from test_core import _authenticate, _branch
from test_morning_delivery import at
from test_operations import ops

pytestmark=pytest.mark.postgres
B2='kps-branch-2'


@pytest.fixture
def auto_ops(ops,monkeypatch):
    actor,post,_,_=ops
    monkeypatch.setattr(service_hours,'now_riyadh',lambda:at('08:00:00'))
    with db.tx() as c:
        c.execute('UPDATE branches SET settings=settings||%s WHERE id=%s',(db.json(_branch()['settings']),B2))
        c.execute("UPDATE users SET branch_ids=%s WHERE id IN ('ops-manager','ops-driver','ops-driver2','ops-branch')",(db.json([B2]),))
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'",(db.json({'morning_delivery':{'enabled':True,'version':1,'execution_branch_ids':[B2],'manager_user_ids':['ops-manager'],'preparer_user_ids':['ops-branch']}}),))
        c.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,token_hash,created_by,approved_at) VALUES('auto-printer','kps',%s,'test','token','ops-owner',now())",(B2,))
    manager=post('manager','/api/shifts',{'kind':'manager','branch_ids':[B2],'delivery_period':'morning'})['shift']
    driver=post('driver','/api/shifts',{'kind':'driver','manager_shift_id':manager['id']})['shift']
    client=actor('owner');client.app.dependency_overrides.pop(auth.current_user,None)
    sessions={}
    for name in ('owner','manager','driver','driver2','branch'):
        with db.tx() as c:user=c.execute('SELECT * FROM users WHERE id=%s',('ops-'+name,)).fetchone()
        _authenticate(client,user)
        sessions[name]=(dict(client.cookies),dict(client.headers))
    def login(name,context=False):
        cookies,headers=sessions[name]
        client.cookies.clear();client.cookies.update(cookies)
        client.headers.clear();client.headers.update(headers)
        if context:client.headers['X-Delivery-Period']='morning'
        return client
    def request(name,path,body=None,*,context=False,key=None):
        return login(name,context).post(path,json=body or {},headers={'Idempotency-Key':key or uuid4().hex})
    return login,request,manager,driver


def new_order(*,period='morning',branch=B2,status='new',snapshot=None):
    oid='auto-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) "
                  "VALUES(%s,'kps',%s,'ops-customer','delivery',%s,'pending','cash',1000,0,1000,%s,'{}')",
                  (oid,branch,status,db.json({'delivery_period':period,'delivery_workflow':'branch_fulfillment','items':[]}|(snapshot or {}))))
    return oid


def version(oid):
    with db.tx() as c:return c.execute('SELECT version FROM orders WHERE id=%s',(oid,)).fetchone()['version']


def prepare(request,oid,status,**kwargs):
    return request('branch',f'/api/orders/{oid}/preparation',{'status':status,'version':version(oid)},**kwargs)


def existing_load(driver,count):
    with db.tx() as c:
        for _ in range(count):
            oid='busy-'+uuid4().hex;tid='busy-task-'+uuid4().hex
            c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps',%s,'ops-customer','delivery','preparing','accepted','cash',1000,0,1000,%s,'{}')",(oid,B2,db.json({'delivery_period':'morning','items':[]})))
            c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,accepted_at) VALUES(%s,%s,'ops-driver',%s,'accepted','ops-manager',now())",(tid,oid,driver['id']))


def photo(request,tid,stage):
    image=io.BytesIO();Image.new('RGB',(12,12),'white').save(image,'PNG')
    result=request('driver',f'/api/tasks/{tid}/photos',{'stage':stage,'data_base64':base64.b64encode(image.getvalue()).decode(),'content_type':'image/png'})
    assert result.status_code==200,result.text
    return result.json()['id']


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_authenticated_cashier_accept_busy_driver_accept_then_prepare_and_deliver(auto_ops):
    login,request,_,driver=auto_ops
    existing_load(driver,1)
    oid=new_order();accepted=prepare(request,oid,'accepted')
    assert accepted.status_code==200,accepted.text
    assert accepted.json()['printing']['status']=='queued'
    assert accepted.json()['auto_assignment']['status']=='assigned'
    tid=accepted.json()['auto_assignment']['task']['id']
    assert accepted.json()['order']['preparation_status']=='accepted'
    assert not accepted.json()['order']['morning_driver_accepted']
    assert prepare(request,oid,'preparing').status_code==409
    assert prepare(request,oid,'ready').status_code==409
    assert request('manager',f'/api/tasks/{tid}/transition',{'status':'accepted'}).status_code==403
    actual=request('driver',f'/api/tasks/{tid}/transition',{'status':'accepted'})
    assert actual.status_code==200,actual.text
    assert login('branch').get('/api/orders/'+oid).json()['order']['morning_driver_accepted']
    early=request('driver',f'/api/tasks/{tid}/transition',{'status':'picked_up'})
    assert early.status_code==409 and early.json()['detail']['code']=='MORNING_PREPARATION_REQUIRED',early.text
    preparing=prepare(request,oid,'preparing');assert preparing.status_code==200,preparing.text
    assert request('driver',f'/api/tasks/{tid}/transition',{'status':'picked_up'}).status_code==409
    ready=prepare(request,oid,'ready');assert ready.status_code==200,ready.text
    picked=request('driver',f'/api/tasks/{tid}/transition',{'status':'picked_up','photo_id':photo(request,tid,'picked_up')})
    assert picked.status_code==200,picked.text
    paid=request('driver',f'/api/orders/{oid}/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':1000})
    assert paid.status_code==200,paid.text
    delivered=request('driver',f'/api/tasks/{tid}/transition',{'status':'delivered','photo_id':photo(request,tid,'delivered'),'proof':'Synthetic authenticated delivery proof'})
    assert delivered.status_code==200,delivered.text
    with db.tx() as c:
        task=c.execute('SELECT * FROM driver_tasks WHERE id=%s',(tid,)).fetchone()
        assert task['accepted_at'] and task['picked_up_at'] and task['delivered_at']
        assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['n']==1


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
@pytest.mark.parametrize('load',[1,2,3])
def test_busy_driver_is_eligible_until_morning_capacity_and_retries_when_space_frees(auto_ops,load):
    _,request,_,driver=auto_ops
    # A legacy evening/manual dispatch limit must not disable morning stacking.
    with db.tx() as c:
        c.execute("UPDATE app_settings SET settings=settings||%s WHERE brand_id='kps'",(db.json({'dispatch':{'mode':'MANUAL','max_active_tasks':1}}),))
    existing_load(driver,load)
    oid=new_order();result=prepare(request,oid,'accepted')
    assert result.status_code==200,result.text
    assert result.json()['auto_assignment']['status']==('assigned' if load<3 else 'manager_required')
    if load==3:
        assert morning_dispatch.tick()==0
        with db.tx() as c:
            c.execute("UPDATE driver_tasks SET status='delivered' WHERE id=(SELECT id FROM driver_tasks WHERE shift_id=%s ORDER BY id LIMIT 1)",(driver['id'],))
        assert morning_dispatch.tick()==1
        assert morning_dispatch.tick()==0
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['n']==1


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_no_available_driver_keeps_cashier_acceptance_and_print_then_retries(auto_ops):
    _,request,_,driver=auto_ops
    with db.tx() as c:c.execute('UPDATE shifts SET available=false WHERE id=%s',(driver['id'],))
    oid=new_order();result=prepare(request,oid,'accepted')
    assert result.status_code==200 and result.json()['auto_assignment']['status']=='manager_required',result.text
    with db.tx() as c:
        row=c.execute('SELECT preparation_status,delivery_status FROM orders WHERE id=%s',(oid,)).fetchone()
        assert row=={'preparation_status':'accepted','delivery_status':'pending'}
        assert c.execute('SELECT count(*) n FROM branch_print_jobs WHERE order_id=%s',(oid,)).fetchone()['n']==1
        c.execute('UPDATE shifts SET available=true WHERE id=%s',(driver['id'],))
    assert morning_dispatch.tick()==1


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
@pytest.mark.parametrize('change',['evening','branch3','new','simulated','policy_off','driver_period','manager_widened','driver_widened','manager_revoked'])
def test_auto_dispatch_never_escapes_morning_launch_scope(auto_ops,change):
    _,_,manager,driver=auto_ops
    oid=new_order(status='accepted',period='evening' if change=='evening' else 'morning',
                  branch='kps-branch-3' if change=='branch3' else B2,
                  snapshot={'simulated':True} if change=='simulated' else {})
    with db.tx() as c:
        if change=='new':c.execute("UPDATE orders SET preparation_status='new' WHERE id=%s",(oid,))
        elif change=='policy_off':c.execute("UPDATE brands SET settings=jsonb_set(settings,'{morning_delivery,enabled}','false') WHERE id='kps'")
        elif change=='driver_period':c.execute('UPDATE shifts SET delivery_window_snapshot=%s WHERE id=%s',(db.json({'period':'evening'}),driver['id']))
        elif change in {'manager_widened','driver_widened'}:
            c.execute('UPDATE shifts SET branch_ids=%s WHERE id=%s',(db.json([B2,'kps-branch-3']),manager['id'] if change=='manager_widened' else driver['id']))
        elif change=='manager_revoked':c.execute("UPDATE brands SET settings=jsonb_set(settings,'{morning_delivery,manager_user_ids}','[]') WHERE id='kps'")
    assert morning_dispatch.tick()==0
    with db.tx() as c:assert not c.execute('SELECT 1 FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_manual_assignment_and_retry_concurrency_never_duplicate_task(auto_ops):
    _,_,_,driver=auto_ops
    oid=new_order(status='accepted')
    with db.tx() as c:principal=c.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
    def attempt(index):
        from app import operations
        from fastapi import HTTPException
        with db.tx() as c:
            morning_dispatch.lock_operations(c)
            if index:return morning_dispatch.try_assign(c,oid)['status']
            try:
                operations.assign_in_transaction(c,principal,oid,operations.Assignment(driver_shift_id=driver['id']))
                return 'assigned'
            except HTTPException as error:
                assert error.status_code==409
                return 'already_assigned'
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
    assert results.count('assigned')==1,results
    with db.tx() as c:assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['n']==1


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_least_loaded_eligible_driver_is_selected_without_requiring_empty_pool(auto_ops):
    _,request,manager,driver=auto_ops
    from app.shift_vehicle import CHECKS
    opened=request('driver2','/api/shifts',{'kind':'driver','manager_shift_id':manager['id'],
        'vehicle_inspection':{'odometer_km':1000,'fuel_percent':100,'checks':{key:'ok' for key in CHECKS},'notes':''}})
    assert opened.status_code==200,opened.text
    existing_load(driver,1)
    oid=new_order();result=prepare(request,oid,'accepted')
    assert result.status_code==200,result.text
    assert result.json()['auto_assignment']['task']['driver_id']=='ops-driver2'


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_manager_whatsapp_order_stays_new_until_cashier_acceptance(auto_ops):
    login,request,_,_=auto_ops
    with db.tx() as c:
        c.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES('auto-coffee','kps','test','test',3000,%s)",(db.json({'source_verified':True,'enabled':True,'option_groups':[]}),))
    body={'brand_id':'kps','branch_id':B2,'service':'delivery','payment_method':'cash','items':[{'product_id':'auto-coffee','quantity':1}],
          'customer':{'name':'WhatsApp test','phone':'+966512345678','address':'test address','neighborhood':'حي اختبار','location':{'lat':28.4,'lng':45.9}}}
    created=request('manager','/api/orders/delivery-center',body,context=True)
    assert created.status_code==200,created.text
    oid=created.json()['order']['id']
    assert created.json()['order']['preparation_status']=='new' and created.json()['order']['delivery_status']=='pending'
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()
        assert not c.execute('SELECT 1 FROM branch_print_jobs WHERE order_id=%s',(oid,)).fetchone()
    assert morning_dispatch.tick()==0
    accepted=prepare(request,oid,'accepted')
    assert accepted.status_code==200 and accepted.json()['auto_assignment']['status']=='assigned',accepted.text
