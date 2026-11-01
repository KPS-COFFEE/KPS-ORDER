"""Independent adversarial coverage; synthetic records, never production sessions."""
from datetime import datetime
from zoneinfo import ZoneInfo
from uuid import uuid4

import pytest
from app import auth, db
from test_operations import ops

pytestmark=pytest.mark.postgres
BASE='/api/owner-employee-activity'


def query(kind='login', **extra):
    today=datetime.now(ZoneInfo('Asia/Riyadh')).date().isoformat()
    return dict(kind=kind,start=today,end=today,**extra)


def owner(client):
    with db.tx() as c:
        sid='review-owner-session'
        token='review_owner_token_'+'x'*40
        csrf='review_owner_csrf_'+'x'*40
        c.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at,step_up_at) VALUES(%s,%s,%s,'ops-owner',now()+interval '1 hour',now()) ON CONFLICT(id) DO NOTHING",(sid,auth.token_hash(token),auth.token_hash(csrf)))
        u=dict(c.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone(),session_id=sid)
    client.app.dependency_overrides[auth.current_user]=lambda:u
    client.cookies.set(auth.COOKIE,token)
    client.cookies.set('kps_work_session',token)
    client.headers['x-csrf-token']=csrf
    return client


@pytest.mark.parametrize('who',['driver','manager','branch','customer'])
def test_reports_and_termination_are_owner_only_even_for_known_employee(ops,who):
    actor,_,_,_=ops
    client=actor(who)
    assert client.get(BASE,params=query()).status_code==403
    assert client.get(BASE+'/options').status_code==403
    assert client.post(BASE+'/ops-driver/end-sessions',json={'reason':'Synthetic security test'},headers={'Idempotency-Key':uuid4().hex}).status_code==403


def test_login_attribution_rejects_unknown_legacy_foreign_and_private_details(ops):
    actor,_,_,_=ops
    with db.tx() as c:
        for eid,brand in [('review-known','ops-brand'),('review-foreign','ops-other'),('review-legacy',None)]:
            details={'brand_id':brand,'access_token':'DO-NOT-EXPOSE-TOKEN','phone':'DO-NOT-EXPOSE-PHONE'} if brand else {}
            c.execute("INSERT INTO audit_events(id,actor_id,action,entity_type,entity_id,details) VALUES(%s,'ops-driver','session.created','user','ops-driver',%s)",(eid,db.json(details)))
    client=owner(actor('owner'))
    result=client.get(BASE,params=query())
    assert result.status_code==200,result.text
    assert 'review-known' in result.text
    assert 'review-foreign' not in result.text
    assert 'review-legacy' not in result.text
    assert 'DO-NOT-EXPOSE' not in result.text
    assert client.get(BASE,params=query(employee_id='ops-other')).status_code in (403,404)
    assert client.get(BASE,params=dict(kind='login',start='2026-01-01',end='2026-11-01')).status_code==422
    assert client.get(BASE,params=query(limit=101)).status_code==422
    assert client.get(BASE,params=dict(kind='login',start='9999-12-31',end='9999-12-31')).status_code==422


def test_work_events_are_joined_to_canonical_brand_without_raw_payload(ops):
    actor,_,order,_=ops
    oid=order(name='review-visible-order')
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) SELECT 'review-hidden-order','ops-other','ops-branch-other',customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer FROM orders WHERE id=%s",(oid,))
        for eid,entity in [('review-visible-work',oid),('review-hidden-work','review-hidden-order'),('review-orphan-work','not-an-order')]:
            c.execute("INSERT INTO audit_events(id,actor_id,action,entity_type,entity_id,details) VALUES(%s,'ops-driver','order.collect','order',%s,%s)",(eid,entity,db.json({'customer':{'phone':'PRIVATE-CUSTOMER'},'token':'PRIVATE-TOKEN','note':'PRIVATE-NOTE'})))
    result=owner(actor('owner')).get(BASE,params=query('work'))
    assert result.status_code==200,result.text
    assert 'review-visible-work' in result.text
    assert 'review-hidden-work' not in result.text
    assert 'review-hidden-order' not in result.text
    assert 'review-orphan-work' not in result.text
    assert 'PRIVATE-' not in result.text


