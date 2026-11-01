"""Roster observations are local snapshots; writes never inherit their results."""
from app.driver_readiness import ReadinessObservation

class Connection:
    def __init__(self):self.calls=[];self.row={'id':'manager','branch_ids':['b1'],'active':True}
    def execute(self,statement,params):
        self.calls.append((statement,params));return ReadinessObservation._PointResult(self.row)

def test_same_snapshot_reuses_shared_manager_but_each_caller_owns_its_row():
    c=Connection();read=ReadinessObservation(c)
    first=read.execute('SELECT * FROM users WHERE id=%s',('manager',)).fetchone()
    first['active']=False;first['branch_ids'].append('ungranted')
    second=read.execute('SELECT * FROM users WHERE id=%s',('manager',)).fetchone()
    assert second=={'id':'manager','branch_ids':['b1'],'active':True}
    assert len(c.calls)==1
    read.execute('SELECT * FROM users WHERE id=%s',('other-manager',)).fetchone()
    assert len(c.calls)==2

def test_next_request_observes_changes_and_locking_or_nonallowlisted_reads_never_cache():
    c=Connection();read=ReadinessObservation(c)
    assert read.execute('SELECT * FROM users WHERE id=%s',('manager',)).fetchone()['active']
    c.row['active']=False
    assert not ReadinessObservation(c).execute('SELECT * FROM users WHERE id=%s',('manager',)).fetchone()['active']
    for statement in ('SELECT * FROM users WHERE id=%s FOR UPDATE','UPDATE users SET active=false WHERE id=%s',
                      'SELECT * FROM users WHERE id=ANY(%s)'):
        count=len(c.calls)
        read.execute(statement,('manager',));read.execute(statement,('manager',))
        assert len(c.calls)==count+2

def test_missing_point_result_is_local_and_cursor_consumption_matches_database():
    c=Connection();c.row=None;read=ReadinessObservation(c)
    assert read.execute('SELECT * FROM shifts WHERE id=%s',('missing',)).fetchone() is None
    assert read.execute('SELECT * FROM shifts WHERE id=%s',('missing',)).fetchall()==[]
    assert len(c.calls)==1
    c.row={'id':'missing'}
    result=ReadinessObservation(c).execute('SELECT * FROM shifts WHERE id=%s',('missing',))
    assert result.fetchone()=={'id':'missing'}
    assert result.fetchone() is None


import pytest
from app import db
from test_central_workdays import central, open_days
from test_enbishan_dual_brand import dual

@pytest.mark.postgres
def test_available_roster_cannot_authorize_assignment_after_driver_is_disabled(central):
    _,actor,post,_,order=central
    _,driver=open_days(central)
    response=actor('manager','kps').get('/api/operations/drivers')
    assert response.status_code==200,response.text
    before=next(d for d in response.json()['drivers'] if d['id']=='dual-driver')
    assert before['assignment_eligible']
    with db.tx() as c:c.execute("UPDATE users SET active=false WHERE id='dual-driver'")
    response=actor('manager','kps').get('/api/operations/drivers')
    assert response.status_code==200,response.text
    after=next(d for d in response.json()['drivers'] if d['id']=='dual-driver')
    assert after['availability']=='inactive' and not after['assignment_eligible']
    oid=order('kps')
    sid=next(s['id'] for s in driver['shifts'] if s['brand_id']=='kps')
    post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':sid},status=409)
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()
    # Manager membership changes are rechecked on the next authenticated read.
    with db.tx() as c:c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-manager' AND brand_id='enbishan'")
    assert actor('manager','enbishan').get('/api/operations/drivers').status_code==403
