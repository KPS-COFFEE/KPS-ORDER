"""Real PostgreSQL witnesses for the single physical workday contract."""
import pytest
from app import db
from app.shift_vehicle import CHECKS
from test_enbishan_dual_brand import dual


@pytest.fixture
def central(dual, monkeypatch):
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED', 'true')
    return dual


def inspection(km=1000):
    return {'odometer_km':km,'fuel_percent':100,'checks':{k:'ok' for k in CHECKS}}


def open_days(central):
    _, _, post, _, _ = central
    manager=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    driver=post('driver','/api/central-operations/workdays',{
        'kind':'driver','manager_shift_id':manager['shift']['id'],'vehicle_inspection':inspection()})
    return manager,driver


@pytest.mark.postgres
def test_one_open_action_covers_both_brands_with_one_vehicle_receipt(central):
    manager,driver=open_days(central)
    with db.tx() as c:
        rows=c.execute("SELECT * FROM shifts WHERE user_id='dual-driver' ORDER BY brand_id").fetchall()
        assert len(rows)==2
        assert {s['brand_id'] for s in rows}=={'kps','enbishan'}
        assert sum('start' in s['vehicle_log'] for s in rows)==1
        assert sum(s['opening_halalas'] for s in rows)==0
        physical=next(s for s in rows if 'start' in s['vehicle_log'])
        ledger=next(s for s in rows if 'start' not in s['vehicle_log'])
        assert ledger['vehicle_log']=={'central_vehicle_shift_id':physical['id']}
        assert len(c.execute('SELECT * FROM central_workdays').fetchall())==2
    assert driver['shift']['id']==physical['id']
    assert len({s['central_workday_id'] for s in driver['shifts']})==1


@pytest.mark.postgres
def test_manual_opening_float_is_recorded_once(central):
    _,_,post,_,_=central
    day=post('owner','/api/central-operations/workdays',{'kind':'manager','branch_ids':['kps-branch-1','enbishan-test-1'],'opening_halalas':5000,'reason':'Synthetic opening cash'})
    assert sum(s['opening_halalas'] for s in day['shifts'])==5000


@pytest.mark.postgres
def test_a_failure_in_second_brand_rolls_back_first_opening(central):
    _,_,post,_,_=central
    with db.tx() as c:
        c.execute("UPDATE branches SET settings=jsonb_set(settings,'{delivery_enabled}','false') WHERE brand_id='enbishan'")
    post('manager','/api/central-operations/workdays',{'kind':'manager'},status=409)
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM shifts WHERE user_id='dual-manager'").fetchone()
        assert not c.execute('SELECT 1 FROM central_workdays').fetchone()


@pytest.mark.postgres
def test_one_manager_workday_can_assign_both_brands_to_same_driver(central):
    _,_,post,_,order=central
    _,driver=open_days(central)
    legs={s['brand_id']:s['id'] for s in driver['shifts']}
    for brand in ['kps','enbishan']:
        oid=order(brand)
        task=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':legs[brand]},brand)
        assert task['task']['driver_id']=='dual-driver'


@pytest.mark.postgres
def test_group_replay_does_not_create_another_vehicle_or_float(central):
    _,_,post,_,_=central
    first=post('manager','/api/central-operations/workdays',{'kind':'manager'},key='one-workday')
    replay=post('manager','/api/central-operations/workdays',{'kind':'manager'},key='one-workday')
    assert first==replay
    post('manager','/api/central-operations/workdays',{'kind':'manager'},status=409)


@pytest.mark.postgres
def test_revocation_blocks_group_detail_and_replay(central):
    _,actor,post,_,_=central
    day=post('manager','/api/central-operations/workdays',{'kind':'manager'},key='revoke-workday')
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-manager' AND brand_id='enbishan'")
    assert actor('manager').get('/api/central-operations/workdays/'+day['shift']['id']).status_code==404
    post('manager','/api/central-operations/workdays',{'kind':'manager'},key='revoke-workday',status=404)


