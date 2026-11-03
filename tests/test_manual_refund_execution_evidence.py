"""Manual refund execution proof is enforced by the server, not the checkbox."""
from uuid import uuid4
import pytest
from psycopg.errors import RaiseException
from app import db
from test_operations import ops


@pytest.mark.postgres
def test_manual_refund_missing_false_or_blank_proof_never_posts_money(ops):
    actor,post,order,_=ops
    oid=order('pickup')
    post('branch',f'/api/orders/{oid}/collect',dict(method='card',amount_halalas=1000,reference='SALE-SLIP'))
    body=dict(method='card',amount_halalas=500,reason='Customer return')
    variants=[{},dict(evidence_reference='REFUND-SLIP'),
        dict(evidence_reference='REFUND-SLIP',executed_confirmed=False),
        dict(evidence_reference='  ',executed_confirmed=True),
        dict(evidence_reference='REFUND-SLIP',executed_confirmed='true')]
    for proof in variants:
        response=actor('owner').post(f'/api/orders/{oid}/refund',json={**body,**proof},headers={'Idempotency-Key':uuid4().hex})
        assert response.status_code==422,response.text
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s',(oid,)).fetchone()['payment_status']=='paid'
        assert c.execute("SELECT count(*) n FROM custody_events WHERE kind='refund'").fetchone()['n']==0


@pytest.mark.postgres
def test_execution_reference_is_immutable_replayable_and_visible_in_order_journey(ops):
    actor,post,order,_=ops
    oid=order('pickup')
    post('branch',f'/api/orders/{oid}/collect',dict(method='card',amount_halalas=1000,reference='SALE-SLIP'))
    body=dict(method='card',amount_halalas=400,reason='Actual partial return',
        evidence_reference=' TERMINAL-REFUND-431 ',executed_confirmed=True)
    key='refund-execution-replay'
    first=actor('owner').post(f'/api/orders/{oid}/refund',json=body,headers={'Idempotency-Key':key})
    assert first.status_code==200,first.text
    repeated=actor('owner').post(f'/api/orders/{oid}/refund',json=body,headers={'Idempotency-Key':key})
    assert repeated.json()==first.json()
    with db.tx() as c:
        rows=c.execute('SELECT * FROM refunds WHERE order_id=%s',(oid,)).fetchall()
        assert len(rows)==1 and rows[0]['amount_halalas']==400
        proof=rows[0]['execution_evidence']
        assert proof==dict(reference='TERMINAL-REFUND-431',executed_confirmed=True,
            recorded_by='ops-owner',evidence_kind='manual_execution_attestation')
    journey=actor('owner').get(f'/api/order-financial-cases/orders/{oid}/journey')
    assert journey.status_code==200,journey.text
    assert journey.json()['refunds'][0]['execution_evidence']==proof
    with pytest.raises(RaiseException,match="Immutable financial record"),db.tx() as c:
        c.execute("UPDATE refunds SET execution_evidence='{}' WHERE id=%s",(rows[0]['id'],))


@pytest.mark.postgres
def test_legacy_refund_stays_unmodified_and_is_not_given_invented_proof(ops):
    actor,post,order,_=ops
    oid=order('pickup')
    payment=post('branch',f'/api/orders/{oid}/collect',dict(method='card',amount_halalas=1000,reference='SALE-SLIP'))
    with db.tx() as c:
        c.execute("INSERT INTO refunds(id,collection_id,order_id,method,amount_halalas,reason,authorized_by) VALUES('legacy-refund',%s,%s,'card',100,'Historical refund','ops-owner')",(payment['collection_id'],oid))
        before=c.execute("SELECT row_to_json(r) value FROM refunds r WHERE id='legacy-refund'").fetchone()['value']
    assert before['execution_evidence'] is None
    post('owner',f'/api/orders/{oid}/refund',dict(method='card',amount_halalas=100,reason='New actual payout',evidence_reference='NEW-SLIP',executed_confirmed=True))
    with db.tx() as c:
        assert c.execute("SELECT row_to_json(r) value FROM refunds r WHERE id='legacy-refund'").fetchone()['value']==before
