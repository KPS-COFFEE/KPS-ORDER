"""Daily vehicle history acceptance against isolated PostgreSQL only."""
from datetime import datetime, timezone
from uuid import uuid4
import io,zipfile,json
import pytest
from app import db,auth
from app.shift_vehicle import CHECKS
from test_operations import ops

pytestmark = pytest.mark.postgres


def seed(c,sid,plate,day,driver='ops-driver',km=210,liters=21,full=True,state='closed',brand='ops-brand',bids=None,exception=False,inspection_issue=False,profile=None):
    opened=datetime(2026,10,day,16,tzinfo=timezone.utc);ended=datetime(2026,10,day,20,tzinfo=timezone.utc)
    log={'vehicle':{'plate':plate,'type':'Test car','description':'Test model',**(profile or {})},
         'start':{'odometer_km':1000,'fuel_percent':100 if full else 50,'checks':{k:'ok' for k in CHECKS},'recorded_at':opened.isoformat()},
         'refuels':[{'liters':str(liters),'odometer_km':1000+km,'recorded_at':ended.isoformat()}]}
    if inspection_issue:
        log['start']['checks']['brakes']='issue';log['start']['notes']='TEST ONLY inspection fault'
    if state=='closed':log['end']={'odometer_km':1000+km,'fuel_percent':100,'checks':{k:'ok' for k in CHECKS},'recorded_at':ended.isoformat()}
    if exception:log['end_exception']={'reason':'Test-only exception','recorded_at':ended.isoformat()}
    return c.execute('INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,opened_at,submitted_at,closed_at,status,vehicle_log,delivery_operational_date) VALUES(%s,\'driver\',%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                     (sid,driver,brand,db.json(bids or ['ops-branch']),opened,ended if state=='closed' else None,ended if state=='closed' else None,state,db.json(log),opened.date())).fetchone()


def delegate(actor):
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions) VALUES('ops-fawzi','TEST ONLY Fawzi','admin','ops-brand','[\"ops-branch\"]','[\"vehicle_maintenance_manage\"]')")
    return actor('fawzi')


def test_vehicle_profile_assignment_rate_snapshot_and_correction(ops):
    from app.shift_vehicle import start,Inspection
    actor,*_=ops;client=actor('owner')
    profile=dict(plate='TEST PROFILE',type='Test compact',model_year=2020,color='White',engine_cc=1193,
                 transmission='automatic',baseline_kml=15,baseline_note='TEST ONLY provisional city use')
    with db.tx() as c:
        seed(c,'profile-legacy','TEST PROFILE',10)
        original=c.execute("SELECT metrics FROM vehicle_operating_logs WHERE shift_id='profile-legacy'").fetchone()['metrics']
    result=client.patch('/api/identity/staff/ops-driver',json={'vehicle':profile,'reason':'TEST ONLY vehicle specification import'})
    assert result.status_code==200,result.text
    assert result.json()['user']['vehicle']['transmission']=='automatic'
    with db.tx() as c:
        target=c.execute("SELECT * FROM users WHERE id='ops-driver'").fetchone()
        snapshot=start(c,target,target,Inspection(odometer_km=1210,fuel_percent=100,checks={k:'ok' for k in CHECKS}))
        assert snapshot['vehicle']['baseline_kml']==15
        seed(c,'profile-new','TEST PROFILE',11,km=260,liters=20,profile=profile)
        assert c.execute("SELECT metrics FROM vehicle_operating_logs WHERE shift_id='profile-legacy'").fetchone()['metrics']==original
        assert c.execute("SELECT metrics->>'fuel_status' status FROM vehicle_operating_logs WHERE shift_id='profile-new'").fetchone()['status']=='orange'
    changed=client.patch('/api/identity/staff/ops-driver',json={'vehicle':{**profile,'baseline_kml':20},'reason':'TEST ONLY revised baseline for future shifts'})
    assert changed.status_code==200,changed.text
    delegate(actor)
    cards=client.get('/api/fleet/vehicles').json()['vehicles']
    car=next(v for v in cards if v['plate']=='TEST PROFILE')
    assert car['details']['baseline_kml']==20 and car['details']['color']=='White'
    assert len(car['assigned_drivers'])==1
    url=f"/api/fleet/vehicles/{car['id']}/logs/profile-new"
    detail=client.get(url).json()['log']
    result=client.post(url+'/corrections',json=dict(revision=detail['revision'],reason='TEST ONLY corrected end reading',
        start_odometer=1000,end_odometer=1270,liters='20',full_start=True,full_end=True),headers={'Idempotency-Key':'profile-correct-once'})
    assert result.status_code==200,result.text
    assert result.json()['metrics']['baseline_kml']==15 and result.json()['metrics']['fuel_status']=='green'
    actor('driver')
    assert client.put('/api/identity/driver/vehicle',json=profile).status_code==403