@pytest.mark.postgres
def test_component_cannot_close_independently(central):
    _,_,post,_,_=central
    manager,_=open_days(central)
    for leg in manager['shifts']:
        post('manager','/api/shifts/'+leg['id']+'/closing',{},brand=leg['brand_id'],status=409)
    with db.tx() as c:
        assert {s['status'] for s in c.execute("SELECT status FROM shifts WHERE user_id='dual-manager'").fetchall()}=={'open'}


@pytest.mark.postgres
def test_closing_rolls_back_all_brands_when_one_has_a_pending_order(central):
    _,_,post,_,order=central
    manager,_=open_days(central)
    oid=order('enbishan')
    enb=next(s for s in manager['shifts'] if s['brand_id']=='enbishan')
    with db.tx() as c:
        db.event(c,oid,'dual-manager','preparation.accepted',{'manager_shift_id':enb['id'],'dispatch_pending':True})
    post('manager','/api/central-operations/workdays/'+manager['shift']['id']+'/closing',{},status=409)
    with db.tx() as c:
        assert {s['status'] for s in c.execute("SELECT status FROM shifts WHERE user_id='dual-manager'").fetchall()}=={'open'}


@pytest.mark.postgres
def test_driver_close_requires_exact_breakdown_and_is_atomic(central,monkeypatch):
    _,_,post,_,_=central
    from app import reporting
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    _,driver=open_days(central)
    path='/api/central-operations/workdays/'+driver['shift']['id']+'/driver-close'
    rows=[{'shift_id':s['id'],'counted_halalas':0,'received_halalas':0,'terminal_total_halalas':0} for s in driver['shifts']]
    body={'legs':rows,'confirmed':True,'exception_reason':'Synthetic delegated end','vehicle_inspection':inspection(1010)}
    post('manager',path,{**body,'legs':rows[:1]},status=422)
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM shifts WHERE user_id='dual-driver' AND status<>'open'").fetchone()
    done=post('manager',path,body)
    assert {s['status'] for s in done['shifts']}=={'closed'}
    assert len(done['report_ids'])==2
    with db.tx() as c:
        rows=c.execute("SELECT * FROM shifts WHERE user_id='dual-driver'").fetchall()
        assert sum('end' in s['vehicle_log'] for s in rows)==1
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='shift.vehicle.end'").fetchone()['n']==1
        assert len(c.execute("SELECT * FROM custody_transfers WHERE from_shift_id=ANY(%s) AND status='confirmed'",([s['id'] for s in rows],)).fetchall())==2


@pytest.mark.postgres
def test_disabled_rollout_never_opens_a_workday(dual):
    _,_,post,_,_=dual
    post('manager','/api/central-operations/workdays',{'kind':'manager'},status=404)


@pytest.mark.postgres
def test_one_early_close_request_and_decision_updates_the_whole_workday(central):
    _,_,post,_,_=central
    _,driver=open_days(central)
    root=driver['shift']['id'];path='/api/central-operations/workdays/'+root
    requested=post('driver',path+'/request',{'reason':'Synthetic shared workday complete'})
    authorization=requested['shift']['closure_authorization']
    post('manager',path+'/decision',{'request_id':authorization['id'],'decision':'approved','reason':'Synthetic manager approval'})
    finished=post('driver',path+'/closing',{})
    assert {s['status'] for s in finished['shifts']}=={'closing'}
    with db.tx() as c:
        assert {s['closure_authorization']['status'] for s in c.execute("SELECT * FROM shifts WHERE user_id='dual-driver'").fetchall()}=={'approved'}


