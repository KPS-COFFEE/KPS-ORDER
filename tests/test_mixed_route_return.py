import pytest
from fastapi import HTTPException
from app import driver_work_hours as hours, central_workdays, multibrand


class Connection:
    def execute(self, sql, args):
        return self
    def fetchall(self):
        return [{'id': 'enb-shift', 'brand_id': 'enbishan', 'branch_ids': ['enb-return']}]


def test_shared_return_branch_uses_target_membership_and_shift(monkeypatch):
    monkeypatch.setattr(central_workdays, 'association', lambda *a: {'id': 'day'})
    monkeypatch.setattr(multibrand, 'project', lambda c,u,b: {**u,'brand_id':b,'branch_ids':['enb-return']})
    checks=[]
    monkeypatch.setattr(hours, 'shift_access', lambda c,u,s: checks.append((u['brand_id'],s['id'])))
    monkeypatch.setattr(hours, 'branch_evidence', lambda c,u,s,b: {'source':'explicit'})
    result=hours.return_branch_evidence(Connection(),{'id':'driver','brand_id':'kps','branch_ids':['kps']},
                                       {'id':'anchor','branch_ids':['kps']},hours.Movement(branch_id='enb-return'))
    assert result['return_brand_id']=='enbishan'
    assert checks==[('enbishan','enb-shift')]


def test_return_branch_revocation_blocks_cross_brand_return(monkeypatch):
    monkeypatch.setattr(central_workdays, 'association', lambda *a: {'id':'day'})
    monkeypatch.setattr(multibrand, 'project', lambda *a: None)
    with pytest.raises(HTTPException) as exc:
        hours.return_branch_evidence(Connection(),{'id':'driver','brand_id':'kps','branch_ids':['kps']},
                                     {'id':'anchor','branch_ids':['kps']},hours.Movement(branch_id='enb-return'))
    assert exc.value.status_code==403


def test_return_does_not_mix_independent_shifts(monkeypatch):
    monkeypatch.setattr(central_workdays,'association',lambda *a:None)
    with pytest.raises(HTTPException):
        hours.return_branch_evidence(None,{'id':'driver','branch_ids':['kps']},{'id':'anchor','branch_ids':['kps']},hours.Movement(branch_id='elsewhere'))


@pytest.mark.parametrize('operation', ['current','depart','return'])
def test_trip_task_permission_rejection_precedes_database(monkeypatch,operation):
    monkeypatch.setattr(hours.auth,'has_permission',lambda *a:False)
    monkeypatch.setattr(hours.db,'tx',lambda:pytest.fail('denied call reached database'))
    user={'id':'driver','role':'driver'}
    with pytest.raises(HTTPException) as err:
        if operation=='current':hours.current_trip(user)
        elif operation=='depart':hours.depart(hours.Departure(shift_id='s',branch_id='b',order_ids=['o']),None,user)
        else:hours.return_trip('trip',hours.Movement(branch_id='b'),None,user)
    assert err.value.status_code==403