def test_ten_cars_twelve_days_history_summary_filters_exports(ops):
    actor,*_=ops
    client=actor('owner')
    with db.tx() as c:
        for day in range(1,13):
            for car in range(1,11):
                seed(c,f'fleet-{day}-{car}',f'TEST {car}',day,driver='ops-driver2' if day==12 else 'ops-driver',km=210,liters=21)
        # Different categories, a second shift of the same car/day, and grey incomplete.
        seed(c,'orange','TEST 11',12,km=185,liters=21)
        seed(c,'red','TEST 12',12,km=240,liters=31,exception=True)
        seed(c,'grey','TEST 13',12,km=150,liters=20,full=False)
        seed(c,'second-shift','TEST 1',12,km=225,liters=22)
        seed(c,'open','TEST 14',12,state='open')
    delegate(actor)
    report=client.get('/api/fleet/report?start=2026-10-01&end=2026-10-12&limit=200').json()
    assert report['total']==125 and len(report['rows'])==125
    s=report['summary'];assert s['vehicles']==14 and s['green']==121 and s['orange']==1 and s['red']==1 and s['grey']==2
    assert report['rows'][0]['operating_day']=='2026-10-12' and report['rows'][-1]['operating_day']=='2026-10-01'
    assert s['efficiency']==round((120*210+185+240+225)/(120*21+21+31+22),3)
    vid=next(v for v in client.get('/api/fleet/vehicles').json()['vehicles'] if v['plate']=='TEST 1')['id']
    history=client.get('/api/fleet/vehicles/'+vid).json()
    assert history['total']==13 and history['rows'][0]['driver_id'] in ('ops-driver','ops-driver2')
    assert {r['driver_id'] for r in history['rows']}=={'ops-driver','ops-driver2'}
    assert all(r['gps_distance'] is None and r['expected_distance'] is None for r in report['rows'])
    for color,count in [('green',121),('orange',1),('red',1),('grey',2)]:
        r=client.get(f'/api/fleet/report?start=2026-10-01&end=2026-10-12&fuel_status={color}').json();assert r['total']==count
    assert client.get('/api/fleet/report?start=2026-10-12&end=2026-10-12&driver_id=ops-driver2').json()['total']==10
    assert client.get('/api/fleet/report?start=2026-10-01&end=2026-10-12&exceptions_only=true').json()['total']==1
    for fmt in ('xlsx','pdf'):
        r=client.get(f'/api/fleet/export?format={fmt}&start=2026-10-01&end=2026-10-12&fuel_status=red&lang=en');assert r.status_code==200,r.text
        if fmt=='xlsx':
            z=zipfile.ZipFile(io.BytesIO(r.content));sheet=z.read('xl/worksheets/sheet1.xml').decode()
            assert 'TEST 12' in sheet and 'TEST 11' not in sheet and sheet.count('<row ')==2
        else:assert r.content.startswith(b'%PDF')
    assert client.get('/api/fleet/report?start=2026-10-12&end=2026-10-01').status_code==422


def test_audited_correction_replay_immutable_original_and_scope(ops):
    actor,*_=ops
    client=actor('owner')
    with db.tx() as c:
        seed(c,'closed-correction','TEST AUDIT',12,km=210,liters=25)
        before=c.execute('SELECT vehicle_log FROM shifts WHERE id=\'closed-correction\'').fetchone()['vehicle_log']
    delegate(actor)
    vid=client.get('/api/fleet/vehicles').json()['vehicles'][0]['id'];url=f'/api/fleet/vehicles/{vid}/logs/closed-correction'
    d=client.get(url).json();body={'revision':d['log']['revision'],'reason':'TEST ONLY receipt correction','start_odometer':1000,'end_odometer':1210,'liters':'21','full_start':True,'full_end':True,'notes':'=HYPERLINK("test")'}
    result=client.post(url+'/corrections',json=body,headers={'Idempotency-Key':'fleet-correction-once'});assert result.status_code==200,result.text
    assert result.json()['metrics']['fuel_status']=='green'
    assert client.post(url+'/corrections',json=body,headers={'Idempotency-Key':'fleet-correction-once'}).json()==result.json()
    assert client.post(url+'/corrections',json=body,headers={'Idempotency-Key':'fleet-correction-stale'}).status_code==409
    assert len(client.get(url).json()['corrections'])==1
    with db.tx() as c:
        assert c.execute('SELECT vehicle_log FROM shifts WHERE id=\'closed-correction\'').fetchone()['vehicle_log']==before
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='vehicle_log.correct'").fetchone()['n']==1
    actor('other');assert client.get(url).status_code==404
    actor('driver');assert client.get('/api/fleet/vehicles').status_code==403
    actor('manager');assert client.get('/api/fleet/report').status_code==403
    actor('fawzi')
    with db.tx() as c:c.execute("UPDATE users SET permissions='[]' WHERE id='ops-fawzi'")
    actor('fawzi');assert client.post(url+'/corrections',json=body,headers={'Idempotency-Key':'fleet-correction-once'}).status_code==403