@pytest.mark.postgres
def test_owner_branch_editor_preserves_role_and_combines_explicit_grants(central):
    _,actor,post,_,_=central
    path='/api/central-operations/staff/dual-driver'
    updated=post('owner',path,{'branch_ids':['kps-branch-1','enbishan-test-2'],'reason':'Synthetic branch assignment'},method='patch')
    assert updated['user']['role']=='driver'
    assert set(updated['user']['branch_ids'])=={'kps-branch-1','enbishan-test-2'}
    session=actor('driver').get('/api/session').json()['user']
    assert {s['id']:s['branch_ids'] for s in session['central_operations']['brands']}=={'kps':['kps-branch-1'],'enbishan':['enbishan-test-2']}
    post('driver',path,{'branch_ids':['kps-branch-1'],'reason':'Unauthorized self grant'},method='patch',status=403)
    post('owner',path,{'branch_ids':['unknown-branch'],'reason':'Unknown branch'},method='patch',status=403)
    with db.tx() as c:
        assert c.execute("SELECT branch_ids FROM staff_brand_memberships WHERE user_id='dual-driver' AND brand_id='enbishan'").fetchone()['branch_ids']==['enbishan-test-2']


@pytest.mark.postgres
def test_branch_editor_rejects_stale_and_running_shift_revocations(central):
    _,_,post,_,_=central
    path='/api/central-operations/staff/dual-driver'
    post('owner',path,{'branch_ids':['kps-branch-1'],'expected_branch_ids':[],'reason':'Synthetic stale edit'},method='patch',status=409)
    open_days(central)
    post('owner',path,{'branch_ids':['kps-branch-1'],'reason':'Synthetic unsafe live revocation'},method='patch',status=409)


@pytest.mark.postgres
def test_disabled_noncentral_routes_still_accept_old_brand_shifts(dual):
    _,_,_,shifts,_=dual
    manager,driver=shifts('enbishan')
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM central_workdays').fetchone()
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(driver,)).fetchone()['status']=='open'


@pytest.mark.postgres
def test_existing_shift_adoption_preserves_cash_vehicle_and_task_identity(central):
    _,_,post,shifts,order=central
    manager,driver=shifts('kps')
    oid=order('kps')
    task=post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':driver})['task']
    with db.tx() as c:
        original=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
    adopted_manager=post('manager','/api/central-operations/workdays/'+manager+'/adopt',{})
    adopted_driver=post('driver','/api/central-operations/workdays/'+driver+'/adopt',{})
    assert adopted_manager['shift']['id']==manager
    assert adopted_driver['shift']['id']==driver
    with db.tx() as c:
        physical=c.execute('SELECT * FROM shifts WHERE id=%s',(driver,)).fetchone()
        assert physical['vehicle_log']==original['vehicle_log']
        assert physical['opening_halalas']==original['opening_halalas']
        assert physical['opened_at']==original['opened_at']
        assert c.execute('SELECT shift_id FROM driver_tasks WHERE id=%s',(task['id'],)).fetchone()['shift_id']==driver
        assert len(c.execute("SELECT * FROM shifts WHERE user_id='dual-driver'").fetchall())==2
        assert sum('start' in s['vehicle_log'] for s in c.execute("SELECT vehicle_log FROM shifts WHERE user_id='dual-driver'").fetchall())==1


def overnight_legacy_shifts(central, monkeypatch, *, hour=3, minute=24):
    """A genuine prior-day manager/driver pair, before the morning admission."""
    from datetime import datetime
    from app import service_hours
    _, _, post, _, _ = central
    hours = [{'days': list(range(7)), 'start': '16:00', 'end': '04:00'}]
    with db.tx() as c:
        c.execute("UPDATE branches SET settings=jsonb_set(settings,'{delivery_hours}',%s::jsonb)", (db.json(hours),))
        # The side-brand's intake has ended, but the physical day is still open.
        side_hours = [{'days': list(range(7)), 'start': '16:00', 'end': '02:00'}]
        c.execute("UPDATE branches SET settings=jsonb_set(settings,'{delivery_hours}',%s::jsonb) WHERE brand_id='enbishan'", (db.json(side_hours),))
    manager_at = datetime(2026, 10, 23, 15, 54, tzinfo=service_hours.RIYADH)
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: manager_at)
    manager = post('manager', '/api/shifts', {'kind': 'manager'})['shift']['id']
    driver_at = datetime(2026, 10, 23, 17, 0, tzinfo=service_hours.RIYADH)
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: driver_at)
    driver = post('driver', '/api/shifts', {'kind': 'driver', 'manager_shift_id': manager,
        'vehicle_inspection': inspection()})['shift']['id']
    with db.tx() as c:
        c.execute('UPDATE shifts SET opened_at=%s WHERE id=%s', (manager_at, manager))
        c.execute('UPDATE shifts SET opened_at=%s WHERE id=%s', (driver_at, driver))
    after_midnight = datetime(2026, 10, 24, hour, minute, tzinfo=service_hours.RIYADH)
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: after_midnight)
    return manager, driver


