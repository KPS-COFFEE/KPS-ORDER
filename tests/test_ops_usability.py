"""Real isolated PostgreSQL: partial contact discovery and atomic vehicle transfer."""
import pytest
from uuid import uuid4
from app import db
from test_operations import ops

pytestmark=pytest.mark.postgres


def test_partial_customer_discovery_scope_city_and_normalization(ops):
    actor,post,order,shifts=ops
    one=order();two=order();foreign=order()
    with db.tx() as c:
        c.execute("INSERT INTO branches(id,brand_id,branch_no,source_id,name,city) VALUES('ops-second','ops-brand',2,'ops-second','Second branch','الرياض')")
        for oid,bid,name in [(one,'ops-branch','Allowed'),(two,'ops-second','Other branch'),(foreign,'ops-branch-other','Other brand')]:
            # Confirmed snapshots stay immutable; seed a fresh synthetic order.
            c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) SELECT %s,%s,%s,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,%s FROM orders WHERE id=%s",('search-'+oid,'ops-other' if oid==foreign else 'ops-brand',bid,db.json({'phone':'+966501234567','name':name}),one))
    client=actor('manager')
    for fragment in ['123','٠٥٠١','0501234567','+966501234567','00966501234567']:
        r=client.get('/api/orders/customer-search',params={'phone':fragment})
        assert r.status_code==200,r.text
        assert [(v['name'],v['city']) for v in r.json()['contacts']]==[('Allowed','حفر الباطن')]
    assert client.get('/api/orders/customer-search',params={'phone':'123','branch_id':'ops-second'}).status_code==404
    assert client.get('/api/orders/customer-search',params={'phone':'12'}).status_code==422
    assert client.get('/api/orders/customer-search',params={'phone':'%123%'}).status_code==422
    data=actor('owner').get('/api/orders/customer-search',params={'phone':'123'}).json()
    assert len(data['contacts'])==2 and {c['city'] for c in data['contacts']}=={'الرياض','حفر الباطن'}
    assert actor('driver').get('/api/orders/customer-search',params={'phone':'123'}).status_code==403
    with db.tx() as c:
        c.execute("UPDATE users SET permission_denials='[\"orders_view\"]' WHERE id='ops-manager'")
    assert actor('manager').get('/api/orders/customer-search',params={'phone':'123'}).status_code==403


def test_transfer_releases_previous_driver_without_swapping_and_replays(ops):
    actor,*_=ops;client=actor('owner')
    def create(plate):
        r=client.post('/api/fleet/vehicles',json={'vehicle':{'plate':plate,'type':'Synthetic','odometer_km':1000},'branch_ids':['ops-branch']},headers={'Idempotency-Key':uuid4().hex})
        assert r.status_code==200,r.text
        return r.json()['vehicle_id']
    vehicle=create('TEST TRANSFER 123');old=create('TEST OLD 456')
    with db.tx() as c:
        c.execute("UPDATE users SET vehicle=%s WHERE id='ops-driver'",(db.json({'plate':'TEST TRANSFER 123'}),))
        c.execute("UPDATE users SET vehicle=%s WHERE id='ops-driver2'",(db.json({'plate':'TEST OLD 456'}),))
    url='/api/fleet/vehicles/'+vehicle+'/management'
    data=client.get(url).json();assert data['assigned_driver_ids']==['ops-driver']
    body={'action':'transfer','driver_id':'ops-driver2','revision':0,'reason':'Owner approved transfer','expected_driver_ids':['ops-driver']}
    assert client.post(url,json={**body,'expected_driver_ids':[]},headers={'Idempotency-Key':uuid4().hex}).status_code==409
    response=client.post(url,json=body,headers={'Idempotency-Key':'transfer-once'})
    assert response.status_code==200,response.text
    assert client.post(url,json=body,headers={'Idempotency-Key':'transfer-once'}).json()==response.json()
    with db.tx() as c:
        rows={r['id']:r['vehicle'] for r in c.execute("SELECT id,vehicle FROM users WHERE id IN ('ops-driver','ops-driver2')").fetchall()}
        assert rows['ops-driver']=={} and rows['ops-driver2']['plate']=='TEST TRANSFER 123'
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='fleet.manage' AND entity_id=%s",(vehicle,)).fetchone()['n']==1
    cards=client.get('/api/fleet/vehicles').json()['vehicles']
    assert next(c for c in cards if c['id']==old)['assigned_drivers']==[]


def test_transfer_never_detaches_vehicle_from_open_shift(ops):
    actor,post,order,shifts=ops
    client=actor('owner')
    car=client.post('/api/fleet/vehicles',json={'vehicle':{'plate':'TEST BUSY 123','type':'Synthetic','odometer_km':1000},'branch_ids':['ops-branch']},headers={'Idempotency-Key':uuid4().hex}).json()['vehicle_id']
    with db.tx() as c:c.execute("UPDATE users SET vehicle=%s WHERE id='ops-driver'",(db.json({'plate':'TEST BUSY 123'}),))
    shifts();client=actor('owner')
    response=client.post('/api/fleet/vehicles/'+car+'/management',json={'action':'transfer','driver_id':'ops-driver2','revision':0,'reason':'Blocked during shift','expected_driver_ids':['ops-driver']},headers={'Idempotency-Key':uuid4().hex})
    assert response.status_code==409,response.text
    with db.tx() as c:assert c.execute("SELECT vehicle->>'plate' plate FROM users WHERE id='ops-driver'").fetchone()['plate']=='TEST BUSY 123'
