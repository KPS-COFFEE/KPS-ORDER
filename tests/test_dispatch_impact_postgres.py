"""Isolated real-PostgreSQL scope, replay and concurrent assignment acceptance."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from uuid import uuid4
import pytest
from app import auth,db,dispatch_impact as d,route_eta
from test_operations import ops
from test_enbishan_dual_brand import dual, shared_dual  # noqa: F401

pytestmark=pytest.mark.postgres


@pytest.fixture
def impact_service(ops,monkeypatch):
    actor,post,create,shifts=ops
    _,sid=shifts()
    with db.tx() as c:
        c.execute("UPDATE branches SET settings=%s WHERE id='ops-branch'",(db.json({'location_verified':True,'location':{'lat':28.4,'lng':45.9}}),))
        c.execute("INSERT INTO driver_latest_locations(driver_id,brand_id,shift_id,lat,lng,accuracy_m) VALUES('ops-driver','ops-brand',%s,28.4,45.9,10)",(sid,))
        c.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES('impact-session','impact-token','impact-csrf','ops-owner',now()+interval '1 hour')")
    client=actor('owner')
    with db.tx() as c:user=c.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone()
    user['session_id']='impact-session'
    client.app.dependency_overrides[auth.current_user]=lambda:user
    monkeypatch.setenv('DISPATCH_ROAD_IMPACT_ENABLED','true')
    monkeypatch.setenv('MAPBOX_ENABLED','true')
    monkeypatch.setenv('MAPBOX_PUBLIC_TOKEN_OPS_BRAND','pk.'+'a'*30)
    monkeypatch.setenv('MAPBOX_ROUTING_TOKEN','pk.'+'b'*30)
    calls=[]
    def matrix(points,token):
        calls.append(points)
        return [[0 if i==j else 30 for j in range(len(points))] for i in range(len(points))]
    monkeypatch.setattr(route_eta,'fetch_matrix',matrix)
    def order():
        # Confirmed customer/snapshot fields are immutable: build the complete
        # synthetic contract at INSERT, exactly as production creation does.
        oid='impact-order-'+uuid4().hex
        snapshot={'items':[{'product_id':'ops-product','name':'قهوة تجربة','quantity':1,
            'unit_price_halalas':1000,'line_total_halalas':1000,'units':[],'note':''}],
            'branch_name':'فرع اختبار','notes':'','delivery_period':'evening',
            'delivery_workflow':'manager_dispatch'}
        customer={'name':'اختبار','location':{'lat':28.41,'lng':45.91}}
        with db.tx() as c:
            c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,
                preparation_status,delivery_status,payment_method,subtotal_halalas,
                delivery_fee_halalas,total_halalas,snapshot,customer)
                VALUES(%s,'ops-brand','ops-branch','ops-customer','delivery','ready',
                       'pending','cash',1000,0,1000,%s,%s)""",(oid,db.json(snapshot),db.json(customer)))
        return oid
    def send(path,body,key=None):return client.post(path,json=body,headers={'Idempotency-Key':key or uuid4().hex})
    def preview(oid):
        # A real cooldown remains in production; deterministic test deliberately
        # starts each independent quote with an empty synthetic budget window.
        with db.tx() as c:c.execute("UPDATE app_settings SET settings=settings-%s WHERE brand_id='kps'",(d.dispatch_map.ROUTE_BUDGET_KEY,))
        r=send('/api/operations/dispatch-impact',{'order_id':oid,'driver_shift_id':sid})
        assert r.status_code==200,r.text
        return r.json()
    return client,user,send,preview,order,sid,calls