@pytest.mark.postgres
@pytest.mark.parametrize('hour,minute', [(3, 24), (4, 1)])
def test_overnight_adoption_preserves_existing_day_and_fresh_manager_gate(central, monkeypatch, hour, minute):
    _, _, post, _, _ = central
    manager, driver = overnight_legacy_shifts(central, monkeypatch, hour=hour, minute=minute)
    with db.tx() as c:
        original = {r['id']: r for r in c.execute('SELECT * FROM shifts WHERE id=ANY(%s)', ([manager, driver],)).fetchall()}
    for name, sid in [('manager', manager), ('driver', driver)]:
        adopted = post(name, '/api/central-operations/workdays/'+sid+'/adopt', {})
        assert len(adopted['shifts']) == 2
        assert {r['delivery_operational_date'] for r in adopted['shifts']} == {'2026-10-23'}
        for leg in adopted['shifts']:
            assert leg['delivery_window_snapshot']['operational_date'] == '2026-10-23'
            for window in leg['delivery_window_snapshot']['branches'].values():
                assert window['window_start'] == '2026-10-23T16:00:00+03:00'
                if name == 'manager':
                    assert window['preparation_start'] == '2026-10-23T15:45:00+03:00'
        with db.tx() as c:
            preserved = c.execute('SELECT * FROM shifts WHERE id=%s', (sid,)).fetchone()
            for field in ['opened_at', 'delivery_operational_date', 'delivery_window_snapshot', 'vehicle_log', 'opening_halalas']:
                assert preserved[field] == original[sid][field]
    # An owner without an existing workday cannot reuse adoption's past clock.
    for untrusted in ({}, {'admission_at': '2026-10-23T16:00:00+03:00', 'adoption_anchor': manager}):
        denied = post('owner', '/api/central-operations/workdays',
            {'kind': 'manager', 'branch_ids': ['kps-branch-1', 'enbishan-test-1'], **untrusted}, status=409)
        assert denied['detail']['code'] == 'MANAGER_SHIFT_HOURS'
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM shifts WHERE user_id='dual-owner'").fetchone()


@pytest.mark.postgres
def test_overnight_adoption_still_revalidates_current_branch_enablement(central, monkeypatch):
    _, _, post, _, _ = central
    manager, _ = overnight_legacy_shifts(central, monkeypatch)
    with db.tx() as c:
        c.execute("UPDATE branches SET settings=jsonb_set(settings,'{delivery_enabled}','false') WHERE brand_id='enbishan'")
    post('manager', '/api/central-operations/workdays/'+manager+'/adopt', {}, status=409)
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM central_workdays').fetchone()
        assert not c.execute("SELECT 1 FROM shifts WHERE brand_id='enbishan'").fetchone()


@pytest.mark.postgres
def test_two_legacy_shifts_are_never_silently_merged(central):
    _,_,post,shifts,_=central
    kps,_=shifts('kps');shifts('enbishan')
    post('manager','/api/central-operations/workdays/'+kps+'/adopt',{},status=409)
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM central_workdays').fetchone()


@pytest.mark.postgres
def test_foreign_brand_pending_task_blocks_a_physical_break(central):
    _,_,post,_,order=central
    from app.operations import unresolved
    _,driver=open_days(central)
    enb=next(s for s in driver['shifts'] if s['brand_id']=='enbishan')
    post('manager','/api/orders/'+order('enbishan')+'/assign',{'driver_shift_id':enb['id']},brand='enbishan')
    with db.tx() as c:
        assert unresolved(c,driver['shift']['id'])==1


