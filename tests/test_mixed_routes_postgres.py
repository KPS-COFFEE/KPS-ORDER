"""Real synthetic workdays: mixed-brand route endpoints, no paid provider calls.

Requires TEST_DATABASE_URL to an explicitly named local test database. These
witnesses use actual grants, sessions, shift groups, tasks, GPS and budget rows.
"""
import pytest
from uuid import uuid4
from app import db, route_eta, dispatch_map
from test_central_workdays import central, open_days
from test_enbishan_dual_brand import dual


@pytest.fixture
def mixed_day(central,monkeypatch):
    client,actor,post,_,order=central
    _,driver=open_days(central)
    legs={s['brand_id']:s['id'] for s in driver['shifts']}
    monkeypatch.setenv('MAPBOX_ENABLED','true')
    for brand in ('KPS','ENBISHAN'):
        monkeypatch.setenv('MAPBOX_PUBLIC_TOKEN_'+brand,'pk.'+'a'*30)
    monkeypatch.setenv('MAPBOX_ROUTING_TOKEN','pk.'+'b'*30)
    with db.tx() as c:
        for branch in ['kps-branch-1','enbishan-test-1']:
            c.execute("UPDATE branches SET settings=settings||%s WHERE id=%s",
                      (db.json({'location_verified':True,'location':{'lat':28.4,'lng':45.9}}),branch))
    anchor=driver['shift']
    post('driver','/api/operations/driver-location',{'lat':28.4,'lng':45.9,'accuracy_m':10},brand=anchor['brand_id'])
    calls=[]
    def matrix(points,token):
        calls.append(points)
        return [[0 if i==j else 60 for j in range(len(points))] for i in range(len(points))]
    monkeypatch.setattr(route_eta,'fetch_matrix',matrix)
    monkeypatch.setattr(route_eta.route_corridor,'fetch_geometry',lambda origin,target,token:
        {'type':'LineString','coordinates':[[origin['lng'],origin['lat']],[target['lng'],target['lat']]]})
    def add(brand,index,status='picked_up'):
        # The location belongs to the original confirmed customer snapshot.
        # Never mutate that immutable snapshot after assignment/confirmation.
        oid=uuid4().hex
        branch='kps-branch-1' if brand=='kps' else 'enbishan-test-1'
        with db.tx() as c:
            c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,
              preparation_status,delivery_status,payment_method,subtotal_halalas,
              delivery_fee_halalas,total_halalas,snapshot,customer)
              VALUES(%s,%s,%s,%s,'delivery','ready','pending','cash',1000,0,1000,%s,%s)""",
              (oid,brand,branch,'dual-'+brand+'-customer',
               db.json({'items':[],'branch_name':branch,'delivery_workflow':'manager_dispatch'}),
               db.json({'name':'SYNTHETIC','location':{'lat':28.41+index*.001,'lng':45.91}})))
        task=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':legs[brand]},brand)['task']
        with db.tx() as c:
            c.execute("UPDATE driver_tasks SET status=%s,picked_up_at=CASE WHEN %s='picked_up' THEN now() ELSE NULL END WHERE id=%s",(status,status,task['id']))
        return task['id']
    return actor,post,legs,calls,add


@pytest.mark.postgres
@pytest.mark.parametrize('counts',[(1,1),(2,1)])
def test_real_mixed_route_and_eta_retain_all_brand_orders(mixed_day,counts):
    actor,post,legs,calls,add=mixed_day
    expected={add(brand,i) for brand,count in zip(('kps','enbishan'),counts) for i in range(count)}
    response=actor('manager').get('/api/central-operations/driver-route-context?driver_id=dual-driver')
    assert response.status_code==200,response.text
    context=response.json()
    assert {t['id'] for t in context['tasks']}==expected
    assert len(context['stops'])==sum(counts)
    assert {t['brand_id'] for t in context['tasks']}=={'kps','enbishan'}
    assert {b['brand_id'] for b in context['return_branches']}=={'kps','enbishan'}
    assert {t['shift_id'] for t in context['tasks']}==set(legs.values())
    plan=post('manager','/api/operations/route-eta',{'driver_id':'dual-driver','return_branch_id':'enbishan-test-1'})
    assert plan['status']=='ready',plan
    assert {s['task_id'] for s in plan['stops'] if s.get('task_id')}==expected
    assert plan['return_at'] and plan['delivery_complete_at']
    assert len(calls)==1
    coordinates=sum(counts)+2  # origin, each customer, explicitly chosen return
    with db.tx() as c:
        budget=c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings'][dispatch_map.ROUTE_BUDGET_KEY]
        assert budget['count']==coordinates**2
    # A cached refresh is not another provider request or budget reservation.
    refreshed=post('manager','/api/operations/route-eta',{'driver_id':'dual-driver'})
    assert refreshed['status']=='ready' and refreshed['cached'] is True
    assert len(calls)==1


@pytest.mark.postgres
def test_real_pending_second_brand_pickup_survives_plan_with_prerequisite(mixed_day):
    actor,post,legs,calls,add=mixed_day
    first=add('kps',1)
    second=add('enbishan',2,status='assigned')
    response=actor('manager').get('/api/central-operations/driver-route-context?driver_id=dual-driver')
    assert response.status_code==200,response.text
    context=response.json()
    assert {t['id'] for t in context['tasks']}=={first}
    stops={s['id']:s for s in context['stops']}
    assert stops['dropoff:'+second]['prerequisite_ids']==['pickup:'+second]
    plan=post('manager','/api/operations/route-eta',{'driver_id':'dual-driver'})
    assert plan['status']=='ready',plan
    ids=[s['id'] for s in plan['stops']]
    pickup=next(s for s in plan['stops'] if s['kind']=='pickup')
    assert second in pickup['task_ids']
    assert ids.index(pickup['id'])<ids.index('dropoff:'+second)
    assert set(ids)=={'dropoff:'+first,pickup['id'],'dropoff:'+second}


@pytest.mark.postgres
def test_revoked_brand_cannot_be_hidden_while_claiming_complete_eta(mixed_day):
    actor,post,legs,calls,add=mixed_day
    add('kps',1);add('enbishan',2)
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-manager' AND brand_id='enbishan'")
    result=post('manager','/api/operations/route-eta',{'driver_id':'dual-driver'})
    assert result['status']=='unavailable'
    assert result['reason']=='itinerary_scope_incomplete'
    assert calls==[]
    assert result['stops']==[]
    assert result['next_stop'] is None
    assert 'mixed-order-enbishan' not in str(result)


@pytest.mark.postgres
def test_mixed_publication_corridor_alert_uses_central_viewer_without_cross_brand_leak(mixed_day,monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app import map_services, multibrand
    actor,post,legs,calls,add=mixed_day
    add('kps',1);add('enbishan',2)
    plan=post('manager','/api/operations/route-eta',{'driver_id':'dual-driver','publish':True})
    assert plan['status']=='ready' and plan['corridor_status']=='ready',plan
    target_brand=plan['next_stop']['brand_id']
    now=datetime.now(timezone.utc)
    with db.tx() as c:
        saved=c.execute("SELECT road_corridor FROM driver_route_eta_state WHERE driver_id='dual-driver' AND manager_id='dual-manager'").fetchone()
        corridor=dict(saved['road_corridor'],calculated_at=(now-timedelta(minutes=3)).isoformat())
        c.execute("UPDATE driver_route_eta_state SET road_corridor=%s WHERE driver_id='dual-driver' AND manager_id='dual-manager'",(db.json(corridor),))
        session=c.execute("SELECT id FROM sessions WHERE user_id='dual-driver' AND revoked_at IS NULL ORDER BY expires_at DESC LIMIT 1").fetchone()['id']
        for age in (90,60,1):
            c.execute("""INSERT INTO driver_tracking_points(id,driver_id,brand_id,shift_id,session_id,lat,lng,accuracy_m,captured_at)
              VALUES(%s,'dual-driver',%s,%s,%s,28.42,45.95,10,%s)""",
              (uuid4().hex,target_brand,legs[target_brand],session,now-timedelta(seconds=age)))
    observed=[]
    actual=route_eta.published_plan_for
    def lookup(c,user,driver):
        result=actual(c,user,driver)
        observed.append((bool(user.get('central_operations')),result))
        return result
    monkeypatch.setattr(route_eta,'published_plan_for',lookup)
    response=actor('manager').get('/api/operations/map-services/alerts')
    assert response.status_code==200,response.text
    alerts=[a for a in response.json()['alerts'] if a['kind']=='route_corridor_deviation']
    assert len(alerts)==1,response.json()
    assert alerts[0]['brand_id']==target_brand
    assert alerts[0]['shift_id']==legs[target_brand]
    assert alerts[0]['basis']=='road_geometry' and alerts[0]['distance_m']>150
    assert observed and all(central and view and view.get('road_corridor') for central,view in observed)
    # A plain canonical principal must not inherit the private central viewer.
    with db.tx() as c:
        user=c.execute("SELECT * FROM users WHERE id='dual-manager'").fetchone()
        canonical=multibrand.project(c,user,'kps')
    observed.clear()
    scoped=map_services.alerts(canonical)
    assert not [a for a in scoped['alerts'] if a['kind']=='route_corridor_deviation']
    assert all(not central for central,_ in observed)
    assert all(a['brand_id']=='kps' for a in scoped['alerts'] if 'brand_id' in a)
