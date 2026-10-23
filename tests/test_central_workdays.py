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
        c.execute("UPDATE users SET permissions=%s WHERE id='dual-accountant'",(db.json(['shifts_close']),))
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
def test_same_physical_plate_cannot_be_received_from_other_brand_ledger(central):
    _,_,post,_,_=central
    with db.tx() as c:
        c.execute("UPDATE users SET vehicle=%s WHERE id='dual-driver'",(db.json({'plate':'SYNTHETIC 123','type':'car'}),))
    manager,_=open_days(central)
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,vehicle) VALUES('opposite-home-driver','SYNTHETIC opposite home','driver','enbishan',%s,%s)",
                  (db.json(['enbishan-test-1','enbishan-test-2']),db.json({'plate':'SYNTHETIC123','type':'car'})))
        c.execute("INSERT INTO staff_brand_memberships(user_id,brand_id,branch_ids,granted_by) VALUES('opposite-home-driver','kps',%s,'dual-owner')",(db.json(['kps-branch-1']),))
    post('owner','/api/central-operations/workdays',{'kind':'driver','user_id':'opposite-home-driver',
         'manager_shift_id':manager['shift']['id'],'vehicle_inspection':inspection()},status=409)
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
