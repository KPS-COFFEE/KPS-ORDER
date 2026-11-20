"""Staff tickets record requests and owner decisions without executing changes."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app import db
from app.employee_tickets import TicketBody,TicketReview
from test_accounting import accountant
from test_operations import ops


@pytest.mark.parametrize('overrides',[
    {'type':'grant_permission'},{'description':'x'*2001},{'issue':''},{'reason':''},{'unexpected':'payload'},
])
def test_ticket_input_is_bounded_and_does_not_accept_commands(overrides):
    with pytest.raises(ValidationError):
        TicketBody(**(dict(type='technical',issue='app_error',reason='not_working',description='TEST failure')|overrides))


@pytest.mark.parametrize('overrides',[
    {'expected_version':True},{'expected_version':1.5},{'expected_version':0},{'status':'deleted'},{'note':'  '},
])
def test_owner_review_requires_strict_version_and_documented_supported_state(overrides):
    with pytest.raises(ValidationError):
        TicketReview(**(dict(expected_version=1,status='in_progress',note='TEST investigating')|overrides))


def request_body(**changes):
    return dict(type='technical',issue='app_error',reason='not_working',description='TEST screen failed to load')|changes


@pytest.mark.postgres
@pytest.mark.parametrize('who',['owner','accountant','manager','delivery-staff','branch','driver'])
def test_every_staff_role_can_create_read_own_ticket_without_extra_permissions(ops,who):
    actor,post,_,_=ops
    accountant()
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('ops-delivery-staff','TEST STAFF','delivery_staff','ops-brand','[\"ops-branch\"]')")
    options=actor(who).get('/api/employee-tickets/options')
    assert options.status_code==200 and len(options.json()['types'])==7
    body=request_body()
    result=post(who,'/api/employee-tickets',body,key='create-staff-ticket')
    assert result['requested_by']=='ops-'+who and result['status']=='open' and result['version']==1
    assert result['reference'].startswith('ET-') and result['description']==body['description']
    assert result['type_label']=={'ar':'مشكلة تقنية','en':'Technical issue'} and result['issue_label']['en']=='Application error'
    assert post(who,'/api/employee-tickets',body,key='create-staff-ticket')==result
    assert actor(who).get('/api/employee-tickets').json()['tickets']==[result]
    detail=actor(who).get('/api/employee-tickets/'+result['id']).json()
    assert detail['ticket']==result and detail['history'][0]['version']==1
    assert detail['history'][0]['note']==body['description']
    with db.tx() as c:assert c.execute("SELECT count(*) n FROM audit_events WHERE action='employee.ticket.created'").fetchone()['n']==1


@pytest.mark.postgres
def test_ticket_visibility_is_own_only_except_owner_in_same_brand(ops):
    actor,post,_,_=ops
    first=post('driver','/api/employee-tickets',request_body())
    second=post('manager','/api/employee-tickets',request_body())
    for who in ('customer',):
        assert actor(who).get('/api/employee-tickets/options').status_code==403
        assert actor(who).get('/api/employee-tickets').status_code==403
        post(who,'/api/employee-tickets',request_body(),status=403)
    assert actor('driver2').get('/api/employee-tickets').json()['tickets']==[]
    assert actor('driver2').get('/api/employee-tickets/'+first['id']).status_code==404
    assert actor('other').get('/api/employee-tickets').json()['tickets']==[]
    assert actor('other').get('/api/employee-tickets/'+first['id']).status_code==404
    assert {t['id'] for t in actor('owner').get('/api/employee-tickets').json()['tickets']}=={first['id'],second['id']}
    review=dict(expected_version=1,status='in_progress',note='TEST owner investigation')
    post('driver','/api/employee-tickets/'+first['id']+'/review',review,status=403)
    post('other','/api/employee-tickets/'+first['id']+'/review',review,status=404)


@pytest.mark.postgres
def test_role_catalog_other_description_and_effective_accounting_profile(ops):
    actor,post,_,_=ops
    accountant()
    driver=actor('driver').get('/api/employee-tickets/options').json()
    accounting=actor('accountant').get('/api/employee-tickets/options').json()
    assert driver['role_profile']['code']=='driver' and accounting['role_profile']['code']=='accounting'
    technical=next(t for t in driver['types'] if t['code']=='technical')
    assert 'location_issue' in {i['code'] for i in technical['issues']}
    assert 'printer_issue' not in {i['code'] for i in technical['issues']}
    delivery=next(i for t in driver['types'] for i in t['issues'] if i['code']=='delivery_issue')
    assert 'customer_unreachable' in {r['code'] for r in delivery['reasons']}
    post('driver','/api/employee-tickets',request_body(issue='printer_issue'),status=422)
    post('driver','/api/employee-tickets',request_body(reason='invented_reason'),status=422)
    for changes in (dict(type='other',issue='other'),dict(issue='other'),dict(reason='other')):
        post('driver','/api/employee-tickets',request_body(**changes,description='  '),status=422)
        result=post('driver','/api/employee-tickets',request_body(**changes,description='TEST explain the other issue'))
        assert result['description']=='TEST explain the other issue'
    post('driver','/api/employee-tickets',request_body(type='service_request',issue='accounting_issue'),status=422)
    result=post('accountant','/api/employee-tickets',request_body(type='service_request',issue='accounting_issue'))
    assert result['issue']=='accounting_issue'


@pytest.mark.postgres
@pytest.mark.parametrize('kind,issue',[('permission_addition','missing_permission'),('permission_change','incorrect_permission'),('cash_correction','cash_receipt_error')])
def test_owner_review_preserves_request_and_cannot_grant_permissions_or_move_cash(ops,kind,issue):
    actor,post,_,_=ops
    body=request_body(type=kind,issue=issue,reason='work_required')
    with db.tx() as c:
        before_users=c.execute('SELECT * FROM users ORDER BY id').fetchall()
        before_cash=c.execute('SELECT * FROM collections ORDER BY id').fetchall()
        before_custody=c.execute('SELECT * FROM custody_events ORDER BY id').fetchall()
    ticket=post('driver','/api/employee-tickets',body)
    frozen=deepcopy(ticket)
    path='/api/employee-tickets/'+ticket['id']+'/review'
    review=dict(expected_version=1,status='in_progress',note='TEST owner reviewing request only')
    working=post('owner',path,review,key='owner-ticket-review')
    assert working['version']==2 and working['status']=='in_progress'
    assert post('owner',path,review,key='owner-ticket-review')==working
    post('owner',path,review,status=409)
    post('owner',path,{**review,'note':'TEST changed payload'},key='owner-ticket-review',status=409)
    followup=post('owner',path,dict(expected_version=2,status='in_progress',note='TEST investigation follow-up without changing status'))
    assert followup['version']==3
    done=post('owner',path,dict(expected_version=3,status='approved',note='TEST manually routed; no system grant or cash command'))
    assert done['status']=='approved' and done['version']==4
    detail=actor('driver').get('/api/employee-tickets/'+ticket['id']).json()
    assert [e['status'] for e in detail['history']]==['open','in_progress','in_progress','approved']
    assert [e['version'] for e in detail['history']]==[1,2,3,4]
    assert detail['ticket']['description']==frozen['description'] and detail['ticket']['requested_by']==frozen['requested_by']
    with db.tx() as c:
        assert c.execute('SELECT * FROM users ORDER BY id').fetchall()==before_users
        assert c.execute('SELECT * FROM collections ORDER BY id').fetchall()==before_cash
        assert c.execute('SELECT * FROM custody_events ORDER BY id').fetchall()==before_custody
        assert c.execute('SELECT count(*) n FROM driver_accounting_settlements').fetchone()['n']==0
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='employee.ticket.reviewed'").fetchone()['n']==3
        created=c.execute("SELECT details FROM audit_events WHERE action='employee.ticket.created' AND entity_id=%s",(ticket['id'],)).fetchone()['details']
        assert created['description']==body['description'] and created['version']==1 and created['status']=='open'
