"""Isolated PostgreSQL endpoint acceptance; provider is deterministic, never paid."""
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from app import auth, db, route_eta as eta, dispatch_map
from test_driver_locations import gps_client, gps_data
from test_map_services import services, owner, pickup


@pytest.fixture
def eta_service(database,services,monkeypatch):
    old,user=services
    assert old.post('/api/operations/driver-location',json={'lat':28.4,'lng':45.9,'accuracy_m':10}).status_code==200
    owner(user)
    user.update(active=True,session_id='eta-manager-session')
    monkeypatch.setenv('MAPBOX_ENABLED','true')
    monkeypatch.setenv('MAPBOX_PUBLIC_TOKEN_KPS','pk.'+'a'*30)
    monkeypatch.setenv('MAPBOX_ROUTING_TOKEN','pk.'+'b'*30)
    with db.tx() as c:
        for number in (1,2):pickup(c,number)
        c.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES('eta-manager-session','eta-manager-token','eta-manager-csrf','map-owner',now()+interval '1 hour')")
        c.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES('eta-driver-session','eta-driver-token','eta-driver-csrf','gps-driver',now()+interval '1 hour')")
    app=FastAPI();app.include_router(eta.router)
    app.dependency_overrides[auth.current_user]=lambda:user
    calls=[]
    def matrix(points,token):
        calls.append(points)
        return [[0 if i==j else 60 for j in range(len(points))] for i in range(len(points))]
    monkeypatch.setattr(eta,'fetch_matrix',matrix)
    monkeypatch.setattr(eta.route_corridor,'fetch_geometry',lambda origin,target,token: {'type':'LineString','coordinates':[[origin['lng'],origin['lat']],[target['lng'],target['lat']]]})
    return TestClient(app),user,calls


@pytest.mark.postgres
def test_real_cache_budget_and_manual_plan_snapshot(eta_service):
    client,user,calls=eta_service
    request={'driver_id':'gps-driver','return_branch_id':'gps-branch'}
    first=client.post('/api/operations/route-eta',json=request)
    assert first.status_code==200,first.text
    data=first.json()
    assert data['status']=='ready' and data['cached'] is False
    assert len(data['stops'])==3 and data['return_seconds']==420
    second=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'})
    assert second.status_code==200 and second.json()['cached'] is True
    assert len(calls)==1
    ids=[s['id'] for s in data['stops'] if s['kind']!='return'][::-1]
    manual=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver','ordered_stop_ids':ids})
    assert manual.status_code==200 and manual.json()['ordering']=='manager'
    assert [s['id'] for s in manual.json()['stops'] if s['kind']!='return']==ids
    assert len(calls)==1
    with db.tx() as c:
        budget=c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings'][dispatch_map.ROUTE_BUDGET_KEY]
        assert budget['count']==17  # Four-coordinate matrix plus the first published road reference.
        assert c.execute('SELECT count(*) n FROM driver_route_eta_expectations').fetchone()['n']==2
        assert c.execute('SELECT ordered_stop_ids FROM driver_route_eta_state').fetchone()['ordered_stop_ids']==ids


@pytest.mark.postgres
def test_permission_revocation_rechecked_even_cached(eta_service):
    client,user,calls=eta_service
    assert client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'}).json()['status']=='ready'
    user.update(role='delivery_staff',branch_ids=['foreign'])
    result=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'})
    assert result.status_code in (403,404,409)
    assert len(calls)==1


@pytest.mark.postgres
def test_provider_failure_commits_reservation_and_does_not_store_success(eta_service,monkeypatch):
    client,user,calls=eta_service
    def unavailable(*args):raise ValueError('offline')
    monkeypatch.setattr(eta,'fetch_matrix',unavailable)
    result=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'})
    assert result.json()['status']=='unavailable'
    with db.tx() as c:
        budget=c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings'][dispatch_map.ROUTE_BUDGET_KEY]
        assert budget['count']==9
        assert c.execute('SELECT count(*) n FROM driver_route_eta_state').fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM driver_route_eta_expectations').fetchone()['n']==0
    assert client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'}).json()['reason']=='route_budget_or_cooldown'


