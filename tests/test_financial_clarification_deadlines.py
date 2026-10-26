"""Deadlines track evidence work, without settling its linked financial case."""
from datetime import datetime, timedelta, timezone
import pytest
from pydantic import ValidationError
from app.financial_clarifications import Question, Decision, followup, command_payload


@pytest.mark.parametrize('value',[True,0,721,'24',1.5])
def test_deadline_requires_bounded_whole_hours(value):
    with pytest.raises(ValidationError):
        Question(version=1,assignee_id='manager',question='Missing receipt',due_hours=value)
    with pytest.raises(ValidationError):
        Decision(expected_version=1,action='return',note='Receipt required',due_hours=value)


def test_returned_requirement_and_overdue_remain_visible_without_financial_completion():
    now=datetime(2026,10,26,tzinfo=timezone.utc)
    row=dict(status='accepted',requested_by='accountant',assignee_id='manager',due_at=now-timedelta(hours=1))
    history=[dict(action='requested',note='Explain collection'),dict(action='returned',note='Attach terminal receipt'),dict(action='accepted',note='I will supply receipt')]
    result=followup(row,history,now)
    assert result['overdue'] and result['deadline_documented']
    assert result['required_correction']=='Attach terminal receipt'
    assert result['next_action']=='manager_response'
    assert result['next_recipient_id']=='accountant'
    assert result['escalation_user_id']=='accountant'
    assert result['authorized_fallback_role']=='owner'
    assert result['financial_resolution_independent']
    assert row['status']=='accepted' and len(history)==3


@pytest.mark.parametrize('state,action,target',[
    ('requested','manager_acceptance','manager'),
    ('declined','accounting_reassignment',None),
    ('responded','accounting_review','accountant'),
])
def test_handoff_destination_uses_recorded_identity(state,action,target):
    result=followup(dict(status=state,requested_by='accountant',assignee_id='manager'),[])
    assert result['next_action']==action and result['next_recipient_id']==target
    assert not result['deadline_documented'] and not result['overdue']


def test_resolved_clarification_does_not_claim_financial_resolution():
    result=followup(dict(status='resolved',requested_by='accountant',assignee_id='manager',due_at=datetime(2020,1,1,tzinfo=timezone.utc)),[])
    assert result['next_action'] is None and not result['overdue']
    assert result['authorized_fallback_role'] is None
    assert result['financial_resolution_independent']


def test_deployment_preserves_payload_hash_for_older_durable_retries():
    old={'expected_version':1,'action':'accept','note':'Accepted by manager','evidence':[],'assignee_id':None}
    assert command_payload(Decision.model_validate(old))==old
    explicit=Question(version=1,assignee_id='manager',question='Missing receipt',due_hours=48)
    assert command_payload(explicit)['due_hours']==48
