from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from app import auth, db, map_services, dispatch_map, delivery_tracking
from test_driver_locations import gps_client, gps_data


def test_location_quality_does_not_claim_missing_or_inaccurate_live():
    now=datetime.now(timezone.utc)
    assert map_services.quality({},now)['state']=='missing'
    for age,accuracy,state in [(5,10,'live'),(5,300,'approximate'),(121,10,'stale')]:
        assert map_services.quality({'updated_at':now-timedelta(seconds=age),'accuracy_m':accuracy},now)['state']==state


def test_multi_stop_is_deterministic_and_does_not_mutate_input():
    origin={'lat':28.,'lng':45.}
    tasks=[{'id':'far','location':{'lat':29.,'lng':45.}}, {'id':'near','location':{'lat':28.01,'lng':45.}}]
    assert [t['id'] for t in map_services.ordered_stops(origin,tasks)]==['near','far']
    assert tasks[0]['id']=='far'


@pytest.fixture
def services(gps_data):
    _,user=gps_data
    app=FastAPI();app.include_router(dispatch_map.router);app.include_router(map_services.router);app.include_router(delivery_tracking.router)
    app.dependency_overrides[auth.current_user]=lambda:user
    client=TestClient(app)
    with db.tx() as c:
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES('gps-branch',99999,'gps-services','kps','Services branch','Test',%s)",
          (db.json({'location':{'lat':28.4,'lng':45.9},'location_verified':True,'coverage':{'type':'radius','center':{'lat':28.4,'lng':45.9},'radius_meters':10000},'delivery_fee_halalas':500}),))
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('map-owner','Map Owner','owner','kps',%s)",(db.json(['gps-branch']),))
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('map-customer','Customer','customer','kps','[]')")
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES('map-manager','manager','map-owner','kps',%s)",(db.json(['gps-branch']),))
        c.execute("UPDATE shifts SET manager_shift_id='map-manager' WHERE id='gps-shift'")
        for i in (1,2):
            c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer)
            VALUES(%s,'kps','gps-branch','map-customer','delivery','pending','cash',1000,500,1500,'{}',%s)""",
            ('map-order-'+str(i),db.json({'location':{'lat':28.41+i/100,'lng':45.91}})))
    return client,user


def owner(user):
    user.update(id='map-owner',role='owner',branch_ids=['gps-branch'],brand_id='kps')


def pickup(c,number=1):
    c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,picked_up_at) VALUES(%s,%s,'gps-driver','gps-shift','picked_up','map-owner',now())",('map-task-'+str(number),'map-order-'+str(number)))


@pytest.mark.postgres
def test_health_records_per_shift_and_no_position_history(services):
    client,user=services
    assert client.post('/api/operations/driver-location',json={'lat':28.4,'lng':45.9,'accuracy_m':10}).status_code==200
    assert client.post('/api/operations/driver-location',json={'lat':28.5,'lng':45.9,'accuracy_m':10}).json()['throttled']
    owner(user)
    report=client.get('/api/operations/map-services/health?shift_id=gps-shift')
    assert report.status_code==200,report.text
    assert report.json()['received_count']==1
    assert not any(k in report.json()['buckets'][0] for k in ('lat','lng','location'))
    user['brand_id']='enbishan'
    assert client.get('/api/operations/map-services/health?shift_id=gps-shift').status_code==404


@pytest.mark.postgres
def test_recommendation_filters_stale_revoked_capacity_and_other_brand(services):
    client,user=services
    client.post('/api/operations/driver-location',json={'lat':28.4,'lng':45.9,'accuracy_m':10})
    owner(user)
    response=client.get('/api/operations/map-services/recommendations?order_id=map-order-1')
    assert response.status_code==200,response.text
    assert response.json()['recommended_shift_id']=='gps-shift'
    with db.tx() as c:c.execute("UPDATE driver_latest_locations SET updated_at=now()-interval '3 minutes'")
    assert client.get('/api/operations/map-services/recommendations?order_id=map-order-1').json()['recommended_shift_id'] is None
    with db.tx() as c:c.execute("UPDATE users SET active=false WHERE id='gps-driver'")
    assert client.get('/api/operations/map-services/recommendations?order_id=map-order-1').json()['drivers']==[]
    user['brand_id']='enbishan'
    assert client.get('/api/operations/map-services/recommendations?order_id=map-order-1').status_code==404


@pytest.mark.postgres
def test_alerts_only_for_active_deliveries_and_authorized_branches(services):
    client,user=services
    owner(user)
    assert client.get('/api/operations/map-services/alerts').json()['alerts']==[]
    with db.tx() as c:
        pickup(c)
        c.execute("UPDATE driver_tasks SET picked_up_at=now()-interval '5 minutes'")
    assert client.get('/api/operations/map-services/alerts').json()['alerts'][0]['kind']=='location_missing'
    user.update(role='delivery_staff',branch_ids=['wrong'])
    assert client.get('/api/operations/map-services/alerts').json()['alerts']==[]


@pytest.mark.postgres
def test_multiple_route_permit_revalidates_targets(services,monkeypatch):
    client,user=services
    client.post('/api/operations/driver-location',json={'lat':28.4,'lng':45.9,'accuracy_m':10})
    owner(user)
    monkeypatch.setenv('MAPBOX_ENABLED','true');monkeypatch.setenv('MAPBOX_PUBLIC_TOKEN_KPS','pk.'+'a'*30)
    with db.tx() as c:
        for i in (1,2):pickup(c,i)
    result=client.post('/api/operations/map-services/plan',json={'driver_id':'gps-driver'})
    assert result.status_code==200,result.text
    assert len(result.json()['stops'])==2 and not result.json()['changes_assignment']
    assert client.post('/api/operations/map-services/plan',json={'driver_id':'gps-driver','order_id':'forged'}).status_code==422


@pytest.mark.postgres
def test_customer_capability_minimal_data_revocation_and_terminal_stop(services):
    client,user=services
    client.post('/api/operations/driver-location',json={'lat':28.4,'lng':45.9,'accuracy_m':10})
    owner(user)
    with db.tx() as c:pickup(c)
    link=client.post('/api/operations/orders/map-order-1/tracking-link')
    assert link.status_code==200,link.text
    token=link.json()['path'].split('#')[1]
    result=client.post('/api/delivery-tracking/view',json={'token':token})
    assert result.status_code==200,result.text
    assert result.json()['status']=='on_the_way'
    assert not any(k in result.json() for k in ('driver_id','phone','customer','display_name','order_id'))
    with db.tx() as c:pickup(c,2)
    assert client.post('/api/delivery-tracking/view',json={'token':token}).json()['location'] is None
    with db.tx() as c:c.execute("UPDATE driver_tasks SET status='delivered',delivered_at=now() WHERE id='map-task-2'")
    with db.tx() as c:c.execute("UPDATE orders SET completed_at=now() WHERE id='map-order-1'")
    result=client.post('/api/delivery-tracking/view',json={'token':token}).json()
    assert result['status']=='ended' and result['location'] is None
    assert client.delete('/api/operations/orders/map-order-1/tracking-link').status_code==200
    assert client.post('/api/delivery-tracking/view',json={'token':token}).status_code==404


@pytest.mark.postgres
def test_tracking_link_replacement_expiry_and_cross_brand(services):
    client,user=services;owner(user)
    first=client.post('/api/operations/orders/map-order-1/tracking-link').json()['path'].split('#')[1]
    second=client.post('/api/operations/orders/map-order-1/tracking-link').json()['path'].split('#')[1]
    assert first!=second
    assert client.post('/api/delivery-tracking/view',json={'token':first}).status_code==404
    with db.tx() as c:c.execute("UPDATE delivery_tracking_links SET expires_at=now()-interval '1 second'")
    assert client.post('/api/delivery-tracking/view',json={'token':second}).status_code==404
    user['brand_id']='enbishan'
    assert client.post('/api/operations/orders/map-order-1/tracking-link').status_code==404


@pytest.mark.postgres
def test_zone_and_trip_report_scopes(services):
    client,user=services;owner(user)
    assert client.get('/api/operations/map-services/zones').json()['zones'][0]['delivery_fee_halalas']==500
    with db.tx() as c:
        pickup(c)
        c.execute("UPDATE driver_tasks SET delivered_at=now()+interval '5 minutes',status='delivered'")
    report=client.get('/api/operations/map-services/trips?shift_id=gps-shift')
    assert report.status_code==200,report.text
    assert report.json()['trips'][0]['delivery_seconds']==300
    assert report.json()['trips'][0]['direct_distance_m']>0
    user.update(role='delivery_staff',branch_ids=['wrong'])
    assert client.get('/api/operations/map-services/zones').json()['zones']==[]
    assert client.get('/api/operations/map-services/trips?shift_id=gps-shift').status_code==403
