"""Actual accepted evidence handoffs, without mutation of financial source rows."""
from uuid import uuid4
import pytest
from psycopg.errors import RaiseException
from pydantic import ValidationError
from app import db
from app.financial_clarifications import Question, Decision
from test_operations import ops
from test_delivery_workflow_controls import picked_up
from test_accounting import accountant


@pytest.mark.parametrize('body',[
    {'version':True,'assignee_id':'manager','question':'Missing payment evidence'},
    {'version':1,'assignee_id':'manager','question':'\x00invalid'},
    {'version':1,'assignee_id':'manager','question':'ok'},
])
def test_question_input_is_strict(body):
    with pytest.raises(ValidationError):Question.model_validate(body)


@pytest.mark.parametrize('body',[
    {'expected_version':True,'action':'accept','note':'Accept request'},
    {'expected_version':1,'action':'approve_payment','note':'Unsupported financial action'},
    {'expected_version':1,'action':'respond','note':'Actual response','evidence':['\x00invalid']},
])
def test_clarification_cannot_encode_financial_or_invalid_commands(body):
    with pytest.raises(ValidationError):Decision.model_validate(body)


def financial_source(oid):
    with db.tx() as c:
        return {'order':c.execute('SELECT version,total_halalas,payment_status,preparation_status,delivery_status FROM orders WHERE id=%s',(oid,)).fetchone(),
            'collections':c.execute('SELECT * FROM collections WHERE order_id=%s ORDER BY id',(oid,)).fetchall(),
            'refunds':c.execute('SELECT * FROM refunds WHERE order_id=%s ORDER BY id',(oid,)).fetchall(),
            'case_events':c.execute("SELECT * FROM order_events WHERE order_id=%s AND event_type IN ('financial_case.opened','financial_case.resolved') ORDER BY id",(oid,)).fetchall()}


def setup_case(ops):
    actor,post,_,_=ops
    accountant()
    oid,_,_,_=picked_up(ops,total=2500)
    post('driver',f'/api/orders/{oid}/collect',{'method':'cash','amount_halalas':1950,'tendered_halalas':1950,'shortfall_reason':'Customer paid less; please investigate'})
    return oid


@pytest.mark.postgres
def test_clarification_accept_response_return_and_accountant_acceptance_preserve_source(ops):
    actor,post,_,_=ops
    oid=setup_case(ops)
    source=financial_source(oid)
    url='/api/financial-clarifications/orders/'+oid
    context=actor('accountant').get(url)
    assert context.status_code==200,context.text
    assert {p['id'] for p in context.json()['assignees']}=={'ops-manager'}
    payload={'version':context.json()['order_version'],'assignee_id':'ops-manager','question':'Confirm why the customer paid 19.50 and provide the receipt reference'}
    post('manager',url,payload,status=403)
    post('accountant',url,{**payload,'version':payload['version']+1},status=409)
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('ops-foreign-manager','Foreign manager','delivery_manager','ops-other','[\"ops-branch-other\"]')")
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions) VALUES('ops-reviewer2','Other accountant','admin','ops-brand','[\"ops-branch\"]','[\"accounting_review\"]')")
    post('accountant',url,{**payload,'assignee_id':'ops-foreign-manager'},status=422)
    key=uuid4().hex
    created=post('accountant',url,payload,key=key)
    assert post('accountant',url,payload,key=key)==created
    assert created['clarification']['due_at'] and created['clarification']['deadline_documented']
    row=created['clarification'];path='/api/financial-clarifications/'+row['id']+'/decision'
    assert row['status']=='requested' and row['responsible_user_id']=='ops-accountant' and row['awaiting_acceptance']
    assert actor('other').get(url).status_code==404
    post('driver',path,{'expected_version':1,'action':'accept','note':'Unauthorized manager impersonation'},status=404)
    post('manager',path,{'expected_version':1,'action':'respond','note':'Must first accept ownership'},status=409)
    row=post('manager',path,{'expected_version':1,'action':'accept','note':'I accept gathering these facts'})['clarification']
    assert row['status']=='accepted' and row['responsible_user_id']=='ops-manager'
    post('manager',path,{'expected_version':1,'action':'accept','note':'Stale duplicate with another key'},status=409)
    row=post('manager',path,{'expected_version':2,'action':'respond','note':'Free cookie returned and customer paid 19.50','evidence':['Customer receipt 001']})['clarification']
    assert row['status']=='responded' and row['responsible_user_id']=='ops-accountant'
    post('reviewer2',path,{'expected_version':3,'action':'resolve','note':'Another accountant cannot accept the original reviewer response'},status=403)
    # A response is not financial completion and cannot substitute for actual recovery.
    blocked=post('accountant',f'/api/order-financial-cases/orders/{oid}/resolve',{'reason':'Attempt to close before evidence accepted'},status=409)
    assert blocked['detail']['code']=='FINANCIAL_CLARIFICATION_PENDING'
    row=post('accountant',path,{'expected_version':3,'action':'return','note':'Specify who authorized the deduction; free cookie has zero invoice value'})['clarification']
    assert row['status']=='requested' and row['awaiting_acceptance']
    assert row['required_correction']=='Specify who authorized the deduction; free cookie has zero invoice value'
    assert row['history'][-1]['note']==row['required_correction']
    post('manager',path,{'expected_version':4,'action':'accept','note':'I accept the correction request'})
    post('manager',path,{'expected_version':5,'action':'respond','note':'No discount was authorized; the shortfall must remain pending','evidence':['Manager statement 002']})
    row=post('accountant',path,{'expected_version':6,'action':'resolve','note':'Facts accepted; financial difference remains separately pending'})['clarification']
    assert row['status']=='resolved' and row['actions']==[]
    assert [e['action'] for e in row['history']]==['requested','accepted','responded','returned','accepted','responded','resolved']
    assert len({e['id'] for e in row['history']})==7
    assert financial_source(oid)==source
    unresolved=post('accountant',f'/api/order-financial-cases/orders/{oid}/resolve',{'reason':'Clarification alone is not actual collection'},status=409)
    assert unresolved['detail']['code']=='ACTUAL_COLLECTION_REQUIRED'
    with pytest.raises(RaiseException):
        with db.tx() as c:c.execute('UPDATE financial_case_clarification_events SET note=%s WHERE clarification_id=%s',('Overwritten evidence',row['id']))


