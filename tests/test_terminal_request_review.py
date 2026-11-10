"""A late customer request stays actionable without reopening a terminal sale."""
from uuid import uuid4
import pytest
from app import db,fiscal_issuance
from test_operations import ops

pytestmark=[pytest.mark.postgres,pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)]


def terminal_case(ops,*,cancelled=False):
    actor,post,order,_=ops;oid=order()
    rid=post('customer','/api/order-requests/orders/'+oid,{'kind':'cancel','note':'طلب عميل محفوظ قبل اكتمال التسليم'})['id']
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,extra_permissions) VALUES('ops-director','محمد عبدالسميع','admin','kps','[\"kps-branch-1\"]',%s)",(db.json(['orders_view','orders_cancel_after_print']),))
        owner=c.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone()
        c.execute("INSERT INTO fiscal_policy_decisions(id,brand_id,actor_id,decision) VALUES(%s,'kps','ops-owner',%s)",(uuid4().hex,db.json({'enabled':True,'phase':'unconfirmed','issuance_mode':'owner_requested'})))
        fiscal_issuance.issue_sale(c,oid,owner,required=True)
        c.execute("INSERT INTO collections(id,order_id,method,amount_halalas,collector_id) VALUES(%s,%s,'card',1000,'ops-driver')",(uuid4().hex,oid))
        c.execute("UPDATE orders SET preparation_status=%s,delivery_status=%s,payment_status='paid',completed_at=now() WHERE id=%s",('cancelled' if cancelled else 'completed','cancelled' if cancelled else 'delivered',oid))
    return oid,rid


def sale_records(oid):
    with db.tx() as c:
        return {'order':c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone(),
            'fiscal':c.execute('SELECT * FROM fiscal_documents WHERE order_id=%s ORDER BY number',(oid,)).fetchall(),
            'collections':c.execute('SELECT * FROM collections WHERE order_id=%s ORDER BY id',(oid,)).fetchall(),
            'refunds':c.execute('SELECT * FROM refunds WHERE order_id=%s ORDER BY id',(oid,)).fetchall(),
            'tasks':c.execute('SELECT * FROM driver_tasks WHERE order_id=%s ORDER BY id',(oid,)).fetchall(),
            'evidence':c.execute("SELECT e.details FROM order_events e WHERE e.order_id=%s AND e.event_type='customer_request.opened' ORDER BY e.id",(oid,)).fetchall()}


def recorded_events(oid):
    with db.tx() as c:
        return c.execute('SELECT * FROM order_events WHERE order_id=%s ORDER BY id',(oid,)).fetchall()


@pytest.mark.parametrize('who',['manager','director','owner'])
def test_delivered_pending_request_rejection_is_recorded_without_sale_or_refund_mutation(ops,who):
    actor,post,_,_=ops;oid,rid=terminal_case(ops);before=sale_records(oid);events_before=recorded_events(oid)
    detail=actor(who).get('/api/order-amendments/orders/'+oid)
    assert detail.status_code==200,detail.text
    request=next(row for row in detail.json()['requests'] if row['id']==rid)
    assert request['decision'] is None
    assert recorded_events(oid)==events_before
    stage=detail.json()['guidance']['stages'][0]
    assert stage['code']=='terminal_request_review' and stage['responsible_role']=='delivery_manager'
    assert stage['source_id']==rid and 'already cancelled' in stage['action']['en']
    assert 'cancellation_review' not in [row['code'] for row in detail.json()['guidance']['stages']]
    assert set(request['available_actions'])=={'rejected','refer_financial'}
    path=f'/api/order-requests/orders/{oid}/{rid}/decision'
    post(who,path,{'action':'resolved','note':'لا يجوز اعتبار التسليم إلغاء منفذا'},status=409)
    body={'action':'rejected','note':'تم التسليم والتحصيل، تمت مراجعة العميل وتوضيح مسار المعالجة المالية'}
    result=post(who,path,body,key='terminal-request-review')
    assert result==post(who,path,body,key='terminal-request-review')
    assert result['status']=='rejected' and result['note']==body['note']
    assert sale_records(oid)==before
    with db.tx() as c:
        decisions=c.execute("SELECT actor_id,details FROM order_events WHERE order_id=%s AND event_type='customer_request.decided'",(oid,)).fetchall()
        assert len(decisions)==1 and decisions[0]['actor_id']=='ops-'+who
        assert decisions[0]['details']['request_id']==rid
    if who=='director':
        denied=actor(who).get('/api/order-financial-cases/orders/'+oid+'/refund-context')
        assert denied.status_code==403,denied.text


@pytest.mark.parametrize('who',['manager','director'])
def test_cancelled_pending_request_can_be_closed_without_implying_a_refund(ops,who):
    _,post,_,_=ops;oid,rid=terminal_case(ops,cancelled=True);before=sale_records(oid)
    result=post(who,f'/api/order-requests/orders/{oid}/{rid}/decision',{'action':'resolved','note':'الطلب ملغى، ومراجعة المبالغ تبقى منفصلة لدى المحاسبة'})
    assert result['status']=='resolved'
    assert sale_records(oid)==before


