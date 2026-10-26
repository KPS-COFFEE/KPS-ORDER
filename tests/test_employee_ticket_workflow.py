"""Persistent factual handoffs never execute the linked operational/financial command."""
from copy import deepcopy
import pytest
from pydantic import ValidationError
from app import db
from app.employee_ticket_workflow import Command,state,source,enabled,require_enabled
from test_operations import ops
from test_accounting import accountant
from test_employee_tickets import request_body


@pytest.mark.parametrize('change',[{'expected_version':True},{'due_hours':0},{'due_hours':721},{'action':'approve_refund'},{'note':'\x00bad'},{'evidence':['\x00proof']},{'assignee_id':'\x00target'},{'fallback_id':'\x00target'}])
def test_followup_does_not_accept_financial_or_unbounded_commands(change):
    with pytest.raises(ValidationError):Command(**(dict(expected_version=1,action='respond',note='Recorded factual answer')|change))


def test_reading_current_workflow_does_not_rewrite_recorded_history():
    events=[{'details':{'version':1,'status':'requested','responsible_user_id':'dispatcher'}},{'details':{'version':2,'status':'accepted','responsible_user_id':'assignee'}}]
    original=deepcopy(events);current=state(events);current['status']='changed locally'
    assert events==original and state(events)['responsible_user_id']=='assignee'


@pytest.mark.postgres
@pytest.mark.parametrize('environment',['testing','production'])
def test_named_acceptance_return_response_deadline_fallback_and_no_financial_mutation(ops,monkeypatch,environment):
    monkeypatch.setenv('APP_ENV',environment)
    monkeypatch.setenv('KPS_ADMIN_FACTUAL_WORKFLOW_ENABLED','true')
    actor,post,_,_=ops
    accountant()
    ticket=post('driver','/api/employee-tickets',request_body())
    path='/api/employee-tickets/'+ticket['id']+'/followup'
    initial=actor('driver').get(path).json()['workflow']
    assert initial['responsible_user_id']=='ops-driver' and initial['due_at']
    with db.tx() as c:
        before=c.execute('SELECT count(*) n FROM collections').fetchone()['n']
        original_permissions=c.execute('SELECT id,permissions FROM users ORDER BY id').fetchall()
    assigned=post('owner',path,dict(expected_version=1,action='assign',assignee_id='ops-manager',department='delivery',note='Explain the failed dispatch and attach the evidence',evidence_required='Dispatch record or contact reference',due_hours=2))['workflow']
    assert assigned['status']=='requested' and assigned['responsible_user_id']=='ops-owner'
    assert assigned['next_recipient_id']=='ops-manager' and assigned['department']=='delivery'
    assert assigned['fallback_users'] and all(p['id']=='ops-owner' for p in assigned['fallback_users'])
    assert actor('driver2').get(path).status_code==404
    assert actor('other').get(path).status_code==404
    post('manager',path,dict(expected_version=2,action='respond',note='Cannot respond before accepting'),status=403)
    accepted=post('manager',path,dict(expected_version=2,action='accept',note='I accept the dispatch evidence task'))['workflow']
    assert accepted['responsible_user_id']=='ops-manager'
    post('manager',path,dict(expected_version=3,action='respond',note='No receipt reference attached'),status=422)
    response=post('manager',path,dict(expected_version=3,action='respond',note='Dispatch record was found',evidence=['Dispatch 001']))['workflow']
    assert response['status']=='responded' and response['responsible_user_id']=='ops-owner'
    returned=post('owner',path,dict(expected_version=4,action='return',note='Specify the timestamp and reason for reassignment',due_hours=3))['workflow']
    assert returned['status']=='requested' and returned['responsible_user_id']=='ops-owner'
    assert returned['return_reason']=='Specify the timestamp and reason for reassignment'
    assert returned['next_recipient_id']=='ops-manager'
    post('manager',path,dict(expected_version=5,action='accept',note='I accept the exact correction'))
    post('manager',path,dict(expected_version=6,action='respond',note='The timestamp and reason are in the dispatch record',evidence=['Dispatch 002']))
    final=post('owner',path,dict(expected_version=7,action='complete',note='Facts accepted; source execution is separate'),key='workflow-complete-001')
    assert post('owner',path,dict(expected_version=7,action='complete',note='Facts accepted; source execution is separate'),key='workflow-complete-001')==final
    assert final['workflow']['status']=='completed' and final['no_source_mutation']
    assert len(final['workflow']['history'])==8
    assert actor('driver').get('/api/employee-tickets/'+ticket['id']).json()['ticket']['status']=='open'
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM collections').fetchone()['n']==before
        assert c.execute('SELECT id,permissions FROM users ORDER BY id').fetchall()==original_permissions