def test_termination_revokes_old_sessions_preserves_employee_and_replays_only_original_result(ops):
    actor,_,_,_=ops
    client=owner(actor('owner'))
    with db.tx() as c:
        for sid in ('review-driver-1','review-driver-2'):
            c.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES(%s,%s,'review-secret','ops-driver',now()+interval '1 hour')",(sid,sid))
    with db.tx() as c:
        c.execute("INSERT INTO identity_tokens(id,brand_id,user_id,purpose,email,token_hash,expires_at) VALUES('review-pending-email','ops-brand','ops-driver','staff_login','review@fixture.invalid','review-pending-email',now()+interval '15 minutes')")
        c.execute("INSERT INTO otp_challenges(id,brand_id,phone,provider_reference,expires_at,source_user_id) VALUES('employee_otp_review_pending','ops-brand','+966500000009','review-provider',now()+interval '5 minutes','ops-driver')")
    path=BASE+'/ops-driver/end-sessions'
    headers={'Idempotency-Key':'review-end-sessions-key'}
    body={'reason':'Synthetic owner ends stale device sessions'}
    first=client.post(path,json=body,headers=headers)
    assert first.status_code==200,first.text
    recorded=client.get(BASE,params=query(employee_id='ops-driver'))
    assert recorded.status_code==200,recorded.text
    ended=[r for r in recorded.json()['rows'] if r['action']=='employee.sessions.ended']
    assert len(ended)==1 and ended[0]['employee_id']=='ops-driver'
    with db.tx() as c:
        assert c.execute("SELECT active FROM users WHERE id='ops-driver'").fetchone()['active'] is True
        assert c.execute("SELECT revoked_at FROM identity_tokens WHERE id='review-pending-email'").fetchone()['revoked_at'] is not None
        assert c.execute("SELECT expires_at<=now() expired FROM otp_challenges WHERE id='employee_otp_review_pending'").fetchone()['expired'] is True
        assert c.execute("SELECT count(*) n FROM sessions WHERE user_id='ops-driver' AND revoked_at IS NULL").fetchone()['n']==0
        c.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES('review-new-manual-login','review-new-manual-login','new-secret','ops-driver',now()+interval '1 hour')")
    replay=client.post(path,json=body,headers=headers)
    assert replay.status_code==200 and replay.json()==first.json(),replay.text
    with db.tx() as c:
        assert c.execute("SELECT revoked_at FROM sessions WHERE id='review-new-manual-login'").fetchone()['revoked_at'] is None
    changed=client.post(path,json={'reason':'Edited action'},headers=headers)
    assert changed.status_code==409
    for employee in ('ops-owner','ops-customer','ops-other'):
        denied=client.post(BASE+'/'+employee+'/end-sessions',json=body,headers={'Idempotency-Key':uuid4().hex})
        assert denied.status_code in (403,404,409),denied.text
    with db.tx() as c:
        c.execute("UPDATE sessions SET revoked_at=now() WHERE id='review-owner-session'")
    denied=client.post(path,json=body,headers=headers)
    assert denied.status_code in (401,403),denied.text


def test_termination_rechecks_csrf_even_when_upstream_principal_was_preloaded(ops):
    actor,_,_,_=ops
    client=owner(actor('owner'))
    with db.tx() as c:
        c.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES('review-csrf-driver','review-csrf-driver','secret','ops-driver',now()+interval '1 hour')")
    result=client.post(BASE+'/ops-driver/end-sessions',json={'reason':'Synthetic CSRF denial'},headers={'Idempotency-Key':uuid4().hex,'x-csrf-token':'wrong-csrf'})
    assert result.status_code==403,result.text
    with db.tx() as c:
        assert c.execute("SELECT revoked_at FROM sessions WHERE id='review-csrf-driver'").fetchone()['revoked_at'] is None


def test_stale_owner_principal_cannot_read_or_terminate_after_role_change(ops):
    actor,_,_,_=ops
    client=owner(actor('owner'))
    with db.tx() as c:
        c.execute("UPDATE users SET role='driver' WHERE id='ops-owner'")
    assert client.get(BASE,params=query()).status_code==403
    assert client.get(BASE+'/options').status_code==403
    result=client.post(BASE+'/ops-driver/end-sessions',json={'reason':'Synthetic stale owner denial'},headers={'Idempotency-Key':uuid4().hex})
    assert result.status_code==403,result.text


def test_report_uses_work_context_cookie_when_customer_context_coexists(ops,monkeypatch):
    actor,_,_,_=ops
    client=owner(actor('owner'))
    # Only policy is selected here; the real PostgreSQL session lookup is kept.
    # A customer cookie must never displace this owner's separate work cookie.
    monkeypatch.setattr(auth,'context_cookies_enabled',lambda *args:True)
    client.cookies.delete(auth.COOKIE)
    client.cookies.set('kps_customer_session','customer_token_'+'y'*40)
    result=client.get(BASE,params=query())
    assert result.status_code==200,result.text


def test_busy_old_login_token_returns_retry_without_partially_revoking_sessions(ops):
    actor,_,_,_=ops
    client=owner(actor('owner'))
    with db.tx() as c:
        c.execute("INSERT INTO sessions(id,token_hash,csrf_hash,user_id,expires_at) VALUES('review-busy-driver','review-busy-driver','secret','ops-driver',now()+interval '1 hour')")
        c.execute("INSERT INTO identity_tokens(id,brand_id,user_id,purpose,email,token_hash,expires_at) VALUES('review-busy-email','ops-brand','ops-driver','staff_login','busy@fixture.invalid','review-busy-email',now()+interval '15 minutes')")
    path=BASE+'/ops-driver/end-sessions'
    headers={'Idempotency-Key':'review-busy-login-key'}
    body={'reason':'Synthetic owner ends busy old login'}
    with db.tx() as held:
        held.execute("SELECT id FROM identity_tokens WHERE id='review-busy-email' FOR UPDATE").fetchone()
        denied=client.post(path,json=body,headers=headers)
        assert denied.status_code==409,denied.text
        assert held.execute("SELECT revoked_at FROM sessions WHERE id='review-busy-driver'").fetchone()['revoked_at'] is None
        assert held.execute("SELECT count(*) n FROM audit_events WHERE action='employee.sessions.ended'").fetchone()['n']==0
    retry=client.post(path,json=body,headers=headers)
    assert retry.status_code==200,retry.text
    with db.tx() as c:
        assert c.execute("SELECT revoked_at FROM sessions WHERE id='review-busy-driver'").fetchone()['revoked_at'] is not None