@pytest.mark.postgres
def test_shared_location_uses_one_gps_point_and_revocable_both_brand_scopes(central):
    _,actor,post,_,_=central
    _,driver=open_days(central)
    with db.tx() as c:
        c.execute("INSERT INTO driver_latest_locations(driver_id,brand_id,shift_id,lat,lng,accuracy_m) VALUES('dual-driver','kps',%s,26.1,45.2,5)",(driver['shift']['id'],))
    response=actor('manager').get('/api/central-operations/driver-locations')
    assert response.status_code==200,response.text
    rows=response.json()['drivers'];assert len(rows)==1
    assert set(rows[0]['branch_ids'])=={'kps-branch-1','enbishan-test-1','enbishan-test-2'}
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids='[\"enbishan-test-2\"]' WHERE user_id='dual-manager' AND brand_id='enbishan'")
    rows=actor('manager').get('/api/central-operations/driver-locations').json()['drivers']
    assert set(rows[0]['branch_ids'])=={'kps-branch-1','enbishan-test-2'}
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-driver' AND brand_id='enbishan'")
    rows=actor('manager').get('/api/central-operations/driver-locations').json()['drivers']
    assert rows[0]['branch_ids']==['kps-branch-1']


@pytest.mark.postgres
def test_grouped_day_cannot_gain_an_independent_brand_shift(central):
    _,_,post,_,_=central
    day=post('manager','/api/central-operations/workdays',{'kind':'manager','branch_ids':['kps-branch-1']})
    assert len(day['shifts'])==1
    post('manager','/api/shifts',{'kind':'manager'},brand='enbishan',status=409)
    with db.tx() as c:
        assert len(c.execute("SELECT id FROM shifts WHERE user_id='dual-manager'").fetchall())==1


@pytest.mark.postgres
def test_side_ledger_cannot_fork_physical_fuel_or_availability(central):
    _,_,post,_,_=central
    _,driver=open_days(central)
    side=next(s for s in driver['shifts'] if s['id']!=driver['shift']['id'])
    post('driver','/api/shifts/'+side['id']+'/refuel',{'liters':'1.00','odometer_km':1001},brand=side['brand_id'],status=409)
    post('manager','/api/shifts/'+side['id']+'/availability',{'available':False},brand=side['brand_id'],status=409)
    with db.tx() as c:
        row=c.execute('SELECT * FROM shifts WHERE id=%s',(side['id'],)).fetchone()
        assert row['vehicle_log']=={'central_vehicle_shift_id':driver['shift']['id']}
        assert row['available']


@pytest.mark.postgres
def test_accountant_manager_close_uses_manager_capability_without_role_change(central):
    _,_,post,_,_=central
    day=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    with db.tx() as c:
        c.execute("UPDATE users SET extra_permissions=%s WHERE id='dual-accountant'",(db.json(['shifts_close']),))
    path='/api/central-operations/workdays/'+day['shift']['id']
    post('accountant',path+'/closing',{})
    body={'legs':[{'shift_id':s['id'],'counted_halalas':0,'handover_halalas':0} for s in day['shifts']],
          'exception_reason':'Synthetic delegated manager close'}
    submitted=post('accountant',path+'/submit',body)
    assert {s['status'] for s in submitted['shifts']}=={'submitted'}
    with db.tx() as c:
        assert c.execute("SELECT role FROM users WHERE id='dual-accountant'").fetchone()['role']=='admin'


@pytest.mark.postgres
def test_canonical_manager_handover_cannot_split_a_shared_workday(central):
    _,_,post,_,_=central
    day=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    post('manager','/api/shifts/'+day['shift']['id']+'/handover-manager',
         {'target_manager_shift_id':day['shift']['id'],'reason':'Synthetic bypass attempt'},status=409)
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM manager_handovers').fetchone()
        assert {s['status'] for s in c.execute("SELECT status FROM shifts WHERE user_id='dual-manager'").fetchall()}=={'open'}