def test_faults_and_branch_scope_no_duplicate_counts(ops):
    actor,*_=ops
    client=actor('owner')
    with db.tx() as c:
        seed(c,'fault-shift','TEST FAULT',12)
        seed(c,'inspection-shift','TEST INSPECTION',12,inspection_issue=True)
        c.execute("INSERT INTO vehicle_reports(id,brand_id,branch_id,driver_id,category,vehicle_description,vehicle_plate,description,created_at) VALUES('fault1','ops-brand','ops-branch','ops-driver','engine','test','TEST FAULT','test-only','2026-10-12 18:00:00Z')")
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city) VALUES('ops-second',2,'second','ops-brand','test second','test')")
        seed(c,'out-of-scope','TEST HIDDEN',12,bids=['ops-branch','ops-second'])
    delegate(actor)
    r=client.get('/api/fleet/report?start=2026-10-12&faults_only=true').json();assert r['total']==2 and r['summary']['faults']==2
    assert client.get('/api/fleet/report?start=2026-10-12').json()['total']==2
    assert client.get('/api/fleet/report?start=2026-10-12&branch_id=ops-second').json()['total']==0
    actor('owner');assert client.get('/api/fleet/report?start=2026-10-12').json()['total']==3


def test_vehicle_changed_driver_rejects_overlap_and_lower_odometer(ops):
    actor,post,order,shifts=ops
    client=actor('owner')
    with db.tx() as c:
        c.execute("UPDATE users SET vehicle='{\"plate\":\"TEST SAME\"}' WHERE id IN ('ops-driver','ops-driver2')")
    manager,driver=shifts()
    post('driver2','/api/shifts',{'kind':'driver','branch_ids':['ops-branch'],'manager_shift_id':manager,'vehicle_inspection':{'odometer_km':1000,'fuel_percent':100,'checks':{k:'ok' for k in CHECKS}}},status=409)
    with db.tx() as c:
        seed(c,'past-higher','TEST SAME',11,km=500)
        c.execute("UPDATE shifts SET status='submitted',submitted_at=now() WHERE id=%s",(driver,))
    post('driver2','/api/shifts',{'kind':'driver','branch_ids':['ops-branch'],'manager_shift_id':manager,'vehicle_inspection':{'odometer_km':1200,'fuel_percent':100,'checks':{k:'ok' for k in CHECKS}}},status=422)
    d=post('driver2','/api/shifts',{'kind':'driver','branch_ids':['ops-branch'],'manager_shift_id':manager,'vehicle_inspection':{'odometer_km':1500,'fuel_percent':100,'checks':{k:'ok' for k in CHECKS}}})
    actor('owner');assert len(client.get('/api/fleet/vehicles').json()['vehicles'])==1


def test_nonempty_vehicle_history_and_corrections_survive_isolated_restore(ops):
    import os
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from app.exports import build_archive,restore_archive
    actor,*_=ops
    with db.tx() as c:seed(c,'fleet-restore-closed','TEST RESTORE',12,km=210,liters=25)
    client=delegate(actor)
    vid=client.get('/api/fleet/vehicles').json()['vehicles'][0]['id']
    url=f'/api/fleet/vehicles/{vid}/logs/fleet-restore-closed'
    revision=client.get(url).json()['log']['revision']
    body={'revision':revision,'reason':'TEST ONLY restored correction','start_odometer':1000,'end_odometer':1210,'liters':'21','full_start':True,'full_end':True,'notes':'TEST ONLY preserve history'}
    response=client.post(url+'/corrections',json=body,headers={'Idempotency-Key':'fleet-restore-correction'})
    assert response.status_code==200,response.text
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        contents,manifest=build_archive(c,full=True)
        source={name:c.execute('SELECT * FROM '+name).fetchall() for name in ('fleet_vehicles','vehicle_operating_logs','vehicle_log_corrections')}
    target='kps_restore_'+uuid4().hex;test_url=os.environ['TEST_DATABASE_URL']
    try:
        result=restore_archive(contents,target_url=test_url,target_schema=target)
        assert result['verified'] and result['counts']==manifest['counts']
        with psycopg.connect(test_url,row_factory=dict_row) as restored:
            restored.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
            for name,rows in source.items():assert restored.execute('SELECT * FROM '+name).fetchall()==rows
            # Re-enabled materialization creates only the next canonical shift
            # row; it never recreates existing history or vehicle identity.
            seed(restored,'fleet-restore-next','TEST RESTORE',12,state='open')
            assert restored.execute('SELECT count(*) n FROM fleet_vehicles').fetchone()['n']==1
            assert restored.execute('SELECT count(*) n FROM vehicle_operating_logs').fetchone()['n']==2
            assert restored.execute('SELECT count(*) n FROM vehicle_log_corrections').fetchone()['n']==1
            with pytest.raises(psycopg.errors.RaiseException):
                with restored.transaction():restored.execute("UPDATE vehicle_operating_logs SET metrics='{}' WHERE shift_id='fleet-restore-closed'")
            with pytest.raises(psycopg.errors.RaiseException):
                with restored.transaction():restored.execute("DELETE FROM vehicle_log_corrections WHERE shift_id='fleet-restore-closed'")
            restored.rollback()
    finally:
        with psycopg.connect(test_url,autocommit=True) as cleanup:cleanup.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(target)))
    with db.tx() as c:
        for name,rows in source.items():assert c.execute('SELECT * FROM '+name).fetchall()==rows
