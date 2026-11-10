"""Real prior call sites keep collection authority and workflow ordering.

These source/PG witnesses supplement the separate immutable-image rollback
certificate; they never claim to certify an image or a production database.
"""
import importlib.util
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException
from app import db
from test_operations import ops
from test_order_interventions import (collection, controlled_order, open_operational_request,
    return_body, resubmit_body, decision_body)

ROOT=Path(__file__).resolve().parents[1]
OVERLAY=ROOT/'tools/rollback_application_overlay'


def prior_module(name):
    module_name='app._rollback_collected_test_'+name
    spec=importlib.util.spec_from_file_location(module_name,OVERLAY/'app'/f'{name}.py')
    module=importlib.util.module_from_spec(spec)
    sys.modules[module_name]=module
    spec.loader.exec_module(module)
    return module


def test_prior_collected_permission_is_explicit_and_revocable_not_role_default():
    permissions=prior_module('employee_permissions')
    code='orders_cancel_collected'
    assert code in permissions.effective({'role':'owner'})
    for role in ('admin','delivery_manager','branch_manager','driver'):
        assert code not in permissions.effective({'role':role})
        granted={'role':role,'extra_permissions':[code]}
        assert (code in permissions.effective(granted))==(role in ('admin','delivery_manager'))
        assert code not in permissions.effective({**granted,'permission_denials':[code]})


def test_prior_uses_the_same_reviewed_authority_and_event_projection():
    # The prior app remains distinct in its order/UI code. Shared security
    # modules must stay exact so the current behavioural witnesses bind them.
    for name in ('collected_order_authority.py','order_interventions.py'):
        assert (OVERLAY/'app'/name).read_bytes()==(ROOT/'app'/name).read_bytes()
    assert (OVERLAY/'app/orders.py').read_bytes()!=(ROOT/'app/orders.py').read_bytes()


@pytest.mark.postgres
def test_prior_preparation_cannot_cancel_refunded_receipts_or_replay_revoked_grant(ops):
    _,_,order,_=ops;oid=order();collection(oid,payment_status='refunded',refund=1000)
    prior=prior_module('orders')
    body=prior.PreparationBody(status='cancelled',version=1,reason='اختبار صلاحية الإلغاء بعد التراجع')
    with db.tx() as c:
        stale=c.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
        before=c.execute('SELECT * FROM collections WHERE order_id=%s',(oid,)).fetchall()
    with pytest.raises(HTTPException) as denied:
        prior.preparation(oid,body,stale,'rollback-denied-key')
    assert denied.value.status_code==403
    assert denied.value.detail['code']=='COLLECTED_CANCELLATION_AUTHORITY'
    with db.tx() as c:
        c.execute("UPDATE users SET extra_permissions='[\"orders_cancel_collected\"]' WHERE id='ops-manager'")
        granted=c.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
    result=prior.preparation(oid,body,granted,'rollback-granted-key')
    assert result['order']['preparation_status']=='cancelled'
    with db.tx() as c:
        assert c.execute('SELECT * FROM collections WHERE order_id=%s',(oid,)).fetchall()==before
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==1
        c.execute("UPDATE users SET extra_permissions='[]' WHERE id='ops-manager'")
    with pytest.raises(HTTPException) as revoked:
        prior.preparation(oid,body,granted,'rollback-granted-key')
    assert revoked.value.status_code==403


@pytest.mark.postgres
def test_prior_intervention_api_cannot_approve_while_returned_or_with_stale_workflow_revision(ops):
    actor,post,_,_=ops;oid=controlled_order();iid=open_operational_request(post,oid)
    path=f'/api/order-interventions/orders/{oid}/{iid}'
    post('owner',path+'/return',return_body())
    prior=prior_module('order_interventions')
    with db.tx() as c:
        owner=c.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone()
        original=c.execute('SELECT * FROM order_events WHERE id=%s',(iid,)).fetchone()
    for revision in (None,1):
        body=prior.Decision(**decision_body(reason='wrong_address',workflow_revision=revision))
        with pytest.raises(HTTPException) as waiting:
            prior.decide(oid,iid,body,owner,uuid4().hex)
        assert waiting.value.status_code==409
        assert waiting.value.detail['code'] in ('INTERVENTION_RETURNED','INTERVENTION_WORKFLOW_CONFLICT')
    post('manager',path+'/resubmit',resubmit_body())
    with pytest.raises(HTTPException) as stale:
        prior.decide(oid,iid,prior.Decision(**decision_body(reason='wrong_address')),owner,uuid4().hex)
    assert stale.value.detail['code']=='INTERVENTION_WORKFLOW_CONFLICT'
    with db.tx() as c:
        assert c.execute('SELECT * FROM order_events WHERE id=%s',(iid,)).fetchone()==original
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='ready'
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0


@pytest.mark.postgres
def test_prior_financial_cancel_preserves_receipts_and_requires_current_delegate(ops):
    _,_,order,_=ops;oid=order();collection(oid)
    prior=prior_module('order_financial_cases')
    body=prior.Cancellation(version=1,reason='إلغاء تشغيلي مشروع مع بقاء المحاسبة')
    with db.tx() as c:
        manager=c.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
        owner=c.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone()
    with pytest.raises(HTTPException) as denied:
        prior.cancel(oid,body,manager,'prior-manager-denied')
    assert denied.value.detail['code']=='COLLECTED_CANCELLATION_AUTHORITY'
    result=prior.cancel(oid,body,owner,'prior-owner-cancelled')
    assert result['financial_case']['status']=='pending' and result['payment_status']=='paid'
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
