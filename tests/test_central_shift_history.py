"""Exact physical history counts and pages over current authorized brand rows."""
from datetime import datetime, timedelta, timezone
import pytest
from app import db
from test_central_workdays import central, open_days
from test_enbishan_dual_brand import dual


def seed_history(count, legacy=False):
    with db.tx() as c:
        for index in range(count):
            stamp=datetime(2026,1,1,18,tzinfo=timezone.utc)+timedelta(days=index)
            group='history-day-'+str(index)
            for brand,branch in [('kps','kps-branch-1'),('enbishan','enbishan-test-1')]:
                sid=f'history-{brand}-{index}'
                c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,closed_at,opening_halalas,submission) "
                          "VALUES(%s,'manager','dual-manager',%s,%s,'closed',%s,%s,%s,%s)",
                          (sid,brand,db.json([branch]),stamp,stamp+timedelta(hours=3),100 if brand=='kps' else 200,
                           db.json({'counted_halalas':100 if brand=='kps' else 200,'difference_halalas':0})))
            c.execute("INSERT INTO central_workdays(id,user_id,kind,anchor_shift_id,created_by) VALUES(%s,'dual-manager','manager',%s,'dual-owner')",
                      (group,f'history-kps-{index}'))
            for brand in ['kps','enbishan']:
                c.execute('INSERT INTO central_workday_legs(workday_id,brand_id,shift_id) VALUES(%s,%s,%s)',
                          (group,brand,f'history-{brand}-{index}'))
        if legacy:
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,closed_at) "
                      "VALUES('history-legacy','manager','dual-manager','kps','[\"kps-branch-1\"]','closed','2025-01-01','2025-01-02')")


def get_history(actor,query='owner_view=history',name='owner'):
    response=actor(name).get('/api/central-operations/shifts?'+query)
    assert response.status_code==200,response.text
    return response.json()


@pytest.mark.postgres
def test_physical_history_is_grouped_before_exact_count_and_pagination(central):
    _,actor,_,_,_=central
    seed_history(61,legacy=True)
    first=get_history(actor)
    second=get_history(actor,'owner_view=history&offset=50')
    exhausted=get_history(actor,'owner_view=history&offset=100')
    assert first['total']==second['total']==exhausted['total']==62
    assert len(first['shifts'])==50 and first['next_offset']==50
    assert len(second['shifts'])==12 and second['next_offset'] is None
    assert exhausted['shifts']==[] and exhausted['next_offset'] is None
    ids=[s['id'] for s in first['shifts']+second['shifts']]
    assert len(set(ids))==62
    assert ids[0]=='history-kps-60' and ids[-1]=='history-legacy'
    grouped=first['shifts'][0]
    assert len(grouped['central_legs'])==2
    assert grouped['opening_halalas']==300
    assert grouped['submission']['counted_halalas']==300
    assert set(grouped['branch_ids'])=={'kps-branch-1','enbishan-test-1'}


@pytest.mark.postgres
def test_search_foreign_leg_id_and_dates_select_one_complete_physical_day(central):
    _,actor,_,_,_=central
    seed_history(4)
    selected=get_history(actor,'owner_view=history&search=history-enbishan-2')
    assert selected['total']==1 and len(selected['shifts'])==1
    assert selected['shifts'][0]['id']=='history-kps-2'
    assert selected['shifts'][0]['opening_halalas']==300
    ranged=get_history(actor,'owner_view=history&start=2026-01-02&end=2026-01-03')
    assert ranged['total']==2
    assert {s['id'] for s in ranged['shifts']}=={'history-kps-1','history-kps-2'}
    response=actor('owner').get('/api/central-operations/shifts?owner_view=history&start=2026-01-03&end=2026-01-01')
    assert response.status_code==422


@pytest.mark.postgres
def test_revoked_foreign_branch_removes_entire_group_from_rows_and_total(central):
    _,actor,_,_,_=central
    seed_history(3,legacy=True)
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET branch_ids=%s WHERE user_id='dual-owner' AND brand_id='enbishan'",
                  (db.json(['enbishan-test-2']),))
    result=get_history(actor)
    assert result['total']==1
    assert [s['id'] for s in result['shifts']]==['history-legacy']


@pytest.mark.postgres
def test_delegated_history_requires_current_permission_and_each_branch_scope(central):
    _,actor,_,_,_=central
    seed_history(2,legacy=True)
    forbidden=actor('accountant').get('/api/central-operations/shifts?owner_view=history')
    assert forbidden.status_code==403
    with db.tx() as c:
        c.execute("UPDATE users SET permissions=%s WHERE id='dual-accountant'",(db.json(['shifts_view']),))
    result=get_history(actor,name='accountant')
    assert result['total']==3
    with db.tx() as c:
        c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-accountant' AND brand_id='enbishan'")
    result=get_history(actor,name='accountant')
    assert result['total']==1 and result['shifts'][0]['id']=='history-legacy'


@pytest.mark.postgres
def test_current_and_review_views_count_shared_workdays_once(central):
    _,actor,_,_,_=central
    manager,driver=open_days(central)
    current=get_history(actor,'owner_view=current')
    assert current['total']==2 and len(current['shifts'])==2
    with db.tx() as c:
        c.execute("UPDATE shifts SET status='closing' WHERE id=ANY(%s)",([s['id'] for s in manager['shifts']],))
        c.execute("UPDATE shifts SET status='submitted' WHERE id=ANY(%s)",([s['id'] for s in driver['shifts']],))
    assert get_history(actor,'owner_view=current')['total']==0
    review=get_history(actor,'owner_view=review')
    assert review['total']==2 and len(review['shifts'])==2


@pytest.mark.postgres
def test_default_off_hides_new_history_but_existing_grouped_days_can_drain(central,monkeypatch):
    _,actor,_,_,_=central
    monkeypatch.setenv('KPS_CENTRAL_OPERATIONS_ENABLED','false')
    assert actor('owner').get('/api/central-operations/shifts?owner_view=history').status_code==404
    seed_history(1)
    assert get_history(actor)['total']==1