@pytest.mark.postgres
def test_decline_retains_dispatcher_then_revocation_and_cross_scope_assignment_fail(ops,monkeypatch):
    monkeypatch.setenv('KPS_ADMIN_FACTUAL_WORKFLOW_ENABLED','true')
    actor,post,_,_=ops;accountant()
    ticket=post('driver','/api/employee-tickets',request_body())
    path='/api/employee-tickets/'+ticket['id']+'/followup'
    post('owner',path,dict(expected_version=1,action='assign',assignee_id='ops-manager',department='delivery',note='Please supply the actual dispatch facts'))
    post('manager',path,dict(expected_version=2,action='accept',note='I accept this follow-up'))
    declined=post('manager',path,dict(expected_version=3,action='decline',note='I am absent; please choose an authorized replacement'))['workflow']
    assert declined['responsible_user_id']=='ops-owner' and declined['status']=='declined'
    post('owner',path,dict(expected_version=4,action='assign',assignee_id='ops-other',department='owner',note='Attempt to send to another brand'),status=422)
    post('owner',path,dict(expected_version=4,action='assign',assignee_id='ops-driver2',department='accounting',note='Driver must not acquire accounting authority'),status=422)
    post('owner',path,dict(expected_version=4,action='assign',assignee_id='ops-accountant',department='accounting',note='Accountant gathers the actual supporting record'))
    assert actor('manager').get(path).status_code==404
    with db.tx() as c:c.execute("UPDATE users SET active=false WHERE id='ops-accountant'")
    assert actor('accountant').get(path).status_code==404
    reassigned=post('owner',path,dict(expected_version=5,action='assign',assignee_id='ops-manager',department='delivery',note='Fallback owner assigns the available manager'))['workflow']
    assert reassigned['responsible_user_id']=='ops-owner' and reassigned['assignee_id']=='ops-manager'


@pytest.mark.postgres
def test_linked_vehicle_report_has_accountable_task_and_source_remains_independent(ops,monkeypatch):
    monkeypatch.setenv('KPS_ADMIN_FACTUAL_WORKFLOW_ENABLED','true')
    actor,post,_,_=ops
    created=post('driver','/api/vehicle-maintenance/reports',dict(branch_id='ops-branch',category='brakes',vehicle_description='Synthetic vehicle',description='Brake evidence requires maintenance review'))
    identifier=created['report']['id'];ticket=created['followup_ticket_id']
    native=actor('owner').get('/api/vehicle-maintenance/reports/'+identifier)
    assert native.status_code==200,native.text
    assert native.json()['followups'][0]['id']==ticket
    path='/api/employee-tickets/'+ticket+'/followup'
    post('owner',path,dict(expected_version=1,action='assign',assignee_id='ops-owner',department='maintenance',note='Inspect actual brake evidence with maintenance authority'))
    post('owner',path,dict(expected_version=2,action='accept',note='I accept investigating this report'))
    post('owner',path,dict(expected_version=3,action='respond',note='Factual investigation is recorded'))
    post('owner',path,dict(expected_version=4,action='complete',note='Information accepted; repair uses the existing report action'))
    assert actor('owner').get('/api/vehicle-maintenance/reports/'+identifier).json()['report']['status']=='open'
    assert actor('other').get('/api/employee-tickets/source/vehicle_report/'+identifier).status_code==404



def test_customer_cannot_use_linked_source_lookup_to_read_administrative_metadata():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:source(None,{'role':'customer','active':True},'order','owned-order')
    assert error.value.status_code==403



