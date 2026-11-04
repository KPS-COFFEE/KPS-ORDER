"""Current-release customer chat evidence and explicit escalation journeys."""
import pytest
from app import db
from test_order_interventions import controlled_order, ops


@pytest.mark.postgres
def test_customer_chat_message_can_document_current_cancellation_without_duplicate_image(ops):
    actor,post,_,_=ops;oid=controlled_order(source='delivery_center')
    from app.order_interventions import customer_message
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        u=c.execute("SELECT * FROM users WHERE id='ops-customer'").fetchone()
        mid=customer_message(c,o,u,'cancel','أرجو إلغاء هذا الطلب')
    detail=actor('manager').get(f'/api/order-interventions/orders/{oid}').json()
    assert any(m['id']==mid for m in detail['customer_messages'])
    body={'kind':'cancel','reason_code':'customer_cancel','version':1,'evidence_message_id':mid}
    post('manager',f'/api/order-interventions/orders/{oid}',body,status=422)
    body['customer_message_confirmed']=True
    iid=post('manager',f'/api/order-interventions/orders/{oid}',body)['id']
    result=post('manager',f'/api/order-interventions/orders/{oid}/{iid}/decision',
        {'action':'approve','reason_code':'customer_cancel','contact_result':'agreed','version':1})
    assert result['status']=='approved'
    with db.tx() as c:
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='cancelled'
        proof=c.execute("SELECT details FROM order_events WHERE id=%s",(iid,)).fetchone()['details']
        assert proof['evidence_message_id']==mid and proof['customer_message_confirmed']
        assert not proof.get('evidence_attachment_id')


@pytest.mark.postgres
def test_customer_message_proof_obeys_order_and_conversation_access(ops):
    actor,post,_,_=ops;oid=controlled_order();other=controlled_order()
    from app.order_interventions import customer_message
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        u=c.execute("SELECT * FROM users WHERE id='ops-customer'").fetchone()
        mid=customer_message(c,o,u,'cancel','طلب صريح')
        c.execute("UPDATE conversations SET route='privacy',category='privacy' WHERE order_id=%s",(oid,))
    body={'kind':'cancel','reason_code':'customer_cancel','version':1,'evidence_message_id':mid,'customer_message_confirmed':True}
    assert not actor('manager').get(f'/api/order-interventions/orders/{oid}').json()['customer_messages']
    post('manager',f'/api/order-interventions/orders/{oid}',body,status=422)
    post('owner',f'/api/order-interventions/orders/{other}',body,status=422)
    assert any(m['id']==mid for m in actor('owner').get(f'/api/order-interventions/orders/{oid}').json()['customer_messages'])
    post('owner',f'/api/order-interventions/orders/{oid}',body)


@pytest.mark.postgres
def test_escalated_problem_reaches_owner_and_cannot_be_closed_by_ordinary_manager(ops):
    actor,post,_,_=ops;oid=controlled_order()
    iid=post('manager',f'/api/order-interventions/orders/{oid}',
        {'kind':'problem','reason_code':'wrong_address','escalate':True,'version':1})['id']
    detail=actor('manager').get(f'/api/order-interventions/orders/{oid}').json()
    assert not detail['requests'][0]['workflow']['available_actions']
    inbox=actor('owner').get('/api/order-interventions/pending-cancellations').json()['requests']
    assert any(r['id']==iid for r in inbox)
    body={'action':'resolve','reason_code':'wrong_address','contact_result':'agreed','version':1}
    post('manager',f'/api/order-interventions/orders/{oid}/{iid}/decision',body,status=403)
    assert post('owner',f'/api/order-interventions/orders/{oid}/{iid}/decision',body)['status']=='resolved'
