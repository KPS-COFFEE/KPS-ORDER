"""Final account delta: real PostgreSQL ownership, retries, filters and notices."""
import pytest
from app import auth,db
from tests.test_core import _prepare_catalog,_create,_authenticate,_make_user
from tests.test_customer_experience import required,verify

pytestmark=pytest.mark.postgres
ADDRESS={'label':'TEST Home','address':'TEST street','neighborhood':'TEST area','lat':28.4,'lng':45.9}

def test_address_create_edit_replay_delete_and_other_customer(required,monkeypatch):
    first=verify(required,monkeypatch)['user']['id']
    r=required.post('/api/customer/addresses',json=ADDRESS,headers={'Idempotency-Key':'test-address-create'})
    assert r.status_code==200,r.text
    saved=r.json();identifier=saved['address']['id']
    assert required.post('/api/customer/addresses',json=ADDRESS,headers={'Idempotency-Key':'test-address-create'}).json()==saved
    changed={**ADDRESS,'label':'TEST Work','lat':None,'lng':None}
    assert required.post('/api/customer/addresses',json=changed,headers={'Idempotency-Key':'test-address-create'}).status_code==409
    edit=required.patch('/api/customer/addresses/'+identifier,json=changed,headers={'Idempotency-Key':'test-address-edit'})
    assert edit.status_code==200,edit.text
    assert required.patch('/api/customer/addresses/'+identifier,json=changed,headers={'Idempotency-Key':'test-address-edit'}).json()==edit.json()
    assert required.get('/api/customer/profile').json()['addresses']==[edit.json()['address']]
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM customer_addresses WHERE customer_id=%s',(first,)).fetchone()['n']==1
    verify(required,monkeypatch,'0502222222')
    assert required.get('/api/customer/profile').json()['addresses']==[]
    assert required.patch('/api/customer/addresses/'+identifier,json=changed,headers={'Idempotency-Key':'test-address-other'}).status_code==404
    assert required.delete('/api/customer/addresses/'+identifier).status_code==404
    verify(required,monkeypatch)
    assert required.delete('/api/customer/addresses/'+identifier).status_code==200
    assert required.get('/api/customer/profile').json()['addresses']==[]
    assert required.patch('/api/customer/addresses/'+identifier,json=changed,headers={'Idempotency-Key':'test-address-edit'}).status_code==404

@pytest.mark.parametrize('changes',[{'label':'  '},{'address':'  '},{'lat':True},{'lat':91},{'lng':181},{'lat':None},{'customer_id':'someone-else'},{'brand_id':'other'}])
def test_address_strict_input(required,monkeypatch,changes):
    verify(required,monkeypatch)
    assert required.post('/api/customer/addresses',json={**ADDRESS,**changes},headers={'Idempotency-Key':'test-address-invalid'}).status_code==422
    assert required.get('/api/customer/profile').json()['addresses']==[]

def test_address_requires_current_proof_and_expiry_preserves_data(required,monkeypatch):
    guest=required.post('/api/identity/guest',json={'display_name':'TEST guest','phone':'0500000000'}).json()
    required.headers['X-CSRF-Token']=guest['csrf_token']
    assert required.post('/api/customer/addresses',json=ADDRESS,headers={'Idempotency-Key':'test-address-guest'}).status_code==403
    user=verify(required,monkeypatch)['user']
    assert required.post('/api/customer/addresses',json=ADDRESS,headers={'Idempotency-Key':'test-address-proof'}).status_code==200
    with db.tx() as c:c.execute("UPDATE sessions SET customer_verified_at=now()-interval '31 days' WHERE user_id=%s",(user['id'],))
    assert required.get('/api/customer/profile').status_code==401
    verify(required,monkeypatch)
    assert len(required.get('/api/customer/profile').json()['addresses'])==1

def test_order_tabs_are_scoped_and_paginated_on_server(required,monkeypatch):
    _prepare_catalog();verify(required,monkeypatch)
    orders=[]
    for i in range(4):
        r=_create(required,key='test-tab-order-'+str(i));assert r.status_code==200,r.text
        orders.append(r.json()['order']['id'])
    with db.tx() as c:
        c.execute("UPDATE orders SET preparation_status='completed' WHERE id=%s",(orders[1],))
        c.execute("UPDATE orders SET preparation_status='cancelled' WHERE id=%s",(orders[2],))
        c.execute("UPDATE orders SET preparation_status='completed',delivery_status='cancelled' WHERE id=%s",(orders[3],))
    for mode,wanted in [('active',[orders[0]]),('history',[orders[1]]),('cancelled',[orders[2],orders[3]])]:
        r=required.get('/api/orders',params={'customer_view':mode,'limit':1});assert r.status_code==200,r.text
        found=[x['id'] for x in r.json()['orders']]
        offset=r.json()['next_offset']
        if offset is not None:
            found += [x['id'] for x in required.get('/api/orders',params={'customer_view':mode,'limit':1,'offset':offset}).json()['orders']]
        assert set(found)==set(wanted)
    verify(required,monkeypatch,'0503333333')
    for mode in ('active','history','cancelled'):
        assert required.get('/api/orders',params={'customer_view':mode}).json()['orders']==[]
    assert required.get('/api/orders/'+orders[0]).status_code==404
    _authenticate(required,_make_user(role='owner'))
    assert required.get('/api/orders?customer_view=active').status_code==403
    assert required.get('/api/orders').status_code==200

@pytest.mark.parametrize('phone',['+12025550123','+971501234567','+966112345678'])
def test_saudi_mobile_is_enforced_before_any_provider(required,phone):
    r=required.post('/api/auth/otp/request',json={'phone':phone})
    assert r.status_code==422 and r.json()['detail']['code']=='PHONE_INVALID'
    with db.tx() as c:assert c.execute('SELECT count(*) n FROM otp_challenges').fetchone()['n']==0

@pytest.mark.parametrize('language,title',[('ar','سياسة الاسترجاع والاسترداد'),('en','Returns and refunds')])
def test_refunds_public_without_identity_and_no_invented_entity(required,language,title):
    r=required.get('/refunds',params={'lang':language})
    assert r.status_code==200 and title in r.text
    assert 'checkbox' not in r.text and '312720875900003' not in r.text
    assert 'Cache-Control' in r.headers