@pytest.mark.postgres
def test_decline_reassign_revocation_and_case_guard(ops):
    actor,post,_,_=ops
    oid=setup_case(ops);url='/api/financial-clarifications/orders/'+oid
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('ops-deputy','Deputy manager','delivery_manager','ops-brand','[\"ops-branch\"]')")
    context=actor('accountant').get(url).json()
    row=post('accountant',url,{'version':context['order_version'],'assignee_id':'ops-manager','question':'Please obtain the actual receipt evidence'})['clarification']
    path='/api/financial-clarifications/'+row['id']+'/decision'
    row=post('manager',path,{'expected_version':1,'action':'decline','note':'I was not on this shift; please assign the eligible deputy'})['clarification']
    assert row['status']=='declined' and row['responsible_user_id']=='ops-accountant'
    row=post('accountant',path,{'expected_version':2,'action':'reassign','assignee_id':'ops-deputy','note':'Deputy manager covers this branch and will provide the source evidence'})['clarification']
    assert row['status']=='requested' and row['assignee_id']=='ops-deputy'
    post('manager',path,{'expected_version':3,'action':'accept','note':'Previous assignee cannot act after reassignment'},status=404)
    with db.tx() as c:
        c.execute("UPDATE users SET active=false WHERE id='ops-deputy'")
    post('deputy',path,{'expected_version':3,'action':'accept','note':'Revoked deputy cannot accept'},status=404)
    with db.tx() as c:
        from app.financial_clarifications import guard_case_resolution
        from app.order_financial_cases import case
        with pytest.raises(Exception) as exc:guard_case_resolution(c,oid,case(c,oid)['id'])
        assert exc.value.detail['code']=='FINANCIAL_CLARIFICATION_PENDING'


@pytest.mark.postgres
def test_direct_driver_evidence_request_is_scoped_and_does_not_settle_money(ops,monkeypatch):
    monkeypatch.setenv('KPS_ADMIN_FACTUAL_WORKFLOW_ENABLED','true')
    actor,post,_,_=ops
    oid=setup_case(ops)
    source=financial_source(oid)
    url='/api/financial-clarifications/orders/'+oid
    context=actor('accountant').get(url).json()
    row=post('accountant',url,{'version':context['order_version'],'assignee_id':'ops-driver','question':'Attach the delivery proof and describe the actual payment received'})['clarification']
    mine=actor('driver').get('/api/financial-clarifications/mine').json()['clarifications']
    assert len(mine)==1 and mine[0]['order_id']==oid
    assert set(mine[0])=={'id','order_id','order_no','question','status','due_at','version'}
    assert actor('manager').get('/api/financial-clarifications/mine').json()['clarifications']==[]
    path='/api/financial-clarifications/'+row['id']+'/decision'
    row=post('driver',path,{'expected_version':1,'action':'accept','note':'I will supply the delivery proof'})['clarification']
    assert row['responsible_role']=='driver' and row['responsible_user_id']=='ops-driver'
    row=post('driver',path,{'expected_version':2,'action':'respond','note':'Customer paid the amount I recorded','evidence':['Delivery evidence actual-001']})['clarification']
    assert row['status']=='responded' and row['responsible_user_id']=='ops-accountant'
    assert actor('driver').get('/api/financial-clarifications/mine').json()['clarifications']==[]
    assert financial_source(oid)==source
    with db.tx() as c:c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-driver'")
    assert actor('driver').get(url).status_code==404

@pytest.mark.postgres
def test_inactive_reviewer_has_named_accounting_fallback_and_recorded_takeover(ops,monkeypatch):
    monkeypatch.setenv('KPS_ADMIN_FACTUAL_WORKFLOW_ENABLED','true')
    actor,post,_,_=ops
    oid=setup_case(ops)
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions) VALUES('ops-reviewer2','Accounting alternate','admin','ops-brand','[\"ops-branch\"]','[\"accounting_review\"]')")
    url='/api/financial-clarifications/orders/'+oid
    version=actor('accountant').get(url).json()['order_version']
    row=post('accountant',url,{'version':version,'assignee_id':'ops-manager','question':'Confirm the actual payment reference'})['clarification']
    assert row['fallback_reviewer_id']=='ops-reviewer2'
    path='/api/financial-clarifications/'+row['id']+'/decision'
    post('reviewer2',path,{'expected_version':1,'action':'takeover','note':'Cannot take over an active reviewer'},status=409)
    with db.tx() as c:c.execute("UPDATE users SET active=false WHERE id='ops-accountant'")
    row=actor('reviewer2').get(url).json()['clarifications'][0]
    assert row['responsible_user_id']=='ops-reviewer2' and row['actions']==['takeover']
    row=post('reviewer2',path,{'expected_version':1,'action':'takeover','note':'Original reviewer unavailable; I accept follow-up responsibility'})['clarification']
    assert row['requested_by']=='ops-accountant' and row['current_reviewer_id']=='ops-reviewer2'
    assert row['history'][-1]['action']=='reviewer_taken_over'
    assert row['response_destination_id']=='ops-reviewer2'
