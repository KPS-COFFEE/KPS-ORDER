from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app import driver_receipt_trial as trial, auth

NOW=datetime(2026,10,21,12,30,tzinfo=timezone.utc)

def shift():
    return {'brand_id':'kps','branch_ids':['branch2'],'delivery_window_snapshot':{'period':'morning'},
            'vehicle_log':{'receipt_trial':{'expires_at':(NOW+timedelta(minutes=20)).isoformat()}}}


def inputs():
    return ({'id':'owner','role':'owner','brand_id':'kps'},
            {'id':'driver','role':'driver','brand_id':'kps'},
            SimpleNamespace(kind='driver',vehicle_inspection=None,opening_halalas=0,reason='Owner trial'),
            {'branch_ids':['branch2'],'delivery_window_snapshot':{'period':'morning'}},
            [{'id':'branch2','branch_no':2,'city':'حفر الباطن'}])


def test_owner_morning_branch_two_approval(monkeypatch):
    calls=[];monkeypatch.setattr(auth,'require_owner_step_up',lambda c,u:calls.append(u['id']))
    trial.authorize_open(None,*inputs())
    assert calls==['owner']


@pytest.mark.parametrize('change', ['role','brand','kind','branch','city','manager_period','manager_scope','reason','inspection','cash'])
def test_trial_scope_rejects_expansion(monkeypatch,change):
    monkeypatch.setattr(auth,'require_owner_step_up',lambda c,u:None)
    actor,target,body,manager,branches=inputs()
    if change=='role':actor['role']='delivery_manager'
    elif change=='brand':target['brand_id']='other'
    elif change=='kind':body.kind='manager'
    elif change=='branch':branches[0]['branch_no']=3
    elif change=='city':branches[0]['city']='الرياض'
    elif change=='manager_period':manager['delivery_window_snapshot']['period']='evening'
    elif change=='manager_scope':manager['branch_ids']=['branch2','branch3']
    elif change=='reason':body.reason=' '
    elif change=='inspection':body.vehicle_inspection={}
    elif change=='cash':body.opening_halalas=100
    with pytest.raises(HTTPException):trial.authorize_open(None,actor,target,body,manager,branches)


def test_trial_cannot_skip_owner_step_up(monkeypatch):
    def denied(c,u):raise HTTPException(403)
    monkeypatch.setattr(auth,'require_owner_step_up',denied)
    with pytest.raises(HTTPException):trial.authorize_open(None,*inputs())


def test_receipt_assignment_expiration_and_evening_isolation():
    morning={'brand_id':'kps','snapshot':{'delivery_period':'morning'}}
    trial.assignment_guard(shift(),morning,NOW)
    with pytest.raises(HTTPException):trial.assignment_guard(shift(),morning,NOW+timedelta(minutes=20))
    with pytest.raises(HTTPException):trial.assignment_guard(shift(),{'brand_id':'kps','snapshot':{}},NOW)
    with pytest.raises(HTTPException):trial.assignment_guard(shift(),{'brand_id':'other','snapshot':{'delivery_period':'morning'}},NOW)


@pytest.mark.parametrize('status', ['picked_up','delivered'])
def test_trial_never_implies_physical_delivery(status):
    with pytest.raises(HTTPException):trial.physical_guard(shift(),status)
    trial.physical_guard({'vehicle_log':{}},status)


def test_accept_and_problem_remain_possible():
    trial.physical_guard(shift(),'accepted')
    trial.physical_guard(shift(),'problem')


def test_opening_log_records_unknown_inspection_honestly():
    class C:
        def execute(self,*a):return self
        def fetchone(self):return {'n':NOW}
    log=trial.opening_log(C(),{'id':'owner'},'Owner trial')
    assert log['start'] is None and log['vehicle']=={}
    assert log['receipt_trial']['inspection_deferred'] is True
    assert log['receipt_trial']['authorized_by']=='owner'
    assert 'odometer_km' not in str(log) and 'fuel_percent' not in str(log)