def test_followup_is_disabled_by_default_and_allows_explicit_production_activation(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.delenv('KPS_ADMIN_FACTUAL_WORKFLOW_ENABLED',raising=False)
    assert enabled() is False
    with pytest.raises(HTTPException) as error:require_enabled()
    assert error.value.status_code==404
    monkeypatch.setenv('KPS_ADMIN_FACTUAL_WORKFLOW_ENABLED','true')
    assert enabled() is True
    monkeypatch.setenv('APP_ENV','production')
    assert enabled() is True


@pytest.mark.postgres
def test_default_off_retains_ticket_access_and_source_behavior(ops,monkeypatch):
    monkeypatch.delenv('KPS_ADMIN_FACTUAL_WORKFLOW_ENABLED',raising=False)
    actor,post,_,_=ops
    ticket=post('driver','/api/employee-tickets',request_body())
    assert 'source' not in ticket
    assert 'workflow' not in actor('driver').get('/api/employee-tickets/'+ticket['id']).json()
    assert actor('manager').get('/api/employee-tickets/'+ticket['id']).status_code==404
    path='/api/employee-tickets/'+ticket['id']+'/followup'
    assert actor('owner').get(path).status_code==404
    post('owner',path,dict(expected_version=0,action='assign',assignee_id='ops-manager',note='Must not activate in production'),status=404)
    created=post('driver','/api/vehicle-maintenance/reports',dict(branch_id='ops-branch',category='brakes',vehicle_description='Synthetic vehicle',description='Original source behavior'))
    assert 'followup_ticket_id' not in created
    assert 'followups' not in actor('owner').get('/api/vehicle-maintenance/reports/'+created['report']['id']).json()
    with db.tx() as c:assert c.execute("SELECT count(*) n FROM audit_events WHERE action='employee.ticket.followup'").fetchone()['n']==0


@pytest.mark.postgres
def test_named_backup_can_reassign_within_department_and_receives_inactive_dispatcher_reply(ops,monkeypatch):
    monkeypatch.setenv('KPS_ADMIN_FACTUAL_WORKFLOW_ENABLED','true')
    actor,post,_,_=ops
    with db.tx() as c:
        c.execute("""INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions)
            VALUES('ops-backup','Authorized delivery backup','delivery_manager','ops-brand','["ops-branch"]','["orders_view"]')""")
        original_money=c.execute('SELECT count(*) n FROM collections').fetchone()['n']
    ticket=post('driver','/api/employee-tickets',request_body())
    path='/api/employee-tickets/'+ticket['id']+'/followup'
    body=dict(expected_version=1,action='assign',assignee_id='ops-manager',department='delivery',fallback_id='ops-backup',note='Please supply the actual delivery facts')
    assigned=post('owner',path,body)['workflow']
    assert assigned['fallback_user_id']=='ops-backup' and assigned['fallback_department']=='delivery'
    assert actor('backup').get('/api/employee-tickets').json()['tickets'][0]['id']==ticket['id']
    assert actor('backup').get(path).json()['workflow']['actions']==['assign']
    post('backup','/api/employee-tickets/'+ticket['id']+'/review',dict(expected_version=1,status='approved',note='Must not grant approval authority'),status=403)
    post('backup',path,dict(expected_version=2,action='assign',assignee_id='ops-driver',department='driver',note='Cannot move task to another department'),status=403)
    post('manager',path,dict(expected_version=2,action='accept',note='I accept collecting delivery evidence'))
    with db.tx() as c:c.execute("UPDATE users SET active=false WHERE id='ops-owner'")
    response=post('manager',path,dict(expected_version=3,action='respond',note='Actual delivery evidence recorded'))['workflow']
    assert response['status']=='responded' and response['responsible_user_id']=='ops-backup'
    assert response['dispatcher_id']=='ops-backup' and response['previous_dispatcher_id']=='ops-owner'
    assert response['handoff_reason']=='dispatcher_unavailable'
    assert 'complete' in actor('backup').get(path).json()['workflow']['actions']
    final=post('backup',path,dict(expected_version=4,action='complete',note='Facts received; original financial decision remains separate'))
    assert final['workflow']['status']=='completed' and final['no_source_mutation']
    with db.tx() as c:assert c.execute('SELECT count(*) n FROM collections').fetchone()['n']==original_money


@pytest.mark.postgres
def test_backup_scope_distinctness_revocation_and_real_reassignment_are_enforced(ops,monkeypatch):
    monkeypatch.setenv('KPS_ADMIN_FACTUAL_WORKFLOW_ENABLED','true')
    actor,post,_,_=ops
    with db.tx() as c:
        c.execute("""INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions)
            VALUES('ops-backup','Delivery backup','delivery_manager','ops-brand','["ops-branch"]','["orders_view"]')""")
    ticket=post('driver','/api/employee-tickets',request_body())
    path='/api/employee-tickets/'+ticket['id']+'/followup'
    body=dict(expected_version=1,action='assign',assignee_id='ops-manager',department='delivery',note='Collect actual dispatch evidence')
    post('owner',path,{**body,'fallback_id':'ops-manager'},status=422)
    post('owner',path,{**body,'fallback_id':'ops-other'},status=422)
    post('owner',path,{**body,'fallback_id':'ops-driver'},status=422)
    post('owner',path,{**body,'fallback_id':'ops-backup'})
    changed=post('backup',path,dict(expected_version=2,action='assign',assignee_id='ops-manager',department='delivery',fallback_id='ops-owner',note='Cover the unavailable dispatcher and keep the actual task'))['workflow']
    assert changed['dispatcher_id']=='ops-backup' and changed['responsible_user_id']=='ops-backup'
    assert changed['history'][-1]['details']['from_responsible_user_id']=='ops-owner'
    assert changed['fallback_user_id']=='ops-owner'
    with db.tx() as c:c.execute("UPDATE users SET active=false WHERE id='ops-backup'")
    assert actor('backup').get(path).status_code in {401,403,404}
    # The authorized owner can still recover the task without changing its source.
    restored=post('owner',path,dict(expected_version=3,action='assign',assignee_id='ops-manager',department='delivery',note='Recover task after recorded dispatcher is disabled'))['workflow']
    assert restored['responsible_user_id']=='ops-owner'