@pytest.mark.postgres
def test_live_completion_during_provider_call_rejects_old_itinerary(eta_service,monkeypatch):
    client,user,calls=eta_service
    def changed(points,token):
        with db.tx() as c:
            c.execute("UPDATE driver_tasks SET status='delivered' WHERE id='map-task-1'")
        return [[0 if i==j else 60 for j in range(len(points))] for i in range(len(points))]
    monkeypatch.setattr(eta,'fetch_matrix',changed)
    result=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'})
    assert result.json()['reason']=='itinerary_changed'
    with db.tx() as c:assert c.execute('SELECT count(*) n FROM driver_route_eta_expectations').fetchone()['n']==0


@pytest.mark.postgres
def test_no_server_token_returns_explicit_state_without_paid_request(eta_service,monkeypatch):
    client,user,calls=eta_service
    monkeypatch.delenv('MAPBOX_ROUTING_TOKEN')
    result=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'})
    assert result.json()['reason']=='routing_token_unconfigured' and not calls
    with db.tx() as c:assert dispatch_map.ROUTE_BUDGET_KEY not in c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings']


@pytest.mark.postgres
def test_manual_reorder_requires_assign_permission(eta_service):
    client,user,calls=eta_service
    user.update(role='delivery_staff',permission_denials=['delivery_assign'])
    result=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver','ordered_stop_ids':['dropoff:map-task-1','dropoff:map-task-2']})
    assert result.status_code==403 and not calls


