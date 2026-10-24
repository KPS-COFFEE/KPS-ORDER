from datetime import date, datetime, timedelta, timezone
import pytest
from fastapi import FastAPI, Depends, Request
from fastapi.testclient import TestClient
from app import auth, db, employee_activity as a
from app.activity_context import ActivityContextMiddleware, associate, context


def test_calendar_bounds():
    lo,hi=a.period(date(2026,10,23),date(2026,10,23))
    assert lo.astimezone(timezone.utc).isoformat()=='2026-10-22T21:00:00+00:00'
    assert hi-lo==timedelta(days=1)
    with pytest.raises(Exception) as exc:a.period(date(2026,9,1),date(2026,10,23))
    assert exc.value.status_code==422


def test_no_sensitive_payloads_and_explicit_approval_semantics():
    clean=a.clean({'token':'secret','before':{'bank_halalas':2800,'password':'bad'},'after':{'bank_halalas':0},'action_type':'correction'})
    assert clean=={'before':{'bank_halalas':2800},'after':{'bank_halalas':0},'action_type':'correction'}
    assert a.title('accounting.balance_approved',clean)=='تعديل بيانات'
    assert a.title('accounting.balance_approved',{'action_type':'approval'})=='اعتماد دون تعديل'


def test_request_actor_is_thread_safe_and_does_not_leak():
    app=FastAPI();app.add_middleware(ActivityContextMiddleware)
    def verify(request:Request):associate(request,{'id':'employee','role':'admin','session_id':'opaque','brand_id':'kps'})
    @app.get('/one')
    def one(_=Depends(verify)):return [context('employee'),context('other')]
    @app.get('/two')
    def two():return context('employee')
    with TestClient(app) as c:
        assert c.get('/one').json()==[{'id':'employee','session_id':'opaque','brand_id':'kps'},{}]
        assert c.get('/two').json()=={}


@pytest.mark.parametrize('role',['admin','driver','customer','delivery_manager','delivery_staff','branch_manager'])
def test_owner_only_even_for_direct_pdf_urls(role):
    app=FastAPI();app.include_router(a.router)
    app.dependency_overrides[auth.current_user]=lambda:{'role':role}
    with TestClient(app) as c:
        for path in ['/employee-activity','/api/admin/employee-activity/employees','/api/admin/employee-activity?start=2026-10-23&end=2026-10-23&format=pdf']:
            assert c.get(path).status_code==403


def test_pdf_multipage():
    now=datetime.now(timezone.utc)
    e={'created_at':now,'display_name':'محاسب تجريبي','title':'تعديل بيانات','entity_type':'report','entity_id':'EXAMPLE-1042','detail_lines':['قبل / شبكة: 28.00 ر.س','بعد / شبكة: 0.00 ر.س']}
    data={'start':'2026-10-23','end':'2026-10-23','generated_at':now,'summary':{'logins':1,'views':2,'actions':1,'exports':0},'sessions':[],'events':[e]*60}
    content=a.pdf(data)
    assert content.startswith(b'%PDF-') and len(content)>15000
    assert b'/Count 1\n' not in content


@pytest.mark.postgres
def test_sessions_scope_events_and_pdf(client):
    from tests.test_core import _make_user,_authenticate
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name) VALUES('enbishan','Foreign test brand') ON CONFLICT(id) DO NOTHING")
    staff=_make_user('admin');owner=_make_user('owner');foreign=_make_user('admin',brand='enbishan')
    _authenticate(client,staff)
    assert client.get('/api/session').status_code==200
    assert client.post('/api/operations/activity-view',json={'page':'accounting'}).status_code==200
    with db.tx() as c:
        e=c.execute("SELECT session_id FROM audit_events WHERE actor_id=%s AND action='activity.view'",(staff['id'],)).fetchone()
        assert e['session_id']
    assert client.post('/api/operations/activity-view',json={'page':'accounting','actor_id':foreign['id']}).status_code==422
    _authenticate(client,owner)
    today=datetime.now(a.TZ).date().isoformat()
    query=f'/api/admin/employee-activity?start={today}&end={today}&employee_id={staff["id"]}'
    response=client.get(query);assert response.status_code==200,response.text
    assert response.json()['summary']=={'logins':1,'views':1,'actions':0,'exports':0}
    assert 'token_hash' not in response.text and 'csrf' not in response.text
    assert client.get(query.replace(staff['id'],foreign['id'])).status_code==404
    assert foreign['id'] not in client.get('/api/admin/employee-activity/employees').text
    pdf=client.get(query+'&format=pdf');assert pdf.status_code==200 and pdf.content.startswith(b'%PDF-')
