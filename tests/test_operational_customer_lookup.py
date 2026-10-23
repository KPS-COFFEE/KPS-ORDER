"""Operational mobile contacts never imply authenticated customer ownership."""
from contextlib import contextmanager
from copy import deepcopy
import pytest
from fastapi import HTTPException
from app import orders, db

OWNER={'id':'owner','role':'owner','brand_id':'kps','branch_ids':[]}
MANAGER={'id':'manager','role':'delivery_manager','brand_id':'kps','branch_ids':['b']}

class Result:
    def __init__(self,row=None,rows=None):self.row=row;self.rows=rows
    def fetchone(self):return self.row
    def fetchall(self):return self.rows

@pytest.mark.parametrize('value',['0501234567','+966501234567','00966501234567','966501234567','٠٥٠١٢٣٤٥٦٧','(050) 123-4567'])
def test_full_saudi_phone_normalization(value):
    assert orders.search_phone(value)=='+966501234567'

@pytest.mark.parametrize('value',['0501','501234567','%50123%','+12025550123','05012345678','not a phone'])
def test_partial_or_non_saudi_number_is_not_a_lookup_key(value):
    assert orders.search_phone(value) is None


def test_repeat_contact_is_stable_without_authenticated_account_selection():
    existing={};statements=[];lock_keys=[]
    class Conn:
        def execute(self,sql,args):
            statements.append(sql)
            if sql.startswith('SELECT pg_advisory_xact_lock'):
                lock_keys.append(args[0])
                assert len(statements)%2==1, 'The contact lock must precede every insert'
                return Result()
            assert sql.startswith('INSERT INTO users') and 'ON CONFLICT(id)' in sql
            assert len(statements)%2==0 and statements[-2].startswith('SELECT pg_advisory_xact_lock')
            existing.setdefault(args[0],{'id':args[0],'active':True,'identity_kind':'guest','brand_id':args[3]})
            return Result(existing[args[0]])
    customer={'phone':'+966501234567','name':'عميل'}
    a=orders.operational_contact(Conn(),'kps',customer,'manager')
    b=orders.operational_contact(Conn(),'kps',customer,'other-manager')
    c=orders.operational_contact(Conn(),'enbishan',customer,'manager')
    assert a==b and a!=c and len(existing)==2
    assert lock_keys==['operational-contact:kps:'+a,'operational-contact:kps:'+b,'operational-contact:enbishan:'+c]
    assert all(customer['phone'] not in value for value in lock_keys)
    assert not any('sessions' in sql or 'customer_identity_links' in sql for sql in statements)


def test_disabled_operational_contact_fails_closed():
    class Conn:
        def execute(self,sql,args):return Result({'id':args[0],'active':False,'identity_kind':'guest','brand_id':'kps'})
    with pytest.raises(HTTPException) as e:orders.operational_contact(Conn(),'kps',{'phone':'+966501234567','name':'N'},'m')
    assert e.value.status_code==409


def test_branch_and_brand_scope_precede_contact_query(monkeypatch):
    queries=[]
    class Conn:
        def execute(self,sql,args):queries.append((sql,args));return Result({'id':'b'})
    with pytest.raises(HTTPException):orders.customer_lookup_scope(Conn(),MANAGER,'foreign')
    assert not queries
    orders.customer_lookup_scope(Conn(),MANAGER,'b')
    assert queries[0][1]==('b','kps')
    denied={**MANAGER,'permission_denials':['orders_view']}
    with pytest.raises(HTTPException):orders.customer_lookup_scope(Conn(),denied,'b')


def test_lookup_returns_only_scoped_snapshots_and_marks_ambiguous(monkeypatch):
    queries=[];audit=[]
    data={'name':'N','address':'A','location':{'lat':28.,'lng':45.},'neighborhood':'حي','phone':'+966501234567','risk_reasons':['SECRET']}
    class Conn:
        def execute(self,sql,args):
            queries.append((sql,args))
            if sql.startswith('SELECT 1 FROM branches'):return Result({'id':'b'})
            if sql.startswith('SELECT customer_id,customer'):return Result(rows=[{'customer_id':'a','customer':data},{'customer_id':'b','customer':data}])
            raise AssertionError(sql)
    @contextmanager
    def tx():yield Conn()
    monkeypatch.setattr(db,'tx',tx);monkeypatch.setattr(db,'rate_limit',lambda *a:True)
    monkeypatch.setattr(db,'audit',lambda *a:audit.append(a))
    result=orders.customer_lookup('0501234567','b',MANAGER)
    assert result['ambiguous'] and not result['verified']
    assert len(result['contacts'])==1 and 'risk_reasons' not in result['contacts'][0]
    query,args=queries[1]
    assert args==('kps','b','+966501234567') and "service='delivery'" in query and 'LIMIT 50' in query
    assert audit and audit[0][-1]['phone_suffix']=='4567'