@pytest.mark.postgres
def test_partial_manager_scope_cannot_claim_whole_trip_eta(eta_service):
    client,user,calls=eta_service
    with db.tx() as c:
        c.execute("""INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings)
          SELECT 'hidden-branch',99998,'hidden-route-branch',brand_id,'Hidden branch','Test',settings
          FROM branches WHERE id='gps-branch'""")
        c.execute("UPDATE users SET branch_ids='[\"gps-branch\",\"hidden-branch\"]' WHERE id='gps-driver'")
        c.execute("UPDATE shifts SET branch_ids='[\"gps-branch\",\"hidden-branch\"]' WHERE id='gps-shift'")
        # A confirmed order cannot change fulfillment branch. Create the
        # hidden branch's own order/task instead of bypassing that invariant.
        c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,delivery_status,payment_method,
          subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer)
          SELECT 'map-order-hidden',brand_id,'hidden-branch',customer_id,service,delivery_status,payment_method,
          subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer
          FROM orders WHERE id='map-order-2'""")
        c.execute("""INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,picked_up_at)
          VALUES('map-task-hidden','map-order-hidden','gps-driver','gps-shift','picked_up','map-owner',now())""")
    user.update(role='delivery_staff',branch_ids=['gps-branch'])
    response=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'})
    assert response.status_code==200,response.text
    assert response.json()['reason']=='itinerary_scope_incomplete'
    assert not calls
    assert 'map-order-hidden' not in response.text and 'hidden-branch' not in response.text


def as_driver(user):
    user.update(id='gps-driver',role='driver',active=True,session_id='eta-driver-session',branch_ids=['gps-branch'],brand_id='kps')


@pytest.mark.postgres
def test_driver_consumes_published_order_and_completed_dropoff_advances(eta_service):
    client,user,calls=eta_service
    published=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver','return_branch_id':'gps-branch','ordered_stop_ids':['dropoff:map-task-2','dropoff:map-task-1']})
    assert published.status_code==200,published.text
    assert published.json()['published']
    as_driver(user)
    first=client.get('/api/operations/driver-route-plan')
    assert first.status_code==200 and first.json()['next_stop']['task_id']=='map-task-2',first.text
    with db.tx() as c:
        c.execute("UPDATE driver_tasks SET status='delivered',delivered_at=now() WHERE id='map-task-2'")
        c.execute("UPDATE orders SET delivery_status='delivered',completed_at=now() WHERE id='map-order-2'")
    advanced=client.get('/api/operations/driver-route-plan')
    assert advanced.status_code==200,advanced.text
    assert advanced.json()['next_stop']['task_id']=='map-task-1'
    assert advanced.json()['eta_status']=='stale' and advanced.json()['next_stop']['eta_seconds'] is None
    assert len(calls)==1  # Driver never calls routing provider.


@pytest.mark.postgres
def test_pickup_advances_without_reapproval_and_source_session_revocation_hides_plan(eta_service):
    client,user,calls=eta_service
    with db.tx() as c:c.execute("UPDATE driver_tasks SET status='assigned',picked_up_at=NULL WHERE id='map-task-1'")
    result=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver','publish':True})
    assert result.status_code==200,result.text
    as_driver(user)
    initial=client.get('/api/operations/driver-route-plan')
    assert initial.status_code==200 and any(s['kind']=='pickup' for s in initial.json()['stops']),initial.text
    with db.tx() as c:c.execute("UPDATE driver_tasks SET status='picked_up',picked_up_at=now() WHERE id='map-task-1'")
    picked=client.get('/api/operations/driver-route-plan')
    assert picked.status_code==200 and picked.json()['status']=='ready',picked.text
    assert all(s['kind']!='pickup' for s in picked.json()['stops'])
    with db.tx() as c:c.execute("UPDATE sessions SET revoked_at=now() WHERE id='eta-manager-session'")
    assert client.get('/api/operations/driver-route-plan').json()['status']=='unavailable'


@pytest.mark.postgres
def test_driver_cannot_fetch_plan_after_own_session_or_shift_ends(eta_service):
    client,user,calls=eta_service
    assert client.post('/api/operations/route-eta',json={'driver_id':'gps-driver','publish':True}).json()['published']
    as_driver(user)
    with db.tx() as c:c.execute("UPDATE sessions SET revoked_at=now() WHERE id='eta-driver-session'")
    assert client.get('/api/operations/driver-route-plan').status_code==401
    with db.tx() as c:
        c.execute("UPDATE sessions SET revoked_at=NULL WHERE id='eta-driver-session'")
        c.execute("UPDATE shifts SET status='closed' WHERE id='gps-shift'")
    assert client.get('/api/operations/driver-route-plan').status_code==409


@pytest.mark.postgres
@pytest.mark.parametrize('revocation',['session','permission'])
def test_provider_race_rechecks_live_caller_authority_before_cache_or_publication(eta_service,monkeypatch,revocation):
    client,user,calls=eta_service
    user['role']='delivery_manager'
    with db.tx() as c:c.execute("UPDATE users SET role='delivery_manager' WHERE id='map-owner'")
    def revoke(points,token):
        with db.tx() as c:
            if revocation=='session':
                c.execute("UPDATE sessions SET revoked_at=now() WHERE id='eta-manager-session'")
            else:
                c.execute("UPDATE users SET permission_denials='[\"drivers_view\"]' WHERE id='map-owner'")
        return [[0 if i==j else 60 for j in range(len(points))] for i in range(len(points))]
    monkeypatch.setattr(eta,'fetch_matrix',revoke)
    result=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver','publish':True})
    assert result.status_code==(401 if revocation=='session' else 403),result.text
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM driver_route_eta_state').fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM driver_route_eta_expectations').fetchone()['n']==0
        # Three matrix coordinates squared plus the reserved Directions reference.
        assert c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings'][dispatch_map.ROUTE_BUDGET_KEY]['count']==10


def second_manager(user):
    with db.tx() as c:
        c.execute("""INSERT INTO users(id,display_name,role,brand_id,branch_ids)
          VALUES('eta-second-manager','Second manager','owner','kps','[\"gps-branch\"]')""")
        c.execute("""INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at)
          VALUES('eta-second-session','eta-second-token','eta-second-csrf','eta-second-manager',now()+interval '1 hour')""")
    user.update(id='eta-second-manager',role='owner',session_id='eta-second-session',
                brand_id='kps',branch_ids=['gps-branch'])


@pytest.mark.postgres
def test_second_manager_reuses_recent_matrix_without_inheriting_publication(eta_service):
    client,user,calls=eta_service
    first=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver','publish':True,
        'ordered_stop_ids':['dropoff:map-task-2','dropoff:map-task-1']})
    assert first.status_code==200 and first.json()['status']=='ready',first.text
    second_manager(user)
    second=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'})
    assert second.status_code==200,second.text
    result=second.json()
    assert result['status']=='ready' and result['cached'] is True,result
    assert result['ordering']=='road_duration' and result['published'] is False
    assert [s['id'] for s in result['stops']]==['dropoff:map-task-1','dropoff:map-task-2']
    assert result['return_at'] is None and result['calculated_at']==first.json()['calculated_at']
    assert len(calls)==1
    with db.tx() as c:
        assert c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings'][dispatch_map.ROUTE_BUDGET_KEY]['count']==10
        own=c.execute("SELECT * FROM driver_route_eta_state WHERE manager_id='eta-second-manager'").fetchone()
        assert own['published_at'] is None and own['source_session_id'] is None
        assert own['ordered_stop_ids']==[] and own['return_branch_id'] is None


@pytest.mark.postgres
def test_shared_recent_matrix_does_not_match_changed_itinerary(eta_service):
    client,user,calls=eta_service
    assert client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'}).json()['status']=='ready'
    second_manager(user)
    with db.tx() as c:
        c.execute("UPDATE driver_tasks SET status='delivered',delivered_at=now() WHERE id='map-task-2'")
        c.execute("UPDATE orders SET delivery_status='delivered',completed_at=now() WHERE id='map-order-2'")
    result=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'})
    assert result.status_code==200,result.text
    assert result.json()['reason']=='route_budget_or_cooldown'
    assert result.json()['stops']==[] and len(calls)==1


@pytest.mark.postgres
def test_shared_matrix_cannot_override_second_manager_branch_scope(eta_service):
    client,user,calls=eta_service
    assert client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'}).json()['status']=='ready'
    second_manager(user)
    user.update(role='delivery_staff',branch_ids=['foreign'])
    response=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'})
    assert response.status_code in (403,404,409),response.text
    assert len(calls)==1
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM driver_route_eta_state WHERE manager_id='eta-second-manager'").fetchone()


@pytest.mark.postgres
@pytest.mark.parametrize('age_seconds',[61,-30])
def test_shared_matrix_rejects_expired_and_future_timestamps(eta_service,age_seconds):
    from datetime import timedelta
    client,user,calls=eta_service
    assert client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'}).json()['status']=='ready'
    second_manager(user)
    with db.tx() as c:
        c.execute("UPDATE driver_route_eta_state SET calculated_at=%s WHERE manager_id='map-owner'",
                  (datetime.now(timezone.utc)-timedelta(seconds=age_seconds),))
    result=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'})
    assert result.status_code==200,result.text
    assert result.json()['reason']=='route_budget_or_cooldown' and len(calls)==1


@pytest.mark.postgres
def test_own_publication_refreshes_from_new_shared_matrix_without_foreign_intent(eta_service):
    from datetime import timedelta
    client,user,calls=eta_service
    original=dict(user)
    own_order=['dropoff:map-task-2','dropoff:map-task-1']
    first=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver','publish':True,'ordered_stop_ids':own_order})
    assert first.status_code==200 and first.json()['published'],first.text
    old=datetime.now(timezone.utc)-timedelta(seconds=61)
    with db.tx() as c:
        c.execute("UPDATE driver_route_eta_state SET calculated_at=%s,published_calculated_at=%s WHERE manager_id='map-owner'",(old,old))
        settings=c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings']
        settings[dispatch_map.ROUTE_BUDGET_KEY]['recent']={}
        c.execute("UPDATE app_settings SET settings=%s WHERE brand_id='kps'",(db.json(settings),))
    second_manager(user)
    foreign=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'})
    assert foreign.status_code==200 and foreign.json()['status']=='ready',foreign.text
    assert not foreign.json()['cached']
    user.clear();user.update(original)
    refreshed=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'})
    assert refreshed.status_code==200,refreshed.text
    data=refreshed.json()
    assert data['status']=='ready' and data['cached'] and data['published'],data
    assert data['calculated_at']==foreign.json()['calculated_at']
    assert [s['id'] for s in data['stops']]==own_order
    assert len(calls)==2
    with db.tx() as c:
        saved=c.execute("SELECT * FROM driver_route_eta_state WHERE manager_id='map-owner'").fetchone()
        assert saved['source_session_id']=='eta-manager-session'
        assert saved['published_calculated_at']>old
        assert saved['published_stop_ids']==own_order
        forecasts=c.execute("SELECT calculated_at FROM driver_route_eta_expectations WHERE manager_id='map-owner' ORDER BY calculated_at DESC").fetchall()
        assert len(forecasts)==2 and forecasts[0]['calculated_at']==saved['published_calculated_at']
        assert c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings'][dispatch_map.ROUTE_BUDGET_KEY]['count']==19
    as_driver(user)
    driver=client.get('/api/operations/driver-route-plan')
    assert driver.status_code==200 and driver.json()['eta_status']=='ready',driver.text
    assert driver.json()['next_stop']['task_id']=='map-task-2'


@pytest.mark.postgres
def test_shared_cached_publication_reserves_geometry_independently_of_matrix_cooldown(eta_service,monkeypatch):
    client,user,calls=eta_service
    geometry_calls=[]
    def geometry(origin,target,token):
        geometry_calls.append((origin,target))
        return {'type':'LineString','coordinates':[[origin['lng'],origin['lat']],[target['lng'],target['lat']]]}
    monkeypatch.setattr(eta.route_corridor,'fetch_geometry',geometry)
    assert client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'}).json()['status']=='ready'
    second_manager(user)
    published=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver','publish':True})
    assert published.status_code==200,published.text
    data=published.json()
    assert data['status']=='ready' and data['cached'] and data['published']
    assert data['corridor_status']=='ready' and len(calls)==1 and len(geometry_calls)==1
    with db.tx() as c:
        budget=c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings'][dispatch_map.ROUTE_BUDGET_KEY]
        assert budget['count']==10
        assert 'driver:gps-driver' in budget['recent'] and 'corridor:driver:gps-driver' in budget['recent']
        row=c.execute("SELECT source_session_id,published_at FROM driver_route_eta_state WHERE manager_id='eta-second-manager'").fetchone()
        assert row['source_session_id']=='eta-second-session' and row['published_at']
        assert c.execute("SELECT published_at FROM driver_route_eta_state WHERE manager_id='map-owner'").fetchone()['published_at'] is None


@pytest.mark.postgres
def test_corridor_cooldown_keeps_cached_eta_and_never_inherits_foreign_geometry(eta_service):
    client,user,calls=eta_service
    first=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver','publish':True})
    assert first.json()['corridor_status']=='ready'
    second_manager(user)
    second=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver','publish':True})
    assert second.status_code==200,second.text
    data=second.json()
    assert data['status']=='ready' and data['cached'] and data['published']
    assert data['corridor_status']=='unavailable' and len(calls)==1
    with db.tx() as c:
        assert c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings'][dispatch_map.ROUTE_BUDGET_KEY]['count']==10
        assert c.execute("SELECT road_corridor FROM driver_route_eta_state WHERE manager_id='eta-second-manager'").fetchone()['road_corridor'] is None


@pytest.mark.postgres
def test_cached_eta_survives_missing_optional_corridor_token(eta_service,monkeypatch):
    client,user,calls=eta_service
    assert client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'}).json()['status']=='ready'
    monkeypatch.delenv('MAPBOX_ROUTING_TOKEN')
    result=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver','publish':True})
    assert result.status_code==200,result.text
    data=result.json()
    assert data['status']=='ready' and data['cached'] and data['published']
    assert data['corridor_status']=='unavailable' and len(calls)==1


@pytest.mark.postgres
def test_optional_corridor_monthly_cap_commits_successful_matrix_and_keeps_eta(eta_service,monkeypatch):
    client,user,calls=eta_service
    monkeypatch.setattr(dispatch_map,'ROUTE_MONTHLY_LIMIT',9)
    result=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver','publish':True})
    assert result.status_code==200,result.text
    data=result.json()
    assert data['status']=='ready' and data['published'] and data['corridor_status']=='unavailable'
    assert len(calls)==1
    with db.tx() as c:
        budget=c.execute("SELECT settings FROM app_settings WHERE brand_id='kps'").fetchone()['settings'][dispatch_map.ROUTE_BUDGET_KEY]
        assert budget['count']==9 and 'corridor:driver:gps-driver' not in budget['recent']
        assert c.execute("SELECT published_at FROM driver_route_eta_state WHERE manager_id='map-owner'").fetchone()['published_at']
