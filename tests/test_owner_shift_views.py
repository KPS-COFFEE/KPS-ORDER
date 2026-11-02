"""Owner read-only views filter before pagination, without touching closures."""
import pytest
from app import db
from test_operations import ops

pytestmark=pytest.mark.postgres

def test_owner_shift_views_scope_pagination_dates_and_preservation(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    with db.tx() as c:
        c.execute("UPDATE shifts SET opened_at='2026-10-01T20:00:00Z' WHERE id=ANY(%s)",([manager,driver],))
        for i in range(205):
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,opened_at,closed_at,delivery_operational_date) VALUES(%s,'manager','ops-manager','ops-brand','[\"ops-branch\"]','closed','2026-10-13T18:00:00Z',now(),'2026-10-13')",('TEST-history-'+str(i),))
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status) VALUES('TEST-foreign','manager','ops-other','ops-other','[\"ops-branch-other\"]','open')")
        before=c.execute('SELECT id,status,submission,opening_halalas FROM shifts ORDER BY id').fetchall()
    def view(which,**kw):
        r=actor('owner').get('/api/shifts',params={'owner_view':which,**kw})
        assert r.status_code==200,r.text
        return r.json()
    assert {s['id'] for s in view('current')['shifts']}=={manager,driver}
    assert view('review')['total']==0
    first=view('history');second=view('history',offset=50)
    assert first['total']==205 and first['next_offset']==50
    assert len(first['shifts'])==50
    assert not ({s['id'] for s in first['shifts']}&{s['id'] for s in second['shifts']})
    assert view('history',offset=200)['next_offset'] is None
    assert view('history',search='manager',start='2026-10-13',end='2026-10-13')['total']==205
    assert view('history',search='nonexistent')['total']==0
    assert view('history',end='2026-10-12')['total']==0
    assert actor('owner').get('/api/shifts?owner_view=history&start=2026-10-14&end=2026-10-13').status_code==422
    assert actor('owner').get('/api/shifts?owner_view=current&offset=-1').status_code==422
    assert actor('manager').get('/api/shifts?owner_view=current').status_code==403
    assert actor('driver').get('/api/shifts?owner_view=history').status_code==403
    foreign=actor('other').get('/api/shifts?owner_view=current').json()
    assert {s['id'] for s in foreign['shifts']}=={'TEST-foreign'}
    with db.tx() as c:
        assert before==c.execute('SELECT id,status,submission,opening_halalas FROM shifts ORDER BY id').fetchall()
        c.execute("UPDATE shifts SET status='closing' WHERE id=%s",(manager,))
        c.execute("UPDATE shifts SET status='submitted' WHERE id=%s",(driver,))
    assert view('current')['total']==0
    assert {s['id'] for s in view('review')['shifts']}=={manager,driver}