@pytest.mark.postgres
@pytest.mark.parametrize('flag_enabled',[True,False])
def test_same_physical_plate_cannot_be_received_from_other_brand_ledger(central,monkeypatch,flag_enabled):
    _,_,post,_,_=central
    with db.tx() as c:
        c.execute("UPDATE users SET vehicle=%s WHERE id='dual-driver'",(db.json({'plate':'SYNTHETIC 123','type':'car'}),))
    manager,_=open_days(central)
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,vehicle) VALUES('opposite-home-driver','SYNTHETIC opposite home','driver','enbishan',%s,%s)",
                  (db.json(['enbishan-test-1','enbishan-test-2']),db.json({'plate':'SYNTHETIC123','type':'car'})))
        c.execute("INSERT INTO staff_brand_memberships(user_id,brand_id,branch_ids,granted_by) VALUES('opposite-home-driver','kps',%s,'dual-owner')",(db.json(['kps-branch-1']),))
    if flag_enabled:
        post('owner','/api/central-operations/workdays',{'kind':'driver','user_id':'opposite-home-driver',
             'manager_shift_id':manager['shift']['id'],'vehicle_inspection':inspection()},status=409)
    else:
        monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED','false')
        parent=next(s for s in manager['shifts'] if s['brand_id']=='enbishan')
        post('owner','/api/shifts',{'kind':'driver','user_id':'opposite-home-driver',
             'manager_shift_id':parent['id'],'vehicle_inspection':inspection()},brand='enbishan',status=409)
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM shifts WHERE user_id='opposite-home-driver'").fetchone()


@pytest.mark.postgres
def test_flag_off_drains_existing_group_without_component_bypass(central,monkeypatch):
    _,actor,post,_,_=central
    from app import reporting
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    _,driver=open_days(central)
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED','false')
    post('manager','/api/central-operations/workdays',{'kind':'manager'},status=404)
    post('driver','/api/central-operations/workdays/'+driver['shift']['id']+'/adopt',{},status=404)
    for leg in driver['shifts']:
        post('driver','/api/shifts/'+leg['id']+'/closing',{},brand=leg['brand_id'],status=409)
    detail=actor('driver').get('/api/central-operations/workdays/'+driver['shift']['id'])
    assert detail.status_code==200,detail.text
    done=post('manager','/api/central-operations/workdays/'+driver['shift']['id']+'/driver-close',{
        'legs':[{'shift_id':s['id'],'counted_halalas':0,'received_halalas':0,'terminal_total_halalas':0} for s in driver['shifts']],
        'confirmed':True,'exception_reason':'Synthetic flag rollback drain','vehicle_inspection':inspection(1010)})
    assert {s['status'] for s in done['shifts']}=={'closed'}


@pytest.mark.postgres
def test_foreign_owner_canonical_lists_and_history_keep_current_branch_scope(central):
    _,actor,post,_,order=central
    day=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    foreign=next(s for s in day['shifts'] if s['brand_id']=='enbishan')
    hidden=order('enbishan')
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids=%s WHERE user_id='dual-owner' AND brand_id='enbishan'",(db.json(['enbishan-test-2']),))
    client=actor('owner','enbishan')
    for path in ['/api/shifts','/api/shifts?owner_view=current']:
        response=client.get(path)
        assert response.status_code==200,response.text
        assert foreign['id'] not in {r['id'] for r in response.json()['shifts']}
        if 'total' in response.json():assert response.json()['total']==0
    assert client.get('/api/shifts/'+foreign['id']).status_code==403
    response=client.get('/api/operations/alerts')
    assert response.status_code==200,response.text
    assert hidden not in {r['id'] for r in response.json()['unassigned']}


