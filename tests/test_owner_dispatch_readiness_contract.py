"""Real eligibility function keeps owner supervision and all driver guards."""
import pytest
from fastapi import HTTPException
from app import operations


class Rows:
    def __init__(self, values): self.values = values
    def execute(self, query, params=()):
        if 'SELECT id,city FROM branches' in query:
            return type('Result', (), {'fetchall': lambda _: [{'id': bid, 'city': 'Test city'} for bid in params[0]]})()
        if 'FROM effective_driver_tasks' in query:
            return type('Result', (), {'fetchall': lambda _: []})()
        if 'driver_breaks' in query: value = self.values.get('break')
        elif 'FROM shifts' in query: value = self.values.get('supervisor')
        elif 'FROM users' in query: value = self.values.get(params[0])
        else: raise AssertionError(query)
        return type('Result', (), {'fetchone': lambda _: value})()


def fixture_rows():
    shift = {'id':'driver-shift','kind':'driver','status':'open','user_id':'driver',
             'manager_shift_id':'owner-shift','brand_id':'test','branch_ids':['a'],'available':True,'vehicle_log':{}}
    rows = {'driver':{'id':'driver','active':True,'role':'driver','brand_id':'test','branch_ids':['a']},
            'supervisor':{'id':'owner-shift','status':'open','brand_id':'test','branch_ids':['a'],'user_id':'owner'},
            'owner':{'id':'owner','active':True,'role':'owner','brand_id':'test','branch_ids':[]}}
    return shift, rows


def test_owner_supervises_driver_without_delivery_manager_role():
    shift, rows = fixture_rows()
    assert operations.eligible_driver_shift(Rows(rows),shift,{'brand_id':'test','branch_id':'a'}) == rows['supervisor']


@pytest.mark.parametrize('risk',['paused','break','revoked','cross_brand','cross_branch','closed_supervisor','revoked_supervisor','branch_supervisor'])
def test_owner_dispatch_does_not_bypass_driver_or_supervisor_guards(risk):
    shift, rows = fixture_rows()
    if risk == 'paused': shift['available'] = False
    elif risk == 'break': rows['break'] = {'id':'active-break'}
    elif risk == 'revoked': rows['driver']['active'] = False
    elif risk == 'cross_brand': rows['driver']['brand_id'] = 'other'
    elif risk == 'cross_branch': rows['driver']['branch_ids'] = ['b']
    elif risk == 'closed_supervisor': rows['supervisor']['status'] = 'closed'
    elif risk == 'revoked_supervisor': rows['owner']['active'] = False
    elif risk == 'branch_supervisor': rows['owner']['role'] = 'branch_manager'
    with pytest.raises(HTTPException):
        operations.eligible_driver_shift(Rows(rows),shift,{'brand_id':'test','branch_id':'a'})
