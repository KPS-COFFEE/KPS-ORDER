"""V5 exact policy boundaries and real PostgreSQL operation isolation/persistence."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import math
import pytest
from fastapi import HTTPException
from app import admin, auth, catalog, db, performance, shift_vehicle
from test_operations import begin_driver_closure


def authorize_and_begin_closure(call, driver):
    def post(actor, path, body=None, status=200):
        result = call(actor, 'POST', path, body)
        assert result.status_code == status, result.text
        return result.json()
    return begin_driver_closure(post, driver)


def synthetic_vehicle_inspection(odometer_km=1000):
    return {'odometer_km': odometer_km, 'fuel_percent': 100,
            'checks': {item: 'ok' for item in shift_vehicle.CHECKS}, 'notes': ''}


@pytest.mark.parametrize('limits',[(1200,1800),(600,900)])
@pytest.mark.parametrize('point,expected',[('before_t1','excellent'),('t1','excellent'),('after_t1','good'),('before_t2','good'),('t2','good'),('after_t2','late')])
def test_exact_threshold_seconds(limits,point,expected):
    t1,t2=limits
    values={'before_t1':t1-1,'t1':t1,'after_t1':t1+1,'before_t2':t2-1,'t2':t2,'after_t2':t2+1}
    assert performance.classify(values[point],{'t1_seconds':t1,'t2_seconds':t2,'version':9})['classification']==expected
    assert performance.classify(t1+0.1,{'t1_seconds':t1,'t2_seconds':t2})['classification']=='good'


def test_missing_policy_and_unstarted_stages_never_fake_zero_or_good():
    assert performance.classify(0,None)['classification']=='unconfigured'
    assert performance.classify(None,None,'not_started')['classification']=='not_started'
    assert performance.classify(200,None,'cancelled')['classification']=='excluded'
    now=datetime.now(timezone.utc)
    assert performance._phase(None,None,now)=={'seconds':None,'state':'not_started'}
    assert performance._phase(now,None,now+timedelta(seconds=1))=={'seconds':1,'state':'running'}
    assert performance._phase(now,now-timedelta(seconds=1),now)['state']=='missing'
    assert performance._phase(now,None,now,terminal=True)['seconds'] is None


@pytest.mark.postgres
def test_dashboard_uses_frozen_province_policy_and_rejected_orders_are_excluded(v5):
    actor,call,body,_=v5
    policy={'province_id':'TEST-dashboard','province_name':'TEST province','branch_ids':['v5-a','v5-b'],
            'metric':'delivery','t1_seconds':1200,'t2_seconds':1800,'effective_at':'2026-01-01T00:00:00+03:00'}
    assert call('manager','POST','/api/performance/policies',policy).status_code==200
    manager=call('manager','POST','/api/shifts',{'kind':'manager','branch_ids':['v5-a','v5-b']}).json()['shift']['id']
    driver=call('driver','POST','/api/shifts',{'kind':'driver','branch_ids':['v5-a','v5-b'],'manager_shift_id':manager,'vehicle_inspection':synthetic_vehicle_inspection()}).json()['shift']['id']
    original=call('customer','POST','/api/orders',body('delivery')).json()['order']
    oid=original['id']
    task=call('manager','POST','/api/orders/'+oid+'/assign',{'driver_shift_id':driver}).json()['task']['id']
    start=datetime.now(timezone.utc)-timedelta(hours=2)
    with db.tx() as c:
        c.execute("UPDATE driver_tasks SET status='picked_up' WHERE id=%s",(task,))
        c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    collected=call('driver','POST','/api/orders/'+oid+'/collect',
                   {'method':'cash','amount_halalas':1200,'tendered_halalas':1200})
    assert collected.status_code==200,collected.text
    with db.tx() as c:
        c.execute("UPDATE orders SET created_at=%s,completed_at=%s,delivery_status='delivered' WHERE id=%s",
                  (start,start+timedelta(seconds=2000),oid))
        c.execute("UPDATE driver_tasks SET status='delivered',picked_up_at=%s,delivered_at=%s WHERE id=%s",
                  (start+timedelta(seconds=199),start+timedelta(seconds=2000),task))
    changed=call('manager','POST','/api/performance/policies',policy|{'t1_seconds':2400,'t2_seconds':3000})
    assert changed.status_code==200,changed.text
    rejected=call('customer','POST','/api/orders',body('delivery')).json()['order']
    with db.tx() as c:
        c.execute("UPDATE orders SET preparation_status='rejected' WHERE id=%s",(rejected['id'],))
    detail=actor('manager').get('/api/orders/'+oid).json()['order']
    assert detail['performance']['classification']=='late' and detail['performance']['policy_version']==1
    reject_detail=actor('manager').get('/api/orders/'+rejected['id']).json()['order']
    assert reject_detail['performance']['classification']=='excluded'
    lo=(start-timedelta(seconds=1)).isoformat();hi=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
    dash=actor('manager').get('/api/dashboard',params={'start':lo,'end':hi}).json()
    frozen=dash['delivery_performance']
    assert frozen['policy_source']=='order_snapshot'
    assert frozen['completed_late']==1 and frozen['completed']['late']==1
    assert frozen['classifications']['excluded']==1
    assert frozen['valid']==1 and frozen['excluded']==1 and frozen['average_seconds']==1801
    assert dash['delivery_time']['delayed']==1 and dash['delivery_time']['valid']==1
    assert authorize_and_begin_closure(call, driver)['status'] == 'closing'
    assert call('driver','POST','/api/shifts/'+driver+'/submit',{'terminal_total_halalas':0,'counted_halalas':1200,'handover_halalas':1200,'vehicle_inspection':synthetic_vehicle_inspection(1025),'handover':{'recipient_user_id':'v5-manager','to_shift_id':manager,'reference':'TEST-policy-bag'}}).status_code==200
    with db.tx() as c:transfer=c.execute('SELECT id FROM custody_transfers WHERE from_shift_id=%s AND closure',(driver,)).fetchone()['id']
    assert call('manager','POST','/api/custody/'+transfer+'/confirm').status_code==200
    approved=call('manager','POST','/api/shifts/'+driver+'/approve')
    assert approved.status_code==200,approved.text
    report=actor('manager').get('/api/reports/'+approved.json()['report_id']).json()['report']
    saved=next(row for row in report['snapshot']['orders'] if row['id']==oid)
    assert saved['performance']==detail['performance']
    assert saved['timings']['delivery']['seconds']==frozen['average_seconds']


@pytest.mark.parametrize('url',['http://www.google.com/maps','https://evil.invalid/maps?q=1,2','https://www.google.com.evil.invalid/maps?q=1,2','https://www.google.com@127.0.0.1/maps','https://www.google.com:8080/maps','https://maps.app.goo.gl/../secret','https://127.0.0.1/admin','file:///etc/passwd'])
def test_maps_never_accept_arbitrary_server_fetch_targets(url):
    assert catalog.safe_map_url(url) is None


def test_verified_directions_are_distinct_for_original_branches_and_need_no_origin():
    one={'settings':{'location':{'lat':28.45,'lng':45.96},'location_verified':True}}
    nine={'settings':{'location':{'lat':28.46,'lng':45.97},'location_verified':True}}
    assert catalog.directions_url(one)!=catalog.directions_url(nine)
    assert 'origin=' not in catalog.directions_url(one)
    assert catalog.directions_url({'settings':{'location':{'lat':28,'lng':45}}}) is None
    assert catalog.direct_distance({'lat':28,'lng':45},{'lat':28,'lng':45})==0
    assert catalog.direct_distance({'lat':math.nan,'lng':45},{'lat':28,'lng':45}) is None
    with pytest.raises(HTTPException):admin.coordinate({'lat':28,'lng':math.inf})


@pytest.fixture
def v5(client):
    hours=[{'days':list(range(7)),'start':'00:00','end':'00:00'}]
    settings={'pickup_enabled':True,'car_enabled':True,'delivery_enabled':True,'ordering_verified':True,
              'opening_hours':hours,'delivery_hours':hours,'payment_methods':['cash','card'],
              'location':{'lat':28,'lng':45},'location_verified':True,'coverage':{'type':'radius','center':{'lat':28,'lng':45},'radius_meters':50000},'delivery_fee_halalas':200}
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name) VALUES('v5-test','TEST ONLY')")
        c.execute("INSERT INTO app_settings(brand_id,settings) VALUES('v5-test','{}')")
        for bid,no,city,lat in [('v5-a',1,'حفر الباطن',28),('v5-b',9,'حفر الباطن',28.1),('v5-c',2,'الدمام',28.01)]:
            cfg=settings|{'location':{'lat':lat,'lng':45},'delivery_enabled':city=='حفر الباطن'}
            c.execute('INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES(%s,%s,%s,%s,%s,%s,%s)',(bid,no,'TEST:'+bid,'v5-test','TEST '+bid,city,db.json(cfg)))
        for who,role,scope in [('owner','owner',[]),('manager','delivery_manager',['v5-a','v5-b']),('limited','delivery_manager',['v5-a']),('branch','branch_manager',['v5-a']),('driver','driver',['v5-a','v5-b']),('customer','customer',[])]:
            c.execute('INSERT INTO users(id,display_name,role,brand_id,branch_ids,phone) VALUES(%s,%s,%s,%s,%s,%s)',('v5-'+who,'TEST '+who,role,'v5-test',db.json(scope),'+966500000000'))
        c.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES('v5-product','v5-test','TEST coffee','TEST',1000,%s)",(db.json({'enabled':True,'source_verified':True}),))
    def actor(who):
        with db.tx() as c:user=c.execute('SELECT * FROM users WHERE id=%s',('v5-'+who,)).fetchone()
        client.app.dependency_overrides[auth.current_user]=lambda:user
        return client
    def call(who,method,path,body=None,key=None):
        return actor(who).request(method,path,json={} if body is None and method not in ('GET','HEAD') else body,headers={'Idempotency-Key':key or uuid4().hex})
    def body(service='pickup',branch='v5-a'):
        return {'brand_id':'v5-test','branch_id':branch,'service':service,'payment_method':'cash',
                'items':[{'product_id':'v5-product','quantity':1}],
                'customer':{'name':'TEST customer','phone':'+966500000000','neighborhood':'TEST حي','address':'TEST address','location':{'lat':28,'lng':45},'car':{'plate':'TEST','color':'black'}}}
    yield actor,call,body,settings
    client.app.dependency_overrides.clear()


@pytest.mark.postgres
def test_brand_pickup_disable_delivery_only_and_old_cart_recheck(v5):
    actor,call,body,_=v5
    original=call('customer','POST','/api/orders',body());assert original.status_code==200,original.text
    order=original.json()['order']
    changed=call('owner','PATCH','/api/admin/brand-services',{'services':{'pickup':False},'reason':'TEST disable pickup'});assert changed.status_code==200,changed.text
    assert actor('customer').post('/api/quote',json=body()).json()['detail']['code']=='BRAND_SERVICE_DISABLED'
    assert call('customer','POST','/api/orders',body()).status_code==409
    assert call('owner','PATCH','/api/admin/branches/v5-a',{'settings':{'pickup_enabled':True}}).status_code==200
    assert actor('customer').post('/api/quote',json=body()).status_code==409
    preview=call('owner','POST','/api/admin/delivery-only',{'branch_ids':['v5-c'],'confirm':False})
    assert preview.status_code==200 and preview.json()['warnings'] and preview.json()['saved'] is False
    save=call('owner','POST','/api/admin/delivery-only',{'branch_ids':['v5-c'],'confirm':True,'reason':'TEST delivery only'})
    assert save.status_code==200 and save.json()['delivery_will_be_enabled'] is False
    with db.tx() as c:
        branch=c.execute("SELECT * FROM branches WHERE id='v5-c'").fetchone();after=c.execute('SELECT * FROM orders WHERE id=%s',(order['id'],)).fetchone()
    assert branch['settings']['delivery_enabled'] is False and branch['settings']['pickup_enabled'] is False
    assert after['snapshot']==order['snapshot'] and after['total_halalas']==order['total_halalas']
    assert actor('owner').get('/api/orders/'+order['id']).status_code==200


@pytest.mark.postgres
def test_product_lifecycle_new_admin_product_old_cart_and_original_snapshots(v5):
    actor,call,body,_=v5
    r=call('customer','POST','/api/orders',body());assert r.status_code==200,r.text
    historic=r.json()['order']
    pause=call('owner','PATCH','/api/admin/products/v5-product',{'settings':{'lifecycle':'paused'}});assert pause.status_code==200,pause.text
    assert actor('customer').post('/api/quote',json=body()).json()['detail']['code']=='PRODUCT_UNAVAILABLE'
    assert call('customer','POST','/api/orders',body()).status_code==409
    created=call('owner','POST','/api/admin/products',{'name':'TEST new','category':'TEST','price_halalas':750,'settings':{}})
    assert created.status_code==200 and created.json()['product']['settings']['lifecycle']=='draft'
    pid=created.json()['product']['id']
    public=actor('customer').get('/api/catalog?brand_id=v5-test').json()
    assert pid not in {p['id'] for p in public['products']}
    assert call('owner','PATCH','/api/admin/products/'+pid,{'settings':{'lifecycle':'active'}}).status_code==422
    assert call('owner','PATCH','/api/admin/products/'+pid,{'settings':{'lifecycle':'active','source_verified':True}}).status_code==200
    db.migrate()
    with db.tx() as c:after=c.execute('SELECT * FROM orders WHERE id=%s',(historic['id'],)).fetchone()
    assert after['snapshot']==historic['snapshot']


@pytest.mark.postgres
def test_nearest_eligible_scoped_verified_and_missing_product_exclusion(v5):
    actor,call,_,_=v5
    payload={'location':{'lat':28.02,'lng':45},'product_ids':['v5-product']}
    result=call('manager','POST','/api/operations/nearest-branches',payload);assert result.status_code==200,result.text
    rows=result.json()['branches'];assert [r['id'] for r in rows]==['v5-a','v5-b']
    assert all('ليست مسافة الطريق' in r['distance_label'] for r in rows)
    assert result.json()['routing_provider'] is None
    limited=call('limited','POST','/api/operations/nearest-branches',payload).json()
    assert [r['id'] for r in limited['branches']]==['v5-a']
    with db.tx() as c:
        c.execute("UPDATE branches SET settings=settings || '{\"location_verified\":false,\"ordering_verified\":false}'::jsonb WHERE id='v5-a'")
    result=call('manager','POST','/api/operations/nearest-branches',payload).json()
    assert [r['id'] for r in result['branches']]==['v5-b'] and result['excluded'][0]['direct_distance_meters'] is None
    assert call('manager','POST','/api/operations/nearest-branches',payload|{'product_ids':['kps-33']}).status_code==422


@pytest.mark.postgres
def test_policy_version_snapshots_boundaries_scope_and_history(v5):
    actor,call,body,_=v5
    policy={'province_id':'TEST-hafar','province_name':'TEST province','branch_ids':['v5-a','v5-b'],'metric':'delivery',
            't1_seconds':1200,'t2_seconds':1800,'effective_at':'2026-01-01T00:00:00+03:00'}
    assert call('limited','POST','/api/performance/policies',policy).status_code==404
    assert call('manager','POST','/api/performance/policies',policy|{'t2_seconds':1200}).status_code==422
    saved=call('manager','POST','/api/performance/policies',policy,key='TEST-policy-first');assert saved.status_code==200,saved.text
    assert call('manager','POST','/api/performance/policies',policy,key='TEST-policy-first').json()==saved.json()
    order=call('customer','POST','/api/orders',body()).json()['order']
    with db.tx() as c:old=c.execute('SELECT performance_snapshot FROM orders WHERE id=%s',(order['id'],)).fetchone()['performance_snapshot']
    assert old['policies']['delivery']['version']==1
    new=call('manager','POST','/api/performance/policies',policy|{'t1_seconds':600,'t2_seconds':900});assert new.status_code==200,new.text
    future=call('customer','POST','/api/orders',body()).json()['order']
    with db.tx() as c:new_snapshot=c.execute('SELECT performance_snapshot FROM orders WHERE id=%s',(future['id'],)).fetchone()['performance_snapshot']
    assert new_snapshot['policies']['delivery']['version']==2
    with db.tx() as c:
        assert c.execute('SELECT performance_snapshot FROM orders WHERE id=%s',(order['id'],)).fetchone()['performance_snapshot']==old
    with pytest.raises(Exception):
        with db.tx() as c:c.execute("UPDATE orders SET performance_snapshot='{}' WHERE id=%s",(order['id'],))
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[\"v5-a\"]' WHERE id='v5-manager'")
    assert call('manager','POST','/api/performance/policies',policy,key='TEST-policy-first').status_code==404


@pytest.mark.postgres
def test_phases_use_events_not_order_age_and_completed_values_freeze(v5):
    actor,call,body,_=v5
    manager=call('manager','POST','/api/shifts',{'kind':'manager','branch_ids':['v5-a','v5-b']}).json()['shift']['id']
    driver=call('driver','POST','/api/shifts',{'kind':'driver','branch_ids':['v5-a','v5-b'],'manager_shift_id':manager,'vehicle_inspection':synthetic_vehicle_inspection()}).json()['shift']['id']
    r=call('customer','POST','/api/orders',body('delivery'));assert r.status_code==200,r.text
    order=r.json()['order'];oid=order['id']
    live=actor('manager').get('/api/orders/'+oid).json()['order'];assert live['timings']['delivery']['seconds'] is None
    task=call('manager','POST','/api/orders/'+oid+'/assign',{'driver_shift_id':driver}).json()['task']['id']
    start=datetime.now(timezone.utc)-timedelta(hours=2)
    with db.tx() as c:
        c.execute('UPDATE orders SET created_at=%s,completed_at=%s,delivery_status=%s WHERE id=%s',(start,start+timedelta(seconds=2500),'delivered',oid))
        db.event(c,oid,'v5-manager','preparation.accepted');db.event(c,oid,'v5-manager','preparation.ready')
        c.execute("UPDATE order_events SET created_at=%s WHERE order_id=%s AND event_type='preparation.accepted'",(start+timedelta(seconds=100),oid))
        c.execute("UPDATE order_events SET created_at=%s WHERE order_id=%s AND event_type='preparation.ready'",(start+timedelta(seconds=700),oid))
        c.execute('UPDATE driver_tasks SET picked_up_at=%s,delivered_at=%s,status=%s WHERE id=%s',(start+timedelta(seconds=900),start+timedelta(seconds=2500),'delivered',task))
        rows=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchall();performance.enrich_orders(c,rows,now=start+timedelta(days=1));times=rows[0]['timings']
    assert {key:value['seconds'] for key,value in times.items()}=={'acceptance_wait':100,'preparation':600,'driver_wait':200,'delivery':1600,'total':2500}
    assert all(value['state']=='completed' for value in times.values())


@pytest.mark.postgres
def test_concurrent_numbers_cancel_and_filters_do_not_renumber(v5):
    actor,call,body,_=v5
    def insert(index):
        with db.tx() as c:
            return c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'v5-test','v5-a','v5-customer','pickup','not_applicable','cash',1000,0,1000,'{}',%s) RETURNING order_no",('TEST-order-'+uuid4().hex,db.json({'neighborhood':'TEST '+str(index)}))).fetchone()['order_no']
    with ThreadPoolExecutor(max_workers=6) as pool:numbers=list(pool.map(insert,range(24)))
    assert len(set(numbers))==24
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='cancelled' WHERE order_no=%s",(numbers[0],))
    filtered=actor('owner').get('/api/orders',params={'search':str(numbers[0]),'sort':'oldest'}).json()['orders']
    assert len(filtered)==1 and filtered[0]['order_no']==numbers[0]
    assert actor('manager').get('/api/orders',params={'status':'active','neighborhood':'TEST'}).json()['orders']==[]
    live=actor('branch').get('/api/orders',params={'status':'active','limit':5,'neighborhood':'TEST'}).json()
    assert len(live['orders'])==5 and live['next_offset']==5
    assert all(r['preparation_status']!='cancelled' for r in live['orders'])


@pytest.mark.postgres
def test_multi_branch_one_shift_revocation_preserves_task_custody_and_draft(v5):
    actor,call,body,_=v5
    manager=call('manager','POST','/api/shifts',{'kind':'manager','branch_ids':['v5-a','v5-b']}).json()['shift']['id']
    driver=call('driver','POST','/api/shifts',{'kind':'driver','branch_ids':['v5-a','v5-b'],'manager_shift_id':manager,'vehicle_inspection':synthetic_vehicle_inspection()}).json()['shift']['id']
    assert call('driver','POST','/api/shifts',{'kind':'driver','branch_ids':['v5-a','v5-b'],'manager_shift_id':manager}).status_code==409
    order=call('customer','POST','/api/orders',body('delivery')).json()['order']
    assigned=call('manager','POST','/api/orders/'+order['id']+'/assign',{'driver_shift_id':driver},key='TEST-assignment-one');assert assigned.status_code==200,assigned.text
    blocked=call('owner','PATCH','/api/admin/users/v5-driver',{'branch_ids':['v5-b']});assert blocked.status_code==409,blocked.text
    assert call('owner','PATCH','/api/admin/users/v5-driver',{'active':False}).status_code==409
    assert call('owner','PATCH','/api/admin/users/v5-driver',{'role':'owner'}).status_code==403
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_tasks WHERE driver_id=%s',('v5-driver',)).fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM shifts WHERE user_id='v5-driver' AND status<>'closed'").fetchone()['n']==1
    # A driver cannot invent retained cash without an approved permanent float.
    rejected=call('driver','POST','/api/shifts/'+driver+'/draft',{'counted_halalas':100,'handover_halalas':80,'notes':'TEST draft'})
    assert rejected.status_code==422,rejected.text
    draft=call('driver','POST','/api/shifts/'+driver+'/draft',{'counted_halalas':100,'handover_halalas':100,'notes':'TEST draft'});assert draft.status_code==200,draft.text
    assert draft.json()['draft']['retained_halalas']==0 and 'difference_halalas' not in draft.json()['draft']
    with db.tx() as c:assert c.execute('SELECT draft FROM closure_drafts WHERE shift_id=%s',(driver,)).fetchone()['draft']['difference_halalas']==100
    db.migrate()
    assert actor('driver').get('/api/shifts/'+driver+'/draft').json()['draft']['notes']=='TEST draft'
    assert call('manager','POST','/api/shifts/'+driver+'/draft',{'counted_halalas':0,'handover_halalas':0}).status_code==403
    assert call('manager','POST','/api/shifts/'+manager+'/closing').status_code==200
    zero=call('manager','POST','/api/shifts/'+manager+'/submit',{'counted_halalas':0,'handover_halalas':0})
    assert zero.status_code==409


@pytest.mark.postgres
def test_chat_fallback_can_claim_after_previous_assignee_scope_is_withdrawn(v5):
    actor,call,body,_=v5
    order=call('customer','POST','/api/orders',body()).json()['order']
    cid=call('customer','POST','/api/conversations',{'order_id':order['id']}).json()['conversation']['id']
    assert call('branch','POST','/api/conversations/'+cid+'/claim').status_code==200
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('v5-backup-branch','TEST fallback','branch_manager','v5-test',%s)",
                  (db.json(['v5-a']),))
        c.execute("UPDATE users SET branch_ids='[]' WHERE id='v5-branch'")
        backup=c.execute("SELECT * FROM users WHERE id='v5-backup-branch'").fetchone()
    result=actor('owner')
    result.app.dependency_overrides[auth.current_user]=lambda:backup
    reclaimed=result.post('/api/conversations/'+cid+'/claim',json={},headers={'Idempotency-Key':uuid4().hex})
    assert reclaimed.status_code==200,reclaimed.text
    assert reclaimed.json()['assignee_id']=='v5-backup-branch'
    assert actor('branch').get('/api/conversations/'+cid+'/messages').status_code==404
    assert actor('customer').get('/api/conversations/'+cid+'/messages').status_code==200


@pytest.mark.parametrize('url',['https://www.google.com/maps/../url?q=https://evil.invalid','https://www.google.com/maps/%2e%2e/url?q=https://evil.invalid','https://goo.gl/not-a-maps-link'])
def test_map_open_redirect_and_path_traversal_are_not_approved(url):
    assert catalog.safe_map_url(url) is None


def test_map_viewport_is_not_accepted_as_destination_and_precise_pin_parses():
    with pytest.raises(HTTPException):
        catalog.parse_map_url(catalog.MapURLBody(url='https://www.google.com/maps/@28.4,45.9,13z'))
    result=catalog.parse_map_url(catalog.MapURLBody(url='https://www.google.com/maps/dir/?api=1&destination=28.4,45.9'))
    assert result['location']=={'lat':28.4,'lng':45.9} and result['location_is_device_verified'] is False


@pytest.mark.postgres
def test_zero_task_driver_shift_still_blocks_manager_until_approved(v5):
    _,call,_,_=v5
    manager=call('manager','POST','/api/shifts',{'kind':'manager','branch_ids':['v5-a','v5-b']}).json()['shift']['id']
    driver=call('driver','POST','/api/shifts',{'kind':'driver','branch_ids':['v5-a','v5-b'],'manager_shift_id':manager,'vehicle_inspection':synthetic_vehicle_inspection()}).json()['shift']['id']
    assert call('manager','POST','/api/shifts/'+manager+'/closing').status_code==200
    assert call('manager','POST','/api/shifts/'+manager+'/submit',{'counted_halalas':0,'handover_halalas':0}).status_code==409
    assert authorize_and_begin_closure(call, driver)['status'] == 'closing'
    assert call('driver','POST','/api/shifts/'+driver+'/submit',{'terminal_total_halalas':0,'counted_halalas':0,'handover_halalas':0,'vehicle_inspection':synthetic_vehicle_inspection(),'handover':{'recipient_user_id':'v5-manager','to_shift_id':manager,'reference':'TEST-empty'}}).status_code==200
    with db.tx() as c:tid=c.execute('SELECT id FROM custody_transfers WHERE from_shift_id=%s AND closure',(driver,)).fetchone()['id']
    assert call('manager','POST','/api/custody/'+tid+'/confirm').status_code==200
    approved=call('manager','POST','/api/shifts/'+driver+'/approve');assert approved.status_code==200,approved.text
    assert call('manager','POST','/api/shifts/'+manager+'/submit',{'counted_halalas':0,'handover_halalas':0}).status_code==200
    with db.tx() as c:
        snap=c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',(approved.json()['report_id'],)).fetchone()['snapshot']
    assert snap['summary']['unique_orders']==0 and snap['summary']['net_collections_halalas']==0


@pytest.mark.postgres
def test_guest_risk_markers_rate_limit_and_operational_call_do_not_verify_phone(v5):
    actor,call,body,_=v5
    with db.tx() as c:
        c.execute("UPDATE users SET identity_kind='guest' WHERE id='v5-customer'")
        c.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled) VALUES('v5-test','guest','active',true)")
    assert call('owner','PATCH','/api/admin/settings',{'guest_operational_confirmation_above_halalas':1000}).status_code==200
    first=call('customer','POST','/api/orders',body(),key='TEST-guest-first');assert first.status_code==200,first.text
    assert 'risk_alert' not in first.json()['order']['customer']
    second=call('customer','POST','/api/orders',body());assert second.status_code==200,second.text
    oid=second.json()['order']['id']
    staff=actor('owner').get('/api/orders/'+oid).json()['order']
    assert staff['customer']['risk_alert'] is True and staff['customer']['requires_operational_confirmation'] is True
    assert staff['customer']['phone_verified'] is False
    assert call('owner','POST','/api/orders/'+oid+'/preparation',{'status':'accepted','version':staff['version']}).status_code==409
    assert call('owner','POST','/api/orders/'+oid+'/operational-confirmation',{'result':'reached','reason':'TEST actual phone call'}).json()['phone_verified'] is False
    # Contact confirmation cannot itself release a suspected duplicate. The
    # owner explicitly confirms this second order without verifying the phone.
    held=call('owner','POST','/api/orders/'+oid+'/preparation',{'status':'accepted','version':staff['version']})
    assert held.status_code==409 and held.json()['detail']['code']=='DUPLICATE_REVIEW_REQUIRED'
    confirmed=call('owner','POST','/api/orders/'+oid+'/duplicate-review',
        {'action':'confirm_additional','version':staff['version'],'reason':'TEST customer confirms an additional order'})
    assert confirmed.status_code==200,confirmed.text
    accepted=call('owner','POST','/api/orders/'+oid+'/preparation',{'status':'accepted','version':confirmed.json()['order']['version']});assert accepted.status_code==200,accepted.text
    for _ in range(8):assert call('customer','POST','/api/orders',body()).status_code==200
    assert call('customer','POST','/api/orders',body()).status_code==429
    assert call('customer','POST','/api/orders',body(),key='TEST-guest-first').json()==first.json()
    with db.tx() as c:assert c.execute("SELECT phone_verified_at FROM users WHERE id='v5-customer'").fetchone()['phone_verified_at'] is None