@pytest.mark.postgres
def test_shared_vehicle_opening_respects_audited_meter_correction(central):
    _,_,post,_,_=central
    with db.tx() as c:
        c.execute("UPDATE users SET vehicle=%s WHERE id='dual-driver'",
                  (db.json({'plate':'SYNTHETIC CHECKPOINT','type':'car'}),))
    manager,driver=open_days(central)
    # Synthetic closed history predates an authorized corrected meter reference.
    # Original opening and ending readings stay in the immutable shift log.
    with db.tx() as c:
        root=driver['shift']['id']
        recorded_at=c.execute('SELECT now() n').fetchone()['n'].isoformat()
        c.execute("UPDATE shifts SET vehicle_log=jsonb_set(vehicle_log,'{end}',%s),status='closed' WHERE id=%s",
                  (db.json({'odometer_km':1100,'fuel_percent':100,'recorded_at':recorded_at}),root))
        c.execute("UPDATE shifts SET status='closed' WHERE user_id='dual-driver' AND id<>%s",(root,))
        c.execute("UPDATE fleet_vehicles SET details=details||%s WHERE plate_key='SYNTHETICCHECKPOINT'",
                  (db.json({'odometer_km':900,'odometer_checkpoint':{'recorded_at':recorded_at,'reason':'Synthetic audited meter correction'}}),))
    opened=post('driver','/api/central-operations/workdays',{
        'kind':'driver','manager_shift_id':manager['shift']['id'],'vehicle_inspection':inspection(950)})
    assert opened['shift']['vehicle_log']['start']['odometer_km']==950


@pytest.fixture
def standalone_morning(central, monkeypatch):
    from app import service_hours
    from test_morning_delivery import at
    monkeypatch.setattr(service_hours, 'now_riyadh', lambda: at('08:00:00'))
    with db.tx() as c:
        c.execute("UPDATE brands SET settings=settings||%s WHERE id='kps'", (db.json({'morning_delivery': {
            'enabled': True, 'version': 1, 'execution_branch_ids': ['kps-branch-1'],
            'manager_user_ids': ['dual-manager'], 'preparer_user_ids': []}}),))
        c.execute("UPDATE users SET branch_ids=%s WHERE id='dual-driver'",
                  (db.json(['kps-branch-1', 'kps-branch-2']),))
    _, _, post, _, _ = central
    manager = post('manager', '/api/shifts', {'kind': 'manager', 'delivery_period': 'morning'})['shift']
    return central, manager


@pytest.mark.postgres
def test_old_client_morning_open_uses_one_canonical_shift_and_replay(standalone_morning):
    central, manager = standalone_morning
    _, _, post, _, _ = central
    body = {'kind': 'driver', 'manager_shift_id': manager['id'], 'branch_ids': [],
            'vehicle_inspection': inspection()}
    first = post('driver', '/api/central-operations/workdays', body, key='morning-compat')
    assert first['shift']['delivery_window_snapshot']['period'] == 'morning'
    assert first['shift']['branch_ids'] == ['kps-branch-1']
    assert first['shift']['brand_id'] == 'kps'
    assert post('driver', '/api/central-operations/workdays', body, key='morning-compat') == first
    assert post('driver', '/api/shifts', body, key='morning-compat') == first
    with db.tx() as c:
        rows = c.execute("SELECT * FROM shifts WHERE user_id='dual-driver'").fetchall()
        assert len(rows) == 1
        assert rows[0]['vehicle_log']['start']['odometer_km'] == 1000
        assert not c.execute('SELECT 1 FROM central_workdays').fetchone()
        c.execute("UPDATE users SET branch_ids='[]' WHERE id='dual-driver'")
    post('driver', '/api/central-operations/workdays', body, key='morning-compat', status=403)


