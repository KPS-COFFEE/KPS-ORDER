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
        assert budget['count']==16  # Four coordinates squared, not one fake unit.
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
        c.execute("UPDATE orders SET branch_id='hidden-branch' WHERE id='map-order-2'")
    user.update(role='delivery_staff',branch_ids=['gps-branch'])
    response=client.post('/api/operations/route-eta',json={'driver_id':'gps-driver'})
    assert response.status_code==200,response.text
    assert response.json()['reason']=='itinerary_scope_incomplete'
    assert not calls
    assert 'map-order-2' not in response.text and 'hidden-branch' not in response.text


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
