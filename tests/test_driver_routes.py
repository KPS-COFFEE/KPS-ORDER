"""Route permits only: never call a paid/external Directions service."""
from datetime import datetime, timezone, timedelta
import pytest
from fastapi import HTTPException
from app import dispatch_map as routes
from test_driver_locations import gps_client, gps_data
from app import db


class BudgetConnection:
    def __init__(self, settings):
        self.settings = settings
        self.now = datetime(2026, 10, 20, tzinfo=timezone.utc)
        self.writes = 0
    def execute(self, sql, args=None):
        if sql.startswith('SELECT'):
            assert "brand_id='kps' FOR UPDATE" in sql
        else:
            self.writes += 1
            self.settings = args[0].obj
        return self
    def fetchone(self):
        return {'settings': self.settings, 'budget_now': self.now}


def test_global_budget_and_independent_driver_manager_throttles():
    c = BudgetConnection({'unrelated': True})
    routes.consume_route_budget(c, 'd1', 'm1')
    assert c.settings['unrelated'] is True
    for driver, manager in [('d1', 'm2'), ('d2', 'm1')]:
        with pytest.raises(HTTPException) as err:
            routes.consume_route_budget(c, driver, manager)
        assert err.value.status_code == 429
    c.now += timedelta(seconds=60)
    routes.consume_route_budget(c, 'd1', 'm1')
    assert c.settings[routes.ROUTE_BUDGET_KEY]['count'] == 2
    c.settings[routes.ROUTE_BUDGET_KEY]['count'] = routes.ROUTE_MONTHLY_LIMIT
    c.now += timedelta(seconds=60)
    with pytest.raises(HTTPException) as err:
        routes.consume_route_budget(c, 'd2', 'm2')
    assert err.value.status_code == 429 and c.writes == 2
    c.now = c.now.replace(month=11)
    routes.consume_route_budget(c, 'd2', 'm2')
    assert c.settings[routes.ROUTE_BUDGET_KEY]['count'] == 1


@pytest.mark.parametrize('budget', [None, {}, {'month':'2026-10','count':-1,'recent':{}},
    {'month':'2026-10','count':0,'recent':{'driver:a':'bad'}}])
def test_budget_corruption_fails_closed(budget):
    c = BudgetConnection({routes.ROUTE_BUDGET_KEY: budget})
    with pytest.raises(HTTPException) as err:
        routes.consume_route_budget(c, 'd', 'm')
    assert err.value.status_code == 503 and c.writes == 0


@pytest.mark.parametrize('body', [{'driver_id':'d'}, {'driver_id':'d','task_id':'t','return_branch_id':'b'},
    {'driver_id':'d','task_id':'t','lat':25}])
def test_route_request_strict(body):
    with pytest.raises(ValueError):
        routes.DriverRouteRequest(**body)


@pytest.mark.parametrize('value', [None, {}, {'lat':True,'lng':45}, {'lat':float('nan'),'lng':45}, {'lat':91,'lng':45}])
def test_route_point_rejects_invalid(value):
    assert routes.route_point(value) is None


@pytest.mark.postgres
def test_route_authorization_freshness_and_target_revalidation(gps_data, monkeypatch):
    client, user = gps_data
    monkeypatch.setenv('MAPBOX_ENABLED', 'true')
    monkeypatch.setenv('MAPBOX_PUBLIC_TOKEN_KPS', 'pk.' + 'a' * 30)
    assert client.post('/api/operations/driver-location', json={'lat':28.4,'lng':45.9,'accuracy_m':10}).status_code == 200
    endpoint = '/api/operations/driver-route-context'
    assert client.get(endpoint, params={'driver_id':'gps-driver'}).status_code == 403
    user.update(role='delivery_manager')
    response = client.get(endpoint, params={'driver_id':'gps-driver'})
    assert response.status_code == 200, response.text
    assert response.json()['tasks'] == []
    with db.tx() as c:
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES('gps-branch',99999,'gps-route','kps','Route branch','Test',%s)",
                  (db.json({'location':{'lat':28.5,'lng':45.8},'location_verified':True}),))
    permitted = client.post(endpoint, json={'driver_id':'gps-driver','return_branch_id':'gps-branch'})
    assert permitted.status_code == 200, permitted.text
    assert permitted.json()['destination'] == {'lat':28.5,'lng':45.8}
    assert permitted.json()['kind'] == 'return_branch'
    assert client.post(endpoint, json={'driver_id':'gps-driver','return_branch_id':'gps-branch'}).status_code == 429
    assert client.post(endpoint, json={'driver_id':'gps-driver','task_id':'invented'}).status_code == 409
    user['branch_ids'] = ['wrong']
    assert client.get(endpoint, params={'driver_id':'gps-driver'}).status_code == 403
    user.update(branch_ids=['gps-branch'], permission_denials=['drivers_view'])
    assert client.get(endpoint, params={'driver_id':'gps-driver'}).status_code == 403
    user['permission_denials'] = []
    with db.tx() as c:
        c.execute("UPDATE driver_latest_locations SET updated_at=now()-interval '121 seconds'")
    assert client.get(endpoint, params={'driver_id':'gps-driver'}).status_code == 409