def test_lookup_rate_limit_prevents_database_contact_read(monkeypatch):
    queries=[]
    class Conn:
        def execute(self,sql,args):queries.append(sql);return Result({'id':'b'})
    @contextmanager
    def tx():yield Conn()
    monkeypatch.setattr(db,'tx',tx);monkeypatch.setattr(db,'rate_limit',lambda *a:False)
    with pytest.raises(HTTPException) as e:orders.customer_lookup('0501234567','b',MANAGER)
    assert e.value.status_code==429 and len(queries)==1


@pytest.mark.parametrize('failure', ['complete','cancelled','driver','stale','coverage','fee','cross_branch','permission'])
def test_location_change_guards_preserve_order(monkeypatch,failure):
    row={'id':'o','brand_id':'kps','branch_id':'b','customer_id':'customer','service':'delivery','preparation_status':'accepted',
        'delivery_status':'pending','delivery_fee_halalas':200,'customer':{'location':{'lat':28.,'lng':45.},'address':'old','neighborhood':'old'}}
    if failure=='complete':row['preparation_status']='completed'
    if failure=='cancelled':row['delivery_status']='cancelled'
    if failure=='cross_branch':row['branch_id']='other'
    original=deepcopy(row);writes=[]
    class Conn:
        def execute(self,sql,args):
            if sql.startswith('SELECT * FROM orders'):return Result(row)
            if sql.startswith('SELECT 1 FROM driver_tasks'):return Result({'id':'task'} if failure=='driver' else None)
            if sql.startswith('SELECT * FROM branches'):return Result({'id':'b','settings':{'coverage':{},'delivery_fee_halalas':201 if failure=='fee' else 200}})
            writes.append(sql);raise AssertionError(sql)
    @contextmanager
    def tx():yield Conn()
    monkeypatch.setattr(db,'tx',tx);monkeypatch.setattr(db,'idempotent',lambda c,u,k,op,b,fn:fn())
    monkeypatch.setattr(orders.catalog,'branch_service',lambda *a:(True,None,None))
    monkeypatch.setattr(orders.catalog,'covered',lambda *a:failure!='coverage')
    body=orders.DeliveryLocationChange(address='new',neighborhood='حي',location={'lat':28.1,'lng':45.1},
        expected_location={'lat':29. if failure=='stale' else 28.,'lng':45.},reason='Customer WhatsApp request')
    actor={**MANAGER,'permission_denials':['orders_amend']} if failure=='permission' else MANAGER
    with pytest.raises(HTTPException):orders.change_delivery_location('o',body,actor,'location-key')
    assert not writes and row==original


def test_location_change_records_source_actor_and_never_mutates_address_book(monkeypatch):
    row={'id':'o','brand_id':'kps','branch_id':'b','service':'delivery','preparation_status':'accepted','delivery_status':'pending',
        'delivery_fee_halalas':200,'customer':{'location':{'lat':28.,'lng':45.},'address':'old','neighborhood':'old'}}
    writes=[];events=[];audit=[]
    class Conn:
        def execute(self,sql,args):
            if sql.startswith('SELECT * FROM orders'):return Result(row)
            if sql.startswith('SELECT 1 FROM driver_tasks'):return Result()
            if sql.startswith('SELECT * FROM branches'):return Result({'settings':{'coverage':{},'delivery_fee_halalas':200}})
            writes.append((sql,args));return Result()
    @contextmanager
    def tx():yield Conn()
    monkeypatch.setattr(db,'tx',tx);monkeypatch.setattr(db,'idempotent',lambda c,u,k,op,b,fn:fn())
    monkeypatch.setattr(orders,'apply_delivery_location_revision',lambda conn,row,customer,user,body:conn.execute('UPDATE orders SET customer=%s WHERE id=%s',(db.json(customer),row['id'])))
    monkeypatch.setattr(db,'event',lambda *a:events.append(a));monkeypatch.setattr(db,'audit',lambda *a:audit.append(a))
    monkeypatch.setattr(orders.catalog,'branch_service',lambda *a:(True,None,None));monkeypatch.setattr(orders.catalog,'covered',lambda *a:True)
    body=orders.DeliveryLocationChange(address='new',neighborhood='حي',location={'lat':28.1,'lng':45.1},expected_location={'lat':28.,'lng':45.},reason='Requested in WhatsApp',whatsapp_reference='manual-reference')
    result=orders.change_delivery_location('o',body,MANAGER,'location-key')
    assert not result['permanent_address_changed'] and not result['whatsapp_verified']
    assert len(writes)==1 and writes[0][0].startswith('UPDATE orders SET customer=')
    assert events[0][2]=='manager' and audit[0][1]=='manager'
    assert events[0][-1]['whatsapp_reference']=='manual-reference'
    assert events[0][-1]['previous']['address']=='old'