def test_terminal_request_replay_rechecks_current_authority_and_scope(ops):
    actor,post,_,_=ops;oid,rid=terminal_case(ops)
    path=f'/api/order-requests/orders/{oid}/{rid}/decision';body={'action':'rejected','note':'مراجعة موثقة بعد التسليم'}
    post('other',path,body,status=404)
    post('driver',path,body,status=403)
    post('director',path,body,key='terminal-revoked-review')
    with db.tx() as c:c.execute("UPDATE users SET extra_permissions='[\"orders_view\"]' WHERE id='ops-director'")
    post('director',path,body,key='terminal-revoked-review',status=403)
    assert actor('customer').get('/api/order-amendments/orders/'+oid).json()['requests'][0]['decision']['status']=='rejected'


@pytest.mark.parametrize('who',['manager','director'])
def test_terminal_request_referral_appears_for_accounting_and_formal_review_posts_no_money(ops,who):
    actor,post,_,_=ops;oid,rid=terminal_case(ops);before=sale_records(oid)
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions) VALUES('ops-accountant','TEST ACCOUNTANT','admin','kps','[\"kps-branch-1\"]',%s)",(db.json(['accounting_reports','accounting_review']),))
    path=f'/api/order-requests/orders/{oid}/{rid}/decision'
    body={'action':'refer_financial','note':'طلب العميل بعد التسليم يحتاج مراجعة المحاسبة'}
    first=post(who,path,body,key='terminal-referral')
    assert first==post(who,path,body,key='terminal-referral')
    again=post(who,path,body,key='terminal-referral-new-key')
    case_id=first['referral']['case_id']
    assert first['status']=='referred' and again['referral']['case_id']==case_id
    assert first['referral']['no_money_posted'] and first['referral']['responsible_role']=='accounting'
    summary=actor(who).get('/api/order-amendments/orders/'+oid).json()
    request=next(row for row in summary['requests'] if row['id']==rid)
    assert request['decision'] is None and request['referral']['case_id']==case_id
    assert request['referral']['status']=='pending' and request['pending_role']=='accounting'
    assert request['available_actions']==[]
    stages=summary['guidance']['stages']
    assert [(stage['code'],stage['responsible_role']) for stage in stages]==[('customer_request_financial_followup','accounting'),('customer_request_accounting_review','accounting')]
    assert stages[0]['case_id']==case_id and stages[1]['source_id']==case_id
    assert 'no refund reference is required' in stages[1]['evidence']['en']
    assert all(stage['code'] not in ('cancellation_review','refund_execution','collection_recovery') for stage in stages)
    post(who,path,{'action':'rejected','note':'لا يغلق مدير التوصيل مراجعة المحاسبة'},status=409)
    inbox=actor('accountant').get('/api/order-financial-cases')
    assert inbox.status_code==200,inbox.text
    pending=next(row['financial_case'] for row in inbox.json()['cases'] if row['id']==oid)
    assert pending['id']==case_id and pending['kind']=='customer_request_review'
    assert pending['review_only'] and pending['pending_amount_halalas']==0
    context=actor('accountant').get('/api/order-financial-cases/orders/'+oid+'/refund-context')
    assert context.status_code==409 and context.json()['detail']['code']=='REQUEST_REVIEW_ONLY'
    for method in ('card','cash'):
        denied=post('accountant','/api/orders/'+oid+'/refund',{'method':method,'amount_halalas':100,'reason':'إحالة المراجعة لا تعتمد رد مبلغ'},status=409)
        assert denied['detail']['code']=='REQUEST_REVIEW_ONLY'
    resolve='/api/order-financial-cases/orders/'+oid+'/resolve'
    post('manager',resolve,{'reason':'لا توجد صلاحية مالية للمدير'},status=403)
    resolved=post('accountant',resolve,{'reason':'تمت المراجعة الرسمية، لا تسجل هذه المراجعة استردادا أو تحصيلا'},key='terminal-accounting-review')
    assert resolved['financial_case']['status']=='resolved'
    assert resolved==post('accountant',resolve,{'reason':'تمت المراجعة الرسمية، لا تسجل هذه المراجعة استردادا أو تحصيلا'},key='terminal-accounting-review')
    after=actor('owner').get('/api/order-amendments/orders/'+oid).json()['requests'][0]
    assert after['decision']['status']=='reviewed'
    assert after['decision']['financial_review_only'] and after['decision']['no_money_posted_by_review']
    assert sale_records(oid)==before
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='financial_case.opened'",(oid,)).fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='customer_request.referred'",(oid,)).fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='customer_request.decided'",(oid,)).fetchone()['n']==1
