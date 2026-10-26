"""Real synthetic workdays: mixed-brand route endpoints, no paid provider calls.

Requires TEST_DATABASE_URL to an explicitly named local test database. These
witnesses use actual grants, sessions, shift groups, tasks, GPS and budget rows.
"""
import pytest
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
    def add(brand,index,status='picked_up'):
        oid=order(brand)
        task=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':legs[brand]},brand)['task']
        with db.tx() as c:
            c.execute('UPDATE orders SET customer=customer||%s WHERE id=%s',
                      (db.json({'location':{'lat':28.41+index*.001,'lng':45.91}}),oid))
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
    assert 'stops' not in result