@pytest.mark.postgres
def test_postgres_staff_customer_reuse_lookup_and_whatsapp_revision(client,monkeypatch):
    from tests.test_core import _prepare_catalog,_make_user,_body
    from tests.sms_policy_helpers import sms_login
    _prepare_catalog()
    manager=_make_user('delivery_manager',branches=['kps-branch-1'])
    sms_login(client,manager,monkeypatch)
    with db.tx() as conn:
        conn.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES('lookup-manager-shift','manager',%s,'kps',%s)",(manager['id'],db.json(['kps-branch-1'])))
        identities_before=conn.execute('SELECT count(*) n FROM sessions').fetchone()['n']
        links_before=conn.execute('SELECT count(*) n FROM customer_identity_links').fetchone()['n']
    body=_body('delivery');body['customer']['phone']='0501234567'
    response=client.post('/api/orders/delivery-center',json=body,headers={'Idempotency-Key':'lookup-first-order'})
    assert response.status_code==200,response.text
    first=response.json()['order']
    assert client.post('/api/orders/delivery-center',json=body,headers={'Idempotency-Key':'lookup-first-order'}).json()==response.json()
    body['customer']['phone']='00966501234567'
    second=client.post('/api/orders/delivery-center',json=body,headers={'Idempotency-Key':'lookup-second-order'})
    assert second.status_code==200,second.text
    assert first['customer_id']==second.json()['order']['customer_id']
    assert first['customer']['phone_verified'] is False
    assert first['snapshot']['order_source']=='delivery_center'
    assert first['snapshot']['created_by_staff_id']==manager['id']
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) n FROM users WHERE id LIKE 'customer_contact_%%'").fetchone()['n']==1
        assert conn.execute('SELECT count(*) n FROM orders').fetchone()['n']==2
        assert conn.execute('SELECT count(*) n FROM sessions').fetchone()['n']==identities_before
        assert conn.execute('SELECT count(*) n FROM customer_identity_links').fetchone()['n']==links_before
        original_snapshot=conn.execute('SELECT snapshot FROM orders WHERE id=%s',(first['id'],)).fetchone()['snapshot']
        principal=conn.execute('SELECT * FROM users WHERE id=%s',(first['customer_id'],)).fetchone()
        assert principal['identity_kind']=='guest' and principal['phone_verified_at'] is None
    found=client.get('/api/orders/customer-lookup',params={'phone':'0501234567','branch_id':'kps-branch-1'})
    assert found.status_code==200,found.text
    assert len(found.json()['contacts'])==1 and found.json()['contacts'][0]['address']==body['customer']['address']
    searched=client.get('/api/orders',params={'phone':'+966501234567'})
    assert searched.status_code==200,searched.text
    assert len(searched.json()['orders'])==2
    assert client.get('/api/orders/customer-lookup',params={'phone':'0501234567','branch_id':'kps-branch-5'}).status_code==404
    assert client.get('/api/orders',params={'phone':'050123'}).status_code==422
    change={'address':'عنوان واتساب الجديد','neighborhood':'حي جديد','location':{'lat':28.401,'lng':45.901},'expected_location':body['customer']['location'],'reason':'Customer requested via WhatsApp','whatsapp_reference':'synthetic-manual-reference'}
    changed=client.post('/api/orders/'+first['id']+'/delivery-location',json=change,headers={'Idempotency-Key':'lookup-location-change'})
    assert changed.status_code==200,changed.text
    assert not changed.json()['whatsapp_verified'] and not changed.json()['permanent_address_changed']
    assert client.post('/api/orders/'+first['id']+'/delivery-location',json=change,headers={'Idempotency-Key':'lookup-location-change'}).json()==changed.json()
    with db.tx() as conn:
        saved=conn.execute('SELECT * FROM orders WHERE id=%s',(first['id'],)).fetchone()
        assert saved['amendment_revision']==1 and saved['customer']['location']==change['location']
        assert saved['total_halalas']==first['total_halalas'] and saved['snapshot']==original_snapshot
        assert conn.execute('SELECT count(*) n FROM order_revisions WHERE order_id=%s',(first['id'],)).fetchone()['n']==1
        assert conn.execute('SELECT count(*) n FROM customer_addresses WHERE customer_id=%s',(first['customer_id'],)).fetchone()['n']==0
        event=conn.execute("SELECT * FROM order_events WHERE order_id=%s AND event_type='order.delivery_location.changed'",(first['id'],)).fetchone()
        assert event['actor_id']==manager['id'] and event['details']['whatsapp_reference']=='synthetic-manual-reference'
        assert event['details']['whatsapp_verified'] is False
        conn.execute("UPDATE users SET permission_denials='[\"orders_amend\"]'::jsonb WHERE id=%s",(manager['id'],))
    assert client.post('/api/orders/'+first['id']+'/delivery-location',json=change,headers={'Idempotency-Key':'lookup-location-change'}).status_code==403


