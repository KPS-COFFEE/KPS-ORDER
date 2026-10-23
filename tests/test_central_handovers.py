"""PostgreSQL witnesses: manager succession cannot split shared driver parents."""
import pytest
from app import db, operations as ops
from test_central_workdays import central, open_days
from test_enbishan_dual_brand import dual


def setup_handover(central):
    _, _, post, _, _ = central
    source, driver = open_days(central)
    target = post('owner', '/api/central-operations/workdays',
                  {'kind':'manager','branch_ids':['kps-branch-1','enbishan-test-1','enbishan-test-2']})
    body={'target_manager_shift_id':target['shift']['id'], 'reason':'Synthetic atomic manager succession'}
    path='/api/central-operations/workdays/'+source['shift']['id']+'/handover-manager'
    return source, driver, target, path, body


def parents():
    with db.tx() as c:
        return {r['brand_id']:r['manager_shift_id'] for r in c.execute(
            "SELECT brand_id,manager_shift_id FROM shifts WHERE user_id='dual-driver'").fetchall()}


@pytest.mark.postgres
def test_atomic_handover_preserves_brand_cash_and_shared_driver_identity(central):
    _, actor, post, _, _ = central
    source, driver, target, path, body = setup_handover(central)
    with db.tx() as c:
        before=c.execute('SELECT id,brand_id,opening_halalas FROM shifts ORDER BY id').fetchall()
        cash=c.execute('SELECT * FROM custody_events ORDER BY id').fetchall()
    pending=post('manager',path,body,key='central-handover-request')
    assert pending['status']=='pending' and len(pending['legs'])==2
    assert len(set(parents().values()))==2
    assert set(parents().values())=={s['id'] for s in source['shifts']}
    assert post('manager',path,body,key='central-handover-request')==pending
    listed=actor('owner').get('/api/central-operations/manager-handovers')
    assert listed.status_code==200,listed.text
    assert [h['id'] for h in listed.json()['handovers']]==[pending['id']]
    assert listed.json()['handovers'][0]['from_name']=='SYNTHETIC dual-manager'
    accept='/api/central-operations/manager-handovers/'+pending['id']+'/accept'
    accepted=post('owner',accept,{},key='central-handover-accept')
    assert accepted['status']=='accepted' and accepted['cash_transferred'] is False
    assert set(parents().values())=={s['id'] for s in target['shifts']}
    assert post('owner',accept,{},key='central-handover-accept')==accepted
    detail=actor('driver').get('/api/central-operations/workdays/'+driver['shift']['id'])
    assert detail.status_code==200,detail.text
    with db.tx() as c:
        assert c.execute('SELECT id,brand_id,opening_halalas FROM shifts ORDER BY id').fetchall()==before
        assert c.execute('SELECT * FROM custody_events ORDER BY id').fetchall()==cash
        assert {h['status'] for h in c.execute('SELECT status FROM manager_handovers').fetchall()}=={'accepted'}
        assert len(c.execute('SELECT * FROM central_manager_handovers').fetchall())==1


@pytest.mark.postgres
def test_second_brand_request_failure_rolls_back_all_records_and_shift_status(central,monkeypatch):
    _, _, post, _, _ = central
    source, _, _, path, body = setup_handover(central)
    original=ops.manager_handover_in_tx
    def fail_second(c,user,sid,body):
        result=original(c,user,sid,body)
        if user['brand_id']=='kps':
            raise db.error(409,'SYNTHETIC_FAILURE','synthetic second ledger failure')
        return result
    monkeypatch.setattr(ops,'manager_handover_in_tx',fail_second)
    post('manager',path,body,status=409)
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM central_manager_handovers').fetchone()
        assert not c.execute('SELECT 1 FROM manager_handovers').fetchone()
        assert {r['status'] for r in c.execute('SELECT status FROM shifts WHERE id=ANY(%s)',
            ([s['id'] for s in source['shifts']],)).fetchall()}=={'open'}


@pytest.mark.postgres
def test_second_brand_acceptance_failure_rolls_back_reparenting_and_can_retry(central,monkeypatch):
    _, _, post, _, _ = central
    source, _, target, path, body = setup_handover(central)
    pending=post('manager',path,body)
    original=ops.accept_manager_handover_in_tx
    def fail_second(c,user,hid):
        result=original(c,user,hid)
        if user['brand_id']=='kps':
            raise db.error(409,'SYNTHETIC_FAILURE','synthetic second ledger failure')
        return result
    monkeypatch.setattr(ops,'accept_manager_handover_in_tx',fail_second)
    accept='/api/central-operations/manager-handovers/'+pending['id']+'/accept'
    post('owner',accept,{},status=409,key='central-failed-accept')
    assert set(parents().values())=={s['id'] for s in source['shifts']}
    with db.tx() as c:
        assert {h['status'] for h in c.execute('SELECT status FROM manager_handovers').fetchall()}=={'pending'}
    monkeypatch.setattr(ops,'accept_manager_handover_in_tx',original)
    post('owner',accept,{},key='central-failed-accept')
    assert set(parents().values())=={s['id'] for s in target['shifts']}


