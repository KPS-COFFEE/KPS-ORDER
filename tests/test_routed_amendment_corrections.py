"""Approved revisions refresh the same scoped preparation route after printing."""
import pytest

from app import db, preparation_routing
from test_preparation_routing import routing
from test_operations import ops


def proposal(r,oid):
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
    return {'version':o['version'],'items':[{'product_id':'route-product','quantity':2,
        'units':[{'options':{'temperature':'hot'}},{'options':{'temperature':'hot'}}]}],
        'customer':o['customer'],'notes':'Approved new preparation','reason':'Customer requested two coffees',
        'attachment_unavailable':True,'attachment_unavailable_reason':'Customer screenshot upload failed'}


def offered(r, *, picked_up=False):
    oid=r['order'](snapshot_changes={'journey_controls_version':1})
    chosen=r['choose'](oid)
    r['post']('owner','/api/printing/orders/'+oid,{'device_id':'printer-'+r['target'],'kind':'both'})
    if picked_up:
        _,driver=r['both_shifts']()
        assigned=r['post']('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':driver,'accept_order':True})
        with db.tx() as c:
            o=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone()
            preparation_routing.pickup_branch_id(c,o)
            c.execute("UPDATE driver_tasks SET status='picked_up',accepted_at=now(),picked_up_at=now() WHERE id=%s",(assigned['task']['id'],))
            c.execute("UPDATE orders SET preparation_status='handed_over',delivery_status='picked_up',version=version+1 WHERE id=%s",(oid,))
    p=proposal(r,oid)
    created=r['post']('owner','/api/order-amendments/orders/'+oid,p)
    aid=created['amendment_id']
    r['post']('owner','/api/order-amendments/'+aid+'/decision',{'action':'offer','note':'Approved exact revised basket'})
    return oid,aid,chosen


@pytest.mark.postgres
def test_postprint_routed_amendment_retains_destination_and_history(routing):
    r=routing;oid,aid,chosen=offered(r)
    r['post']('owner','/api/order-amendments/'+aid+'/decision',{'action':'accept','channel':'phone','note':'Customer agreed to exact basket'})
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        route=preparation_routing.current_route(c,o)
        assert o['branch_id']==r['source']
        assert route['target_branch_id']==r['target'] and route['previous_branch_id']==r['target']
        assert route['id']!=chosen['route_id'] and route['amendment_revision']==o['amendment_revision']==1
        assert route['evidence']['amendment_id']==aid
        assert preparation_routing.pickup_branch_id(c,o)==r['target']
        assert c.execute('SELECT count(*) n FROM order_preparation_routes WHERE order_id=%s',(oid,)).fetchone()['n']==2
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='preparation.amendment_routed'",(oid,)).fetchone()['n']==1
    target=r['actor']('target').get('/api/preparation-routing/tasks/'+oid)
    assert target.status_code==200,target.text
    assert target.json()['preparation_task']['items'][0]['quantity']==2
    version=target.json()['preparation_task']['version']
    r['post']('target','/api/preparation-routing/tasks/'+oid+'/prepare',{'version':version,'status':'accepted'})
    with db.tx() as c:
        receipt=c.execute("SELECT * FROM order_revision_receipts WHERE order_id=%s AND revision=1 AND kind='branch'",(oid,)).fetchone()
        assert receipt['actor_id']=='ops-target'
    # A stale queue card never acknowledges or advances another version.
    r['post']('target','/api/preparation-routing/tasks/'+oid+'/prepare',{'version':version,'status':'preparing'},status=409)


@pytest.mark.postgres
def test_routed_amendment_revalidates_target_product_at_execution(routing):
    r=routing;oid,aid,chosen=offered(r)
    with db.tx() as c:
        settings=c.execute("SELECT settings FROM products WHERE id='route-product'").fetchone()['settings']
        settings['option_groups'][0]['values'][0]['unavailable_branch_ids']=[r['target']]
        c.execute("UPDATE products SET settings=%s WHERE id='route-product'",(db.json(settings),))
    response=r['post']('owner','/api/order-amendments/'+aid+'/decision',{'action':'accept','channel':'phone','note':'Customer agreed'},status=409)
    assert response['detail']['code']=='PRODUCT_UNAVAILABLE'
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert o['amendment_revision']==0 and o['preparation_route_id']==chosen['route_id']
        assert c.execute('SELECT count(*) n FROM order_preparation_routes WHERE order_id=%s',(oid,)).fetchone()['n']==1
        assert c.execute('SELECT status FROM order_amendments WHERE id=%s',(aid,)).fetchone()['status']=='offered'


@pytest.mark.postgres
def test_old_worker_cannot_apply_routed_revision_without_exact_new_route(routing,monkeypatch):
    r=routing;oid,aid,chosen=offered(r)
    monkeypatch.setattr(preparation_routing,'amendment_route',lambda c,o,actor,aid,q:o['preparation_route_id'])
    response=r['actor']('owner').post('/api/order-amendments/'+aid+'/decision',json={
        'action':'accept','channel':'phone','note':'Customer agreed'},headers={'Idempotency-Key':'old-worker-route-amendment'})
    assert response.status_code==503,response.text
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert o['amendment_revision']==0 and o['preparation_route_id']==chosen['route_id']


@pytest.mark.postgres
def test_postpickup_target_reviews_revision_without_changing_physical_status(routing):
    from app.order_amendments import guard
    from fastapi import HTTPException
    r=routing;oid,aid,_=offered(r,picked_up=True)
    r['post']('owner','/api/order-amendments/'+aid+'/decision',{'action':'accept','channel':'phone','note':'Customer agreed'})
    queue=r['actor']('target').get('/api/preparation-routing/tasks')
    row=next(t for t in queue.json()['preparation_tasks'] if t['id']==oid)
    assert row['next_status']=='reviewed' and row['items'][0]['quantity']==2
    assert row['operational_instructions']=='Approved exact revised basket'
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        with pytest.raises(HTTPException) as missing:guard(c,o,driver_id='ops-driver')
        assert missing.value.detail['code']=='AMENDMENT_BRANCH_RECEIPT'
    response=r['post']('target','/api/preparation-routing/tasks/'+oid+'/prepare',{'version':row['version'],'status':'reviewed'})
    assert response['preparation_task']['preparation_status']=='handed_over'
    r['post']('driver','/api/order-amendments/orders/'+oid+'/receipt',{'revision':1,'kind':'driver'})
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert o['preparation_status']=='handed_over' and o['delivery_status']=='picked_up'
        guard(c,o,driver_id='ops-driver')
    queue=r['actor']('target').get('/api/preparation-routing/tasks')
    assert oid not in {t['id'] for t in queue.json()['preparation_tasks']}
