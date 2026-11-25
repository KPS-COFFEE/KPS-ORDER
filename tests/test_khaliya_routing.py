"""Owner-approved Hafar Khaliya routing, validated against real isolated PostgreSQL."""
from uuid import uuid4
import pytest
from app import auth, catalog, db

pytestmark = pytest.mark.postgres


def prepare(conn):
    hours=[{'days':list(range(7)),'start':'00:00','end':'00:00'}]
    settings={'active':True,'ordering_verified':True,'pickup_enabled':True,'car_enabled':True,
              'delivery_enabled':True,'opening_hours':hours,'delivery_hours':hours,
              'payment_methods':['cash','card'],'location':{'lat':28.4,'lng':45.9},'location_verified':True,
              'coverage':{'type':'radius','center':{'lat':28.4,'lng':45.9},'radius_meters':50000},
              'delivery_fee_halalas':500,'minimum_order_halalas':0}
    conn.execute("UPDATE branches SET settings=settings||%s WHERE id IN ('kps-branch-1','kps-branch-2')",(db.json(settings),))
    conn.execute("UPDATE branches SET settings=settings||%s WHERE id='kps-branch-1'",(db.json({'product_availability':{'kps-33':False,'kps-34':False}}),))
    conn.execute("UPDATE products SET settings=settings||%s WHERE id IN ('kps-33','kps-34')",(db.json({'available_branch_ids':['kps-branch-2']}),))


def body(service='delivery', branch='kps-branch-1', khaliya=True):
    items=[{'product_id':'kps-01','quantity':1}]
    if khaliya:items.append({'product_id':'kps-33','quantity':1,'units':[{'options':{'TYPE':'classic'}}]})
    return {'brand_id':'kps','branch_id':branch,'service':service,'payment_method':'cash','items':items,
            'customer':{'name':'TEST routing','neighborhood':'TEST حي','address':'TEST address',
                        'location':{'lat':28.4,'lng':45.9},'car':{'plate':'TEST','color':'black'}}}


def test_delivery_routes_entire_basket_and_keeps_customer_branch_and_fee(client):
    with db.tx() as conn:
        prepare(conn)
        conn.execute("UPDATE branches SET settings=settings||%s WHERE id='kps-branch-2'",(db.json({'delivery_fee_halalas':900}),))
        internal=catalog.quote_order(conn,catalog.QuoteBody.model_validate(body()),for_update=True)
    assert internal['branch_id']=='kps-branch-2' and len(internal['items'])==2
    assert internal['branch_no']==2 and internal['delivery_fee_halalas']==500
    result=client.post('/api/quote',json=body())
    assert result.status_code==200,result.text
    public=result.json()['quote']
    assert public['branch_id']=='kps-branch-1' and 'internal_routing' not in public
    catalog_result=client.get('/api/catalog?branch_id=kps-branch-1&service=delivery').json()
    assert next(p for p in catalog_result['products'] if p['id']=='kps-33')['available']


@pytest.mark.parametrize('service',['pickup','car'])
def test_pickup_is_blocked_on_selected_branch_and_allowed_after_explicit_switch(client,service):
    with db.tx() as conn:prepare(conn)
    catalog_result=client.get('/api/catalog',params={'branch_id':'kps-branch-1','service':service}).json()
    product=next(p for p in catalog_result['products'] if p['id']=='kps-33')
    assert not product['available'] and 'فرع 2' in product['unavailable_reason']
    result=client.post('/api/quote',json=body(service))
    assert result.status_code==409 and 'فرع 2' in result.json()['detail']['message']
    switched=client.post('/api/quote',json=body(service,'kps-branch-2'))
    assert switched.status_code==200,switched.text
    assert switched.json()['quote']['branch_id']=='kps-branch-2'


def test_without_khaliya_never_routes(client):
    with db.tx() as conn:prepare(conn)
    result=client.post('/api/quote',json=body(khaliya=False))
    assert result.status_code==200,result.text
    assert result.json()['quote']['branch_id']=='kps-branch-1'


@pytest.mark.parametrize('stop',['branch','khaliya','classic','other_item'])
def test_target_stops_block_entire_order(client,stop):
    with db.tx() as conn:
        prepare(conn)
        key={'branch':'active','khaliya':'kps-33','classic':'kps-34','other_item':'kps-01'}[stop]
        settings={'active':False} if stop=='branch' else {'product_availability':{key:False}}
        conn.execute("UPDATE branches SET settings=settings||%s WHERE id='kps-branch-2'",(db.json(settings),))
    assert client.post('/api/quote',json=body()).status_code==409


def test_no_cross_city_route(client):
    with db.tx() as conn:
        prepare(conn)
        conn.execute("UPDATE branches SET city='رفحاء' WHERE id='kps-branch-1'")
    result=client.post('/api/quote',json=body())
    assert result.status_code==409
    assert 'حفر الباطن' in result.json()['detail']['message']


def test_persisted_execution_branch_idempotency_and_scopes(client):
    with db.tx() as conn:
        prepare(conn)
        for uid,role,branches in [('routing-customer','customer',[]),('routing-manager','delivery_manager',['kps-branch-2']),
                                  ('routing-one','branch_manager',['kps-branch-1']),('routing-two','branch_manager',['kps-branch-2'])]:
            conn.execute('INSERT INTO users(id,display_name,role,brand_id,branch_ids,phone) VALUES(%s,%s,%s,%s,%s,%s)',
                         (uid,uid,role,'kps',db.json(branches),'+966500000001'))
        conn.execute("INSERT INTO shifts(id,user_id,brand_id,kind,branch_ids,status) VALUES('routing-duty','routing-manager','kps','manager',%s,'open')",(db.json(['kps-branch-2']),))
    def actor(uid):
        with db.tx() as conn:user=conn.execute('SELECT * FROM users WHERE id=%s',(uid,)).fetchone()
        client.app.dependency_overrides[auth.current_user]=lambda:user
        return client
    customer=actor('routing-customer');key=uuid4().hex
    response=customer.post('/api/orders',json=body(),headers={'Idempotency-Key':key})
    assert response.status_code==200,response.text
    public=response.json()['order'];oid=public['id']
    assert public['branch_id']=='kps-branch-1' and 'internal_routing' not in public['snapshot']
    assert customer.post('/api/orders',json=body(),headers={'Idempotency-Key':key}).json()['order']['id']==oid
    with db.tx() as conn:
        stored=conn.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert stored['branch_id']=='kps-branch-2' and stored['snapshot']['branch_id']=='kps-branch-2'
        assert len(stored['snapshot']['items'])==2
        assert conn.execute('SELECT count(*) n FROM orders WHERE id=%s',(oid,)).fetchone()['n']==1
    assert actor('routing-one').get('/api/orders/'+oid).status_code==404
    assert actor('routing-two').get('/api/orders/'+oid).json()['order']['branch_id']=='kps-branch-2'
