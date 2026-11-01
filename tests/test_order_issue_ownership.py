"""Operational responsibility follows accepted duty handover, never a read grant."""
from uuid import uuid4
import pytest
from app import db
from app.order_issue_ownership import current_managers,with_current_manager
from test_order_interventions import controlled_order
from test_operations import ops

pytestmark=pytest.mark.postgres


def setup_problem(ops):
    actor,post,_,_=ops
    source,target,driver,task=[uuid4().hex for _ in range(4)]
    oid=controlled_order()
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('ops-next','Next manager','delivery_manager','ops-brand','[\"ops-branch\"]')")
        for sid,user,kind,parent in [(source,'ops-manager','manager',None),(target,'ops-next','manager',None),(driver,'ops-driver','driver',source)]:
            c.execute("INSERT INTO shifts(id,user_id,kind,brand_id,branch_ids,manager_shift_id) VALUES(%s,%s,%s,'ops-brand','[\"ops-branch\"]',%s)",(sid,user,kind,parent))
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,assigned_by) VALUES(%s,%s,'ops-driver',%s,'ops-manager')",(task,oid,driver))
    rid=post('driver',f'/api/order-interventions/orders/{oid}',{'version':1,'kind':'problem','reason_code':'no_answer','note':'العميل لا يرد'})['id']
    return actor,post,oid,rid,source,target,driver


def test_driver_problem_moves_to_recipient_only_after_accepted_manager_handover(ops):
    actor,post,oid,rid,source,target,driver=setup_problem(ops)
    with db.tx() as c:
        original=c.execute('SELECT * FROM order_events WHERE id=%s',(rid,)).fetchone()
    detail=lambda who: actor(who).get(f'/api/order-interventions/orders/{oid}').json()['requests'][0]['workflow']
    inbox=lambda who: actor(who).get('/api/order-interventions/pending-cancellations').json()['requests']
    assert detail('manager')['current_responsible_user_id']=='ops-manager'
    assert any(r['id']==rid for r in inbox('manager'))
    assert not any(r['id']==rid for r in inbox('next'))
    handover=post('manager',f'/api/shifts/{source}/handover-manager',{'target_manager_shift_id':target,'reason':'نقل متابعة البلاغ مع المناوبة'})['handover_id']
    # Requesting a handover alone cannot transfer responsibility.
    assert detail('manager')['current_responsible_user_id']=='ops-manager'
    post('next',f'/api/manager-handovers/{handover}/accept')
    flow=detail('next')
    assert flow['current_responsible_user_id']=='ops-next'
    assert flow['current_responsible_shift_id']==target
    assert any(r['id']==rid for r in inbox('next'))
    assert not any(r['id']==rid for r in inbox('manager'))
    with db.tx() as c:
        assert c.execute('SELECT * FROM order_events WHERE id=%s',(rid,)).fetchone()==original
        assert c.execute('SELECT manager_shift_id FROM shifts WHERE id=%s',(driver,)).fetchone()['manager_shift_id']==target


def test_revoked_current_manager_is_not_named_and_explicit_return_is_not_reassigned(ops):
    _,_,oid,_,source,_,_=setup_problem(ops)
    with db.tx() as c:
        assert current_managers(c,[oid])[oid]['manager_user_id']=='ops-manager'
        c.execute("UPDATE users SET branch_ids='[]' WHERE id='ops-manager'")
        assert current_managers(c,[oid])=={}
    flow={'status':'returned','current_responsible_role':'delivery_manager',
          'current_responsible_user_id':'explicit-manager','current_responsible_name':'Explicit manager'}
    assert with_current_manager(flow,{'manager_user_id':'other','manager_name':'Other','manager_shift_id':source})==flow


def test_returned_problem_successor_can_resubmit_after_accepted_handover(ops):
    actor,post,oid,rid,source,target,_=setup_problem(ops)
    base=f'/api/order-interventions/orders/{oid}/{rid}'
    post('owner',base+'/return',{'version':1,'workflow_revision':0,'note':'استكمل توضيح التواصل','corrected_reason_code':'no_answer','target_manager_id':'ops-manager'})
    handover=post('manager',f'/api/shifts/{source}/handover-manager',{'target_manager_shift_id':target,'reason':'تسليم البلاغ المعاد للمناوبة التالية'})['handover_id']
    post('next',f'/api/manager-handovers/{handover}/accept')
    detail=actor('next').get(f'/api/order-interventions/orders/{oid}').json()['requests'][0]
    assert detail['workflow']['current_responsible_user_id']=='ops-next'
    assert 'resubmit' in detail['workflow']['available_actions']
    old=actor('manager').get(f'/api/order-interventions/orders/{oid}').json()['requests'][0]
    assert 'resubmit' not in old['workflow']['available_actions']
    body={'version':1,'workflow_revision':1,'note':'تم الاتصال وتوثيق النتيجة','contact_result':'no_answer'}
    post('manager',base+'/resubmit',body,status=403)
    assert post('next',base+'/resubmit',body)['status']=='resubmitted'
    with db.tx() as c:
        original=c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='intervention.returned'",(oid,)).fetchone()['details']
        assert original['target_manager_id']=='ops-manager'


def test_current_driver_receives_operational_decision_without_private_evidence(ops):
    actor,post,oid,rid,_,_,_=setup_problem(ops)
    note='تواصل مع العميل ثم عد إلى الفرع إذا لم يرد'
    post('owner',f'/api/order-interventions/orders/{oid}/{rid}/decision',
         {'action':'resolve','reason_code':'no_answer','contact_result':'no_answer','version':1,'workflow_revision':0,'note':note})
    # An order-level management decision must reach its current executor too.
    other=post('owner',f'/api/order-interventions/orders/{oid}',
               {'version':1,'kind':'problem','reason_code':'wrong_address','note':'توجيه تشغيلي للسائق'})['id']
    post('owner',f'/api/order-interventions/orders/{oid}/{other}/decision',
         {'action':'resolve','reason_code':'wrong_address','contact_result':'agreed','version':1,'workflow_revision':0,'note':'تأكد من الدبوس مع العميل'})
    result=actor('driver').get(f'/api/order-interventions/orders/{oid}').json()
    rows={r['id']:r for r in result['requests']}
    assert rows[rid]['decision']['note']==note
    assert rows[rid]['workflow']['next_action_label']==note
    assert rows[other]['decision']['note']=='تأكد من الدبوس مع العميل'
    for row in rows.values():
        assert row['workflow']['available_actions']==[]
        assert not any(k in row['details'] for k in ('evidence_message_id','evidence_attachment_id','customer_request_id','order_total_halalas','proof'))
    assert result['attachments']==result['customer_messages']==result['customer_requests']==[]