@pytest.mark.postgres
@pytest.mark.parametrize('change,status', [
    ('missing_inspection', 422), ('self_scope', 403), ('mismatched_period', 409),
    ('closed_manager', 409), ('revoked_manager', 409), ('foreign_actor', 404),
])
def test_old_client_morning_open_preserves_canonical_guards(standalone_morning, change, status):
    central, manager = standalone_morning
    _, _, post, _, _ = central
    body = {'kind': 'driver', 'manager_shift_id': manager['id'], 'vehicle_inspection': inspection()}
    brand = 'kps'
    if change == 'missing_inspection':
        body.pop('vehicle_inspection')
    elif change == 'self_scope':
        body['branch_ids'] = ['kps-branch-1']
    elif change == 'mismatched_period':
        body['delivery_period'] = 'evening'
    else:
        with db.tx() as c:
            if change == 'closed_manager':
                c.execute("UPDATE users SET branch_ids='[\"kps-branch-1\"]' WHERE id='dual-driver'")
                c.execute("UPDATE shifts SET status='closed' WHERE id=%s", (manager['id'],))
            elif change == 'revoked_manager':
                c.execute("UPDATE users SET active=false WHERE id='dual-manager'")
            elif change == 'foreign_actor':
                c.execute("UPDATE users SET brand_id='enbishan',branch_ids='[\"enbishan-test-1\"]' WHERE id='dual-driver'")
                brand = 'enbishan'
    post('driver', '/api/central-operations/workdays', body, brand=brand, status=status)
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM shifts WHERE user_id='dual-driver'").fetchone()
        assert not c.execute('SELECT 1 FROM central_workdays').fetchone()


@pytest.mark.postgres
def test_old_client_evening_standalone_is_not_treated_as_morning(central):
    _, _, post, _, _ = central
    manager = post('manager', '/api/shifts', {'kind': 'manager'})['shift']
    result = post('driver', '/api/central-operations/workdays', {
        'kind': 'driver', 'manager_shift_id': manager['id'], 'vehicle_inspection': inspection()}, status=409)
    assert result['detail']['code'] == 'MANAGER_SHIFT_SCOPE'


def test_morning_compat_delegates_canonical_contract_without_group_action(monkeypatch):
    """The old central endpoint fails here at the real missing-association guard."""
    from contextlib import contextmanager
    from types import SimpleNamespace
    from app import central_workdays as workdays, central_operations, multibrand, operations
    user = {'id': 'driver', 'role': 'driver', 'active': True, 'brand_id': 'kps', 'branch_ids': ['branch']}
    manager = {'id': 'morning', 'kind': 'manager', 'status': 'open', 'brand_id': 'kps',
               'delivery_window_snapshot': {'period': 'morning'}}
    class Connection:
        def execute(self, sql, values=()):
            row = user if 'FROM users' in sql else None
            return SimpleNamespace(fetchone=lambda: row)
    c = Connection()
    @contextmanager
    def tx():
        yield c
    monkeypatch.setattr(db, 'tx', tx)
    monkeypatch.setattr(workdays, 'require_enabled', lambda *args: None)
    monkeypatch.setattr(workdays, 'lock_lanes', lambda *args: None)
    monkeypatch.setattr(workdays, 'association', lambda *args: None)
    monkeypatch.setattr(operations, 'lock_shift', lambda *args: manager)
    monkeypatch.setattr(multibrand, 'project', lambda conn, actor, brand: user)
    monkeypatch.setattr(central_operations, 'context', lambda *args: {'brands': [{'id': 'kps'}]})
    monkeypatch.setattr(db, 'idempotent', lambda conn, actor, key, op, body, perform: perform())
    calls = []
    def canonical(conn, principal, body):
        assert not workdays._action.get(), 'Canonical grouped-shift protections must remain active'
        assert multibrand.operation_brand.get() == 'kps'
        calls.append((principal, body))
        return {'shift': {'id': 'driver-morning', 'brand_id': 'kps'}}
    def mutate(conn, principal, request, operation, body, perform):
        assert operation == 'shift.open'
        assert request.headers['Idempotency-Key'] == 'same-canonical-key'
        return perform()
    monkeypatch.setattr(operations, 'open_shift_in_tx', canonical)
    monkeypatch.setattr(operations, 'mutate', mutate)
    body = operations.ShiftOpen(kind='driver', manager_shift_id='morning', vehicle_inspection=inspection())
    before = multibrand.operation_brand.get()
    result = workdays.open_workday(body, SimpleNamespace(headers={'Idempotency-Key': 'same-canonical-key'}), user)
    assert result['shift']['id'] == 'driver-morning'
    assert calls == [(user, body)]
    assert multibrand.operation_brand.get() == before
