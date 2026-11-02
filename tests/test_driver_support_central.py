"""A support request transfers the complete physical driver day atomically."""
import pytest
from app import db, driver_support
from test_central_workdays import central, open_days
from test_enbishan_dual_brand import dual


@pytest.mark.postgres
def test_central_driver_support_moves_all_ledgers_without_money(central):
    _, _, post, _, _ = central
    source, driver = open_days(central)
    target = post('owner', '/api/central-operations/workdays',
                  {'kind':'manager','branch_ids':['kps-branch-1','enbishan-test-1','enbishan-test-2']})
    d = next(s for s in driver['shifts'] if s['brand_id']=='kps')
    t = next(s for s in target['shifts'] if s['brand_id']=='kps')
    with db.tx() as c:
        before = c.execute("SELECT * FROM shifts WHERE user_id='dual-driver' ORDER BY brand_id").fetchall()
        money = c.execute('SELECT * FROM custody_events ORDER BY id').fetchall()
    requested=post('owner','/api/driver-support',{'driver_shift_id':d['id'],'target_manager_shift_id':t['id'],'reason':'دعم تشغيلي مشترك'})
    assert len(requested['legs'])==2
    create_approver()
    post('manager',f"/api/driver-support/{requested['request_id']}/accept",{},status=403)
    result=post('approver',f"/api/driver-support/{requested['request_id']}/accept",{})
    assert result['cash_transferred'] is False
    targets={s['brand_id']:s['id'] for s in target['shifts']}
    with db.tx() as c:
        after=c.execute("SELECT * FROM shifts WHERE user_id='dual-driver' ORDER BY brand_id").fetchall()
        assert after==[s|{'manager_shift_id':targets[s['brand_id']]} for s in before]
        assert c.execute('SELECT * FROM custody_events ORDER BY id').fetchall()==money


@pytest.mark.postgres
def test_support_second_ledger_failure_rolls_back_whole_driver(central, monkeypatch):
    _, _, post, _, _ = central
    source, driver = open_days(central)
    target=post('owner','/api/central-operations/workdays',{'kind':'manager','branch_ids':['kps-branch-1','enbishan-test-1','enbishan-test-2']})
    d=next(s for s in driver['shifts'] if s['brand_id']=='kps')
    t=next(s for s in target['shifts'] if s['brand_id']=='kps')
    requested=post('owner','/api/driver-support',{'driver_shift_id':d['id'],'target_manager_shift_id':t['id'],'reason':'دعم مشترك'})
    create_approver()
    original=driver_support.driver_snapshot
    def failure(c, source, driver):
        if driver['brand_id']=='kps':
            raise db.error(409,'SYNTHETIC_FAILURE','second brand failure')
        return original(c,source,driver)
    monkeypatch.setattr(driver_support,'driver_snapshot',failure)
    post('approver',f"/api/driver-support/{requested['request_id']}/accept",{},status=409)
    with db.tx() as c:
        assert {s['manager_shift_id'] for s in c.execute("SELECT manager_shift_id FROM shifts WHERE user_id='dual-driver'").fetchall()}=={s['id'] for s in source['shifts']}
        assert {r['status'] for r in c.execute('SELECT status FROM driver_support_requests').fetchall()}=={'pending'}


def create_approver():
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,extra_permissions) VALUES('dual-approver','Synthetic approver','admin','kps','[\"kps-branch-1\"]','[\"driver_support_approve\"]')")
        c.execute("INSERT INTO staff_brand_memberships(user_id,brand_id,branch_ids,granted_by) VALUES('dual-approver','enbishan','[\"enbishan-test-1\",\"enbishan-test-2\"]','dual-owner')")


@pytest.mark.postgres
def test_support_approver_must_keep_every_brand_membership(central):
    _, _, post, _, _ = central
    source, driver = open_days(central)
    target=post('owner','/api/central-operations/workdays',{'kind':'manager','branch_ids':['kps-branch-1','enbishan-test-1','enbishan-test-2']})
    d=next(s for s in driver['shifts'] if s['brand_id']=='kps')
    t=next(s for s in target['shifts'] if s['brand_id']=='kps')
    requested=post('owner','/api/driver-support',{'driver_shift_id':d['id'],'target_manager_shift_id':t['id'],'reason':'دعم مشترك'})
    create_approver()
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-approver' AND brand_id='enbishan'")
    post('approver',f"/api/driver-support/{requested['request_id']}/accept",{},status=403)
    with db.tx() as c:
        assert {s['manager_shift_id'] for s in c.execute("SELECT manager_shift_id FROM shifts WHERE user_id='dual-driver'").fetchall()}=={s['id'] for s in source['shifts']}
        assert {r['status'] for r in c.execute('SELECT status FROM driver_support_requests').fetchall()}=={'pending'}