@pytest.mark.postgres
def test_canonical_accept_cannot_split_a_pending_group(central):
    _, _, post, _, _ = central
    source, _, _, path, body = setup_handover(central)
    pending=post('manager',path,body)
    for leg in pending['legs']:
        post('owner','/api/manager-handovers/'+leg['id']+'/accept',{},brand=leg['brand_id'],status=409)
    assert set(parents().values())=={s['id'] for s in source['shifts']}


@pytest.mark.postgres
def test_only_real_recipient_can_accept_and_revoked_scope_hides_group(central):
    _, actor, post, _, _ = central
    source, _, _, path, body = setup_handover(central)
    pending=post('manager',path,body,key='central-revocation-request')
    accept='/api/central-operations/manager-handovers/'+pending['id']+'/accept'
    post('manager',accept,{},status=403)
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-manager' AND brand_id='enbishan'")
    post('manager',path,body,key='central-revocation-request',status=404)
    assert actor('manager').get('/api/central-operations/manager-handovers').json()['handovers']==[]
    assert set(parents().values())=={s['id'] for s in source['shifts']}


@pytest.mark.postgres
def test_stale_receiver_scope_rolls_back_entire_acceptance(central):
    _, _, post, _, _ = central
    source, _, _, path, body = setup_handover(central)
    pending=post('manager',path,body)
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-owner' AND brand_id='enbishan'")
    post('owner','/api/central-operations/manager-handovers/'+pending['id']+'/accept',{},status=404)
    assert set(parents().values())=={s['id'] for s in source['shifts']}
    with db.tx() as c:
        assert {h['status'] for h in c.execute('SELECT status FROM manager_handovers').fetchall()}=={'pending'}


@pytest.mark.postgres
def test_incomplete_target_workday_cannot_take_shared_driver(central):
    _, _, post, _, _ = central
    source, _ = open_days(central)
    target=post('owner','/api/central-operations/workdays',{'kind':'manager','branch_ids':['kps-branch-1']})
    post('manager','/api/central-operations/workdays/'+source['shift']['id']+'/handover-manager',
         {'target_manager_shift_id':target['shift']['id'],'reason':'Incomplete synthetic target'},status=409)
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM manager_handovers').fetchone()


@pytest.mark.postgres
def test_morning_policy_failure_does_not_partially_request_handover(central):
    _, _, post, _, _ = central
    source, _, _, path, body = setup_handover(central)
    with db.tx() as c:
        c.execute("UPDATE shifts SET delivery_window_snapshot=%s WHERE id=%s",
                  (db.json({'period':'morning'}),next(s['id'] for s in source['shifts'] if s['brand_id']=='kps')))
    post('manager',path,body,status=409)
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM manager_handovers').fetchone()
        assert not c.execute('SELECT 1 FROM central_manager_handovers').fetchone()


@pytest.mark.postgres
def test_projected_owner_branch_restriction_hides_all_group_records_and_acceptance(central):
    _, actor, post, _, _ = central
    source, _, _, path, body = setup_handover(central)
    pending=post('manager',path,body)
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids=%s WHERE user_id='dual-owner' AND brand_id='enbishan'",
                  (db.json(['enbishan-test-1']),))
    listed=actor('owner').get('/api/central-operations/manager-handovers')
    assert listed.status_code==200 and listed.json()['handovers']==[]
    post('owner','/api/central-operations/manager-handovers/'+pending['id']+'/accept',{},status=404)
    assert set(parents().values())=={s['id'] for s in source['shifts']}


@pytest.mark.postgres
def test_existing_handover_can_drain_when_central_rollout_is_disabled(central,monkeypatch):
    _, actor, post, _, _ = central
    _, _, target, path, body = setup_handover(central)
    pending=post('manager',path,body)
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED','false')
    listed=actor('owner').get('/api/central-operations/manager-handovers')
    assert listed.status_code==200,listed.text
    assert listed.json()['handovers'][0]['id']==pending['id']
    accepted=post('owner','/api/central-operations/manager-handovers/'+pending['id']+'/accept',{})
    assert accepted['status']=='accepted'
    assert set(parents().values())=={s['id'] for s in target['shifts']}


@pytest.mark.postgres
def test_successor_can_cover_more_branches_than_requesting_manager(central):
    _, _, post, _, _ = central
    source=post('manager','/api/central-operations/workdays',{'kind':'manager','branch_ids':['kps-branch-1','enbishan-test-1']})
    target=post('owner','/api/central-operations/workdays',
                {'kind':'manager','branch_ids':['kps-branch-1','enbishan-test-1','enbishan-test-2']})
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids=%s WHERE user_id='dual-manager' AND brand_id='enbishan'",
                  (db.json(['enbishan-test-1']),))
    pending=post('manager','/api/central-operations/workdays/'+source['shift']['id']+'/handover-manager',
                 {'target_manager_shift_id':target['shift']['id'],'reason':'Successor has broader approved coverage'})
    accepted=post('owner','/api/central-operations/manager-handovers/'+pending['id']+'/accept',{})
    assert accepted['status']=='accepted'