@pytest.mark.postgres
def test_postgres_concurrent_operational_contact_never_claims_verified_identity(database):
    from concurrent.futures import ThreadPoolExecutor
    from tests.test_core import _make_user
    verified=_make_user()
    with db.tx() as conn:
        conn.execute("UPDATE users SET phone='+966501234567',identity_kind='phone_sms_verified',phone_verified_at=now(),phone_sms_verified_at=now() WHERE id=%s",(verified['id'],))
        original=conn.execute('SELECT * FROM users WHERE id=%s',(verified['id'],)).fetchone()
    def create(_):
        with db.tx() as conn:
            return orders.operational_contact(conn,'kps',{'phone':'+966501234567','name':'Operational contact'},'synthetic')
    with ThreadPoolExecutor(max_workers=4) as pool:
        identifiers=list(pool.map(create,range(4)))
    assert len(set(identifiers))==1 and identifiers[0]!=verified['id']
    with db.tx() as conn:
        assert conn.execute('SELECT * FROM users WHERE id=%s',(verified['id'],)).fetchone()==original
        assert conn.execute('SELECT count(*) n FROM users WHERE id=%s',(identifiers[0],)).fetchone()['n']==1
        assert conn.execute('SELECT count(*) n FROM sessions WHERE user_id=%s',(identifiers[0],)).fetchone()['n']==0
        assert conn.execute('SELECT count(*) n FROM customer_identity_links').fetchone()['n']==0


@pytest.mark.postgres
@pytest.mark.parametrize('revocation',['branch','shift'])
def test_postgres_manager_order_replay_rechecks_branch_and_open_shift(client,monkeypatch,revocation):
    from tests.test_core import _prepare_catalog,_make_user,_body
    from tests.sms_policy_helpers import sms_login
    _prepare_catalog()
    manager=_make_user('delivery_manager',branches=['kps-branch-1'])
    sms_login(client,manager,monkeypatch)
    with db.tx() as conn:
        conn.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES('replay-manager-shift','manager',%s,'kps',%s)",(manager['id'],db.json(['kps-branch-1'])))
    body=_body('delivery');body['customer']['phone']='0501234567'
    create=lambda:client.post('/api/orders/delivery-center',json=body,headers={'Idempotency-Key':'manager-replay-reauthorize'})
    first=create();assert first.status_code==200,first.text
    assert create().json()==first.json()
    with db.tx() as conn:
        if revocation=='branch':conn.execute("UPDATE users SET branch_ids='[]'::jsonb WHERE id=%s",(manager['id'],))
        else:conn.execute("UPDATE shifts SET status='submitted' WHERE id='replay-manager-shift'")
    denied=create()
    assert denied.status_code==(403 if revocation=='branch' else 409),denied.text
    assert 'order' not in denied.json()
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM orders').fetchone()['n']==1
        assert conn.execute("SELECT count(*) n FROM idempotency_keys WHERE operation='orders.delivery_center'").fetchone()['n']==1
