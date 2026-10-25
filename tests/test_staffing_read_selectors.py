"""Fresh operational work ignores ended staffing, preserves pending finance."""
import pytest
from app import db, manager_workday
from test_central_workdays import central, inspection
from test_aggregate_driver_closure import submit_physical


class LegacySchema:
    """A non-multibrand read must not require the gated staffing column."""
    def execute(self, query, params):
        assert 'staffing_ended_at' not in query
        return self
    def fetchone(self): return None
    def fetchall(self): return []


def test_nonmultibrand_current_workspace_works_without_staffing_migration(monkeypatch):
    monkeypatch.setattr('app.multibrand.enabled',lambda:False)
    connection=LegacySchema()
    assert manager_workday.current_shift(connection,{'id':'manager','brand_id':'kps'}) is None
    assert manager_workday.workspace_orders(connection,{'id':'owner','role':'owner','brand_id':'kps'})==('FALSE',[])


@pytest.mark.postgres
def test_fresh_manager_workspace_excludes_receipted_unallocated_financial_shift(central):
    _,actor,post,_,order=central
    manager=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    sid=manager['shift']['id'];path='/api/central-operations/workdays/'+sid
    original=post('manager',path+'/manager-physical-submit',{'cash_halalas':0,'recipient_user_id':'dual-owner','reference':'Empty manager bag ending physical work','confirmed':True})
    post('owner',path+'/manager-physical-receipt',{'declaration_id':original['declaration_id'],'received_halalas':0,'confirmed':True})
    # An unassigned order does not make yesterday's physically ended manager
    # become the current workspace again merely because finance is pending.
    order('kps')
    with db.tx() as c:
        user=c.execute("SELECT * FROM users WHERE id='dual-manager'").fetchone()
        assert manager_workday.current_shift(c,user) is None
        assert manager_workday.workspace_orders(c,user)==('FALSE',[])
        assert manager_workday.workspace_orders(c,{'id':'dual-owner','role':'owner','brand_id':'kps'})==('FALSE',[])
        old=c.execute('SELECT status,staffing_ended_at FROM shifts WHERE id=%s',(sid,)).fetchone()
        assert old['status']=='submitted' and old['staffing_ended_at'] is not None
    new=post('manager','/api/central-operations/workdays',{'kind':'manager'})
    with db.tx() as c:
        current=manager_workday.current_shift(c,user)
        assert current['id']==new['shift']['id'] and current['id']!=sid
        _,values=manager_workday.workspace_orders(c,{'id':'dual-owner','role':'owner','brand_id':'kps'})
        assert new['shift']['id'] in values and sid not in values
        assert not c.execute('SELECT 1 FROM shared_manager_closure_allocations').fetchone()


@pytest.mark.postgres
def test_driver_fuel_start_accepts_only_completed_physical_staffing_not_live_shift(central):
    _,actor,post,_,_=central
    manager,driver,path,declaration=submit_physical(central)
    post('manager',path+'/physical-receipt',{'declaration_id':declaration['declaration_id'],'received_halalas':700,'confirmed':True,'reference':'Original actual driver bag'})
    request={'phase':'start','manager_shift_id':manager['shift']['id'],'reason':'aldrees_unavailable','fuel_percent':65,'odometer_km':1010}
    created=post('driver','/api/fuel-exceptions',request)
    assert created['status']=='requested'
    next_day=post('driver','/api/central-operations/workdays',{'kind':'driver','manager_shift_id':manager['shift']['id'],'vehicle_inspection':inspection(1010)})
    failed=post('driver','/api/fuel-exceptions',request,status=409)
    assert failed['detail']['code']=='SHIFT_ALREADY_OPEN'
    with db.tx() as c:
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(driver['shift']['id'],)).fetchone()['status']=='submitted'
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(next_day['shift']['id'],)).fetchone()['status']=='open'
        assert c.execute("SELECT count(*) n FROM fuel_exceptions WHERE driver_id='dual-driver'").fetchone()['n']==1
        assert not c.execute('SELECT 1 FROM shared_driver_closure_allocations').fetchone()
