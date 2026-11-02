"""Driver fuel report reads ledger scope, corrected metrics and physical segments."""
import pytest
from app import db
from test_operations import ops
from test_fleet import seed, delegate
pytestmark=pytest.mark.postgres


def test_report_filters_and_incomplete_totals_and_role_denials(ops):
    actor,*_=ops;client=actor('owner')
    with db.tx() as c:
        seed(c,'fuel-rpt-one','REPORT ONE',12,driver='ops-driver',km=210,liters=21,state='open')
        seed(c,'fuel-rpt-two','REPORT TWO',13,driver='ops-driver2',km=80,liters=8,state='open')
        c.execute("UPDATE shifts SET vehicle_log=jsonb_set(jsonb_set(vehicle_log,'{refuels,0,amount_halalas}','4200'),'{end}',%s),status='closed' WHERE id='fuel-rpt-one'", (db.json({'odometer_km':1210,'fuel_percent':100}),))
        city=c.execute("SELECT city FROM branches WHERE id='ops-branch'").fetchone()['city']
    base='/api/fleet/driver-report?start=2026-10-12&end=2026-10-13'
    result=client.get(base+'&limit=1');assert result.status_code==200,result.text
    data=result.json();assert data['total']==2 and len(data['rows'])==1
    assert data['summary']['distance_km']==210
    assert data['summary']['distance_missing_records']==1
    assert data['summary']['fuel_amount_known_halalas']==4200
    assert data['summary']['fuel_amount_halalas'] is None
    one=client.get(base+'&driver_id=ops-driver').json()
    assert one['total']==1 and one['summary']['fuel_amount_halalas']==4200
    assert one['rows'][0]['driver_name']
    assert client.get(base+'&city='+city).status_code==200
    assert client.get(base+'&city=outside-scope').status_code==422
    assert client.get('/api/fleet/driver-report/options').status_code==200
    actor('driver');assert client.get(base).status_code==403
    actor('manager');assert client.get(base).status_code==403
    delegate(actor);assert client.get(base).status_code==200
    with db.tx() as c:c.execute("UPDATE users SET permission_denials=%s WHERE id='ops-fawzi'", (db.json(["vehicle_maintenance_manage"]),))
    assert client.get(base).status_code==403


def test_fuel_report_cross_scope_and_missing_refuel_are_explicit(ops):
    actor,*_=ops;client=actor('owner')
    with db.tx() as c:
        seed(c,'fuel-visible','VISIBLE',12,state='open')
        seed(c,'fuel-hidden','HIDDEN',12,bids=['ops-other'])
        c.execute("UPDATE shifts SET vehicle_log=jsonb_set(vehicle_log,'{refuels}','[]') WHERE id='fuel-visible'")
    delegate(actor)
    result=client.get('/api/fleet/driver-report?start=2026-10-12&end=2026-10-12')
    assert result.status_code==200,result.text
    data=result.json();assert data['total']==1
    assert data['rows'][0]['shift_id']=='fuel-visible'
    assert data['rows'][0]['fuel_record_missing'] is True
    assert data['rows'][0]['fuel_amount_halalas'] is None
    assert data['summary']['fuel_record_missing']==1


def test_report_fails_closed_when_scope_revoked_during_read(ops,monkeypatch):
    from app import driver_fuel_report
    actor,*_=ops;client=actor('owner')
    with db.tx() as c:seed(c,'fuel-grant-change','REVOKED',12)
    delegate(actor)
    original=driver_fuel_report.fuel_values
    called=False
    def revoke(log):
        nonlocal called
        if not called:
            called=True
            with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-fawzi'")
        return original(log)
    monkeypatch.setattr(driver_fuel_report,'fuel_values',revoke)
    response=client.get('/api/fleet/driver-report?start=2026-10-12&end=2026-10-12')
    assert response.status_code==403,response.text
    assert response.json()['detail']['code']=='FUEL_REPORT_SCOPE_CHANGED'


