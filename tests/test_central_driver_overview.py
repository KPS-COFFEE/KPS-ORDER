"""Shared driver screens retain current scopes and canonical ledger policy."""
import pytest
from app import db
from test_central_workdays import central, open_days
from test_enbishan_dual_brand import dual


@pytest.mark.postgres
def test_destination_unions_current_branches_and_saves_one_physical_intent(central):
    _,actor,post,_,_=central
    _,driver=open_days(central)
    path='/api/central-operations/driver-destination'
    response=actor('driver').get(path)
    assert response.status_code==200,response.text
    view=response.json()
    assert {b['id'] for b in view['branches']}=={'kps-branch-1','enbishan-test-1','enbishan-test-2'}
    assert not view['external_enbishan_allowed']
    selected=post('driver',path,{'kind':'branch','branch_id':'enbishan-test-2'},method='put')
    assert selected['next_destination']['branch_id']=='enbishan-test-2'
    post('driver',path,{'kind':'branch','branch_id':'kps-branch-1'},method='put')
    with db.tx() as c:
        rows=c.execute('SELECT next_destination FROM shifts WHERE id=ANY(%s)',([s['id'] for s in driver['shifts']],)).fetchall()
        assert sum(bool(s['next_destination']) for s in rows)==1


@pytest.mark.postgres
def test_other_ledger_task_blocks_destination_and_invalidates_prior_intent(central):
    _,actor,post,_,order=central
    _,driver=open_days(central)
    path='/api/central-operations/driver-destination'
    post('driver',path,{'kind':'branch','branch_id':'enbishan-test-1'},method='put')
    kps=next(s for s in driver['shifts'] if s['brand_id']=='kps')
    post('manager','/api/orders/'+order('kps')+'/assign',{'driver_shift_id':kps['id']})
    view=actor('driver').get(path).json()
    assert view['visible_active_task_count']==1
    assert view['next_destination'] is None
    post('driver',path,{'kind':'branch','branch_id':'enbishan-test-1'},method='put',status=409)
    with db.tx() as c:
        c.execute("UPDATE driver_tasks SET status='delivered' WHERE shift_id=%s",(kps['id'],))
    assert actor('driver').get(path).json()['next_destination'] is None


@pytest.mark.postgres
def test_destination_rechecks_all_workday_grants(central):
    _,actor,_,_,_=central
    open_days(central)
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-driver' AND brand_id='enbishan'")
    response=actor('driver').get('/api/central-operations/driver-destination')
    assert response.status_code==404,response.text


@pytest.mark.postgres
def test_incentives_union_canonical_ledgers_and_recheck_revocation(central):
    _,actor,post,_,order=central
    _,driver=open_days(central)
    for shift in driver['shifts']:
        oid=order(shift['brand_id'])
        post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':shift['id']},brand=shift['brand_id'])
    with db.tx() as c:
        c.execute("UPDATE driver_tasks SET status='delivered' WHERE driver_id='dual-driver'")
        c.execute("UPDATE orders SET payment_status='paid' WHERE id IN (SELECT order_id FROM driver_tasks WHERE driver_id='dual-driver')")
        c.execute("UPDATE shifts SET status='closed' WHERE user_id='dual-driver'")
    path='/api/central-operations/driver-incentives'
    response=actor('driver').get(path)
    assert response.status_code==200,response.text
    report=response.json()
    assert report['eligible']==2
    assert {r['brand_id'] for r in report['rows']}=={'kps','enbishan'}
    assert report['entries']==[]
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-driver' AND brand_id='enbishan'")
    report=actor('driver').get(path).json()
    assert report['eligible']==1
    assert {r['brand_id'] for r in report['rows']}=={'kps'}
    assert actor('driver').get(path+'?driver_id=someone-else').status_code==403