def test_real_preview_assignment_and_original_deadlines(impact_service):
    client,user,send,preview,order,sid,calls=impact_service
    oid=order();quote=preview(oid)
    assert quote['status']=='within_target' and len(calls)==1
    assert quote['orders'][0]['deadline_basis']=='current_policy_assessment'
    body={'driver_shift_id':sid,'impact_quote_id':quote['quote_id']}
    first=send(f'/api/orders/{oid}/assign',body,'impact-assign')
    assert first.status_code==200,first.text
    assert send(f'/api/orders/{oid}/assign',body,'impact-assign').json()==first.json()
    with db.tx() as c:
        events=c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='dispatch.impact_accepted'",(oid,)).fetchall()
        assert len(events)==1 and events[0]['details']['deadline_preserved']
        assert events[0]['details']['orders'][0]['order_id']==oid
        assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['n']==1
        task=c.execute('SELECT id FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()
        publication=c.execute("SELECT * FROM driver_route_eta_state WHERE driver_id='ops-driver' AND manager_id='ops-owner'").fetchone()
        assert publication and publication['source_session_id']=='impact-session'
        assert 'dropoff:'+task['id'] in publication['published_stop_ids']
        assert all('candidate:' not in i for i in publication['published_stop_ids'])
        assert any(s.get('dispatch_ready_at') for s in publication['published_stops'] if s['kind']=='pickup')
        assert first.json()['dispatch_impact']['route_published']
        assert '_route_bridge' not in first.json()['dispatch_impact']

    assert len(calls)==1  # Assignment does not issue provider HTTP while locked.


def test_provider_failure_has_no_fake_eta_and_named_override_does_not_block(impact_service,monkeypatch):
    client,user,send,preview,order,sid,calls=impact_service
    monkeypatch.setattr(route_eta,'fetch_matrix',lambda *a:(_ for _ in ()).throw(ValueError('provider')))
    oid=order();quote=preview(oid)
    assert quote['status']=='unavailable' and quote['orders']==[]
    assert send(f'/api/orders/{oid}/assign',{'driver_shift_id':sid,'impact_quote_id':quote['quote_id']}).status_code==409
    body={'driver_shift_id':sid,'impact_quote_id':quote['quote_id'],'impact_reason':'estimate_unavailable','impact_contact':'not_contacted'}
    result=send(f'/api/orders/{oid}/assign',body)
    assert result.status_code==200,result.text
    assert result.json()['dispatch_impact']['status']=='unavailable'
    assert result.json()['dispatch_impact']['reason_label']=='تقدير المسار غير متاح'


def test_concurrent_quotes_for_same_driver_cannot_both_claim_old_empty_itinerary(impact_service):
    client,user,send,preview,order,sid,calls=impact_service
    a,b=order(),order();qa,qb=preview(a),preview(b)
    assert qa['status']==qb['status']=='within_target'
    def assign(pair):
        oid,quote=pair
        return send(f'/api/orders/{oid}/assign',{'driver_shift_id':sid,'impact_quote_id':quote['quote_id']}).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:codes=list(executor.map(assign,[(a,qa),(b,qb)]))
    assert sorted(codes)==[200,409]
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM driver_tasks WHERE driver_id='ops-driver'").fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM order_events WHERE event_type='dispatch.impact_accepted'").fetchone()['n']==1


@pytest.mark.parametrize('change',[
    "UPDATE dispatch_impact_quotes SET expires_at=now()-interval '1 second'",
    "UPDATE orders SET version=version+1 WHERE id=%s",
    "UPDATE driver_latest_locations SET updated_at=now()-interval '5 minutes'",
    "UPDATE driver_latest_locations SET lat=29.4 WHERE driver_id='ops-driver'",
])
def test_expired_changed_stale_or_moved_quote_requires_explicit_review(impact_service,change):
    client,user,send,preview,order,sid,calls=impact_service
    oid=order();quote=preview(oid)
    with db.tx() as c:c.execute(change,(oid,) if '%s' in change else None)
    result=send(f'/api/orders/{oid}/assign',{'driver_shift_id':sid,'impact_quote_id':quote['quote_id']})
    assert result.status_code==409,result.text
    with db.tx() as c:assert not c.execute('SELECT 1 FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()


def test_another_actor_cannot_reuse_quote_and_revoked_session_cannot_save_preview(impact_service,monkeypatch):
    client,user,send,preview,order,sid,calls=impact_service
    oid=order();quote=preview(oid)
    user['id']='ops-manager';user['role']='delivery_manager'
    assert send(f'/api/orders/{oid}/assign',{'driver_shift_id':sid,'impact_quote_id':quote['quote_id']}).status_code==409
    user.update(id='ops-owner',role='owner')
    def revoke(points,token):
        with db.tx() as c:c.execute("UPDATE sessions SET revoked_at=now() WHERE id='impact-session'")
        return [[0 if i==j else 30 for j in range(len(points))] for i in range(len(points))]
    monkeypatch.setattr(route_eta,'fetch_matrix',revoke)
    with db.tx() as c:c.execute("UPDATE app_settings SET settings=settings-%s WHERE brand_id='kps'",(d.dispatch_map.ROUTE_BUDGET_KEY,))
    result=send('/api/operations/dispatch-impact',{'order_id':oid,'driver_shift_id':sid})
    assert result.status_code==401,result.text


def test_physical_shift_lock_covers_foreign_brand_leg(shared_dual):
    import psycopg
    _,_,driver,legs=shared_dual
    native,foreign=legs['kps'][1],legs['enbishan'][1]
    def try_foreign_leg():
        try:
            with db.tx() as other:
                other.execute('SELECT id FROM shifts WHERE id=%s FOR UPDATE NOWAIT',(foreign,)).fetchone()
        except psycopg.errors.LockNotAvailable:
            return 'locked'
        return 'unlocked'
    with db.tx() as c:
        d.lock_physical_shifts(c,[native])
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(try_foreign_leg).result(timeout=5)=='locked'