def test_handover_report_preserves_each_vehicle_fuel_without_double_count(ops):
    from copy import deepcopy
    actor,*_=ops;client=actor('owner')
    with db.tx() as c:
        initial=seed(c,'fuel-handover','REPORT OLD',12,state='open')
        old=deepcopy(initial['vehicle_log']);old['refuels'][0]['amount_halalas']=1000
        old['end']={'odometer_km':1210,'fuel_percent':100,'recorded_at':'2026-10-12T18:00:00+00:00'}
        current={'vehicle':{'plate':'REPORT NEW','type':'Test'},
                 'start':{'odometer_km':2000,'fuel_percent':100,'recorded_at':'2026-10-12T18:00:00+00:00'},
                 'end':{'odometer_km':2100,'fuel_percent':100,'recorded_at':'2026-10-12T20:00:00+00:00'},
                 'refuels':[{'liters':'10','amount_halalas':2300,'recorded_at':'2026-10-12T19:00:00+00:00'}],
                 'previous_segments':[old]}
        c.execute("INSERT INTO fleet_vehicles(id,brand_id,plate_key,plate,details) VALUES('report-new','ops-brand','REPORTNEW','REPORT NEW',%s)",(db.json(current['vehicle']),))
        c.execute("UPDATE shifts SET vehicle_log=%s WHERE id='fuel-handover'",(db.json(current),))
    response=client.get('/api/fleet/driver-report?start=2026-10-12&end=2026-10-12')
    assert response.status_code==200,response.text
    data=response.json();assert data['total']==2
    assert sorted(r['fuel_amount_halalas'] for r in data['rows'])==[1000,2300]
    assert data['summary']['fuel_amount_halalas']==3300
    assert data['summary']['liters']==31
    assert len({(r['shift_id'],r['segment_no']) for r in data['rows']})==2


def test_general_manager_supervisor_reads_without_maintenance_and_revokes(ops):
    actor,*_=ops;client=actor('owner')
    with db.tx() as c:
        seed(c,'fuel-supervisor-visible','SUPERVISOR VISIBLE',12)
        seed(c,'fuel-supervisor-hidden','SUPERVISOR HIDDEN',12,bids=['ops-other'])
    actor('manager')
    url='/api/fleet/driver-report?start=2026-10-12&end=2026-10-12'
    assert client.get(url).status_code==403
    assert client.get('/api/fleet/driver-report/options').status_code==403
    with db.tx() as c:
        c.execute("UPDATE users SET extra_permissions='[\"reports_supervise\"]' WHERE id='ops-manager'")
    actor('manager')  # Fixture snapshots the dependency; real auth reads each request.
    response=client.get(url);assert response.status_code==200,response.text
    assert [r['shift_id'] for r in response.json()['rows']]==['fuel-supervisor-visible']
    assert client.get('/api/fleet/driver-report/options').status_code==200
    # A report-only supervisor never gains maintenance mutation/listing access.
    assert client.get('/api/fleet/vehicles').status_code==403
    with db.tx() as c:
        c.execute("UPDATE users SET permission_denials='[\"reports_supervise\"]' WHERE id='ops-manager'")
    assert client.get(url).status_code==403
    with db.tx() as c:
        c.execute("UPDATE users SET permission_denials='[\"reports_view\"]' WHERE id='ops-manager'")
    assert client.get(url).status_code==403


def test_supervisor_grant_retracted_during_fuel_read_fails_closed(ops,monkeypatch):
    from app import driver_fuel_report
    actor,*_=ops;client=actor('owner')
    with db.tx() as c:
        seed(c,'fuel-supervisor-revoke','SUPERVISOR REVOKE',12)
        c.execute("UPDATE users SET extra_permissions='[\"reports_supervise\"]' WHERE id='ops-manager'")
    actor('manager')
    original=driver_fuel_report.fuel_values
    def revoke(log):
        with db.tx() as c:c.execute("UPDATE users SET extra_permissions='[]' WHERE id='ops-manager'")
        return original(log)
    monkeypatch.setattr(driver_fuel_report,'fuel_values',revoke)
    response=client.get('/api/fleet/driver-report?start=2026-10-12&end=2026-10-12')
    assert response.status_code==403,response.text
