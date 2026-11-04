"""Physical returns reduce claims, never invent cash or change the source invoice."""
from copy import deepcopy
from uuid import uuid4
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from app import db
from app.delivery_partial_returns import Line, Proposal, value
from test_operations import ops,close_driver


def sale():
    return dict(total_halalas=2500,delivery_fee_halalas=0,snapshot=dict(items=[
        dict(product_id='coffee',name='Coffee',quantity=2,unit_price_halalas=750,line_total_halalas=1500,units=[],note=''),
        dict(product_id='cake',name='Cake',quantity=1,unit_price_halalas=1000,line_total_halalas=1000,units=[],note='')]))


def test_partial_return_values_original_units_without_mutating_source():
    order=sale();before=deepcopy(order)
    assert value(order,[Line(line_index=0,quantity=1)],{})==750
    assert value(order,[Line(line_index=0,quantity=1)],{0:1})==750
    assert order==before
    for lines,previous in [([Line(line_index=0,quantity=1),Line(line_index=0,quantity=1)],{}),
        ([Line(line_index=0,quantity=2)],{0:1}),([Line(line_index=0,quantity=2),Line(line_index=1,quantity=1)],{})]:
        with pytest.raises(HTTPException):value(order,lines,previous)


def test_discount_allocation_and_fees_do_not_become_return_value():
    order=sale();order.update(total_halalas=2200,delivery_fee_halalas=200)
    assert value(order,[Line(line_index=1,quantity=1)],{})==800
    order['snapshot']['items'][0]['units']=[dict(price_halalas=500),dict(price_halalas=1000)]
    with pytest.raises(HTTPException):value(order,[Line(line_index=0,quantity=1)],{})
    with pytest.raises(ValidationError):Line(line_index=True,quantity=1)
    with pytest.raises(ValidationError):Proposal(version=1,lines=[dict(line_index=0,quantity=1)],cause='customer_reduction',reason='   ')


def setup(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    oid=order(total=2500,items=sale()['snapshot']['items'])
    with db.tx() as c:
        c.execute("UPDATE branches SET settings=jsonb_set(settings,'{minimum_order_halalas}','2000') WHERE id=(SELECT branch_id FROM orders WHERE id=%s)",(oid,))
    tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    post('driver',f'/api/tasks/{tid}/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver',f'/api/tasks/{tid}/transition',{'status':'picked_up'})
    return oid,tid,manager,driver


def request(ops,tid,**kw):
    actor,post,_,_=ops
    context=actor('driver').get('/api/delivery-partial-returns/tasks/'+tid).json()
    return post('driver','/api/delivery-partial-returns/tasks/'+tid,{**dict(version=context['version'],lines=[dict(line_index=1,quantity=1)],cause='customer_reduction',reason='Customer requests smaller order at arrival'),**kw})


@pytest.mark.postgres
@pytest.mark.parametrize('ops',[None,'fiscal-kps'],indirect=True)
def test_manager_approved_below_minimum_return_preserves_sale_and_actual_cash(ops):
    actor,post,_,_=ops
    with db.tx() as c:fiscal=c.execute("SELECT brand_id FROM users WHERE id='ops-owner'").fetchone()['brand_id']=='kps'
    if fiscal:post('owner','/api/fiscal/policy',dict(enabled=True,phase='unconfirmed',issuance_mode='owner_requested',reason='Synthetic invoice at print'))
    oid,tid,manager,driver=setup(ops)
    if fiscal:post('owner',f'/api/fiscal/orders/{oid}/print')
    with db.tx() as c:invoice=c.execute("SELECT document FROM fiscal_documents WHERE order_id=%s AND kind='invoice'",(oid,)).fetchone()
    with db.tx() as c:before=c.execute('SELECT snapshot,total_halalas FROM orders WHERE id=%s',(oid,)).fetchone()
    pending=request(ops,tid);path='/api/delivery-partial-returns/'+pending['id']+'/decision'
    post('driver',path,dict(action='approve',reason='Driver cannot approve',physical_resolution='with_driver_return_to_branch'),status=403)
    post('manager',f'/api/tasks/{tid}/transition',dict(status='picked_up',reason='Attempt bypass'),status=409)
    post('manager',path,dict(action='approve',reason='Confirmed partial physical receipt',physical_resolution='with_driver_return_to_branch'),status=422)
    body=dict(action='approve',reason='Approved customer reduction below minimum',approve_below_minimum=True,physical_resolution='with_driver_return_to_branch')
    key=uuid4().hex;result=post('manager',path,body,key=key)
    assert post('manager',path,body,key=key)==result
    assert result['credit_halalas']==1000 and result['corrected_total_halalas']==1500 and result['below_minimum']
    post('manager',path,body,status=409)
    summary=actor('driver').get(f'/api/order-amendments/orders/{oid}').json()
    assert summary['original_total_halalas']==2500 and summary['effective_claim_halalas']==1500
    assert summary['due_halalas']==1500 and summary['refund_due_halalas']==0
    task=next(t for t in actor('driver').get('/api/tasks').json()['tasks'] if t['id']==tid)
    assert task['original_total_halalas']==2500 and task['effective_claim_halalas']==1500
    collected=post('driver',f'/api/orders/{oid}/collect',dict(method='cash',amount_halalas=1500,tendered_halalas=1500))
    assert collected['remaining_halalas']==0 and collected['payment_status']=='paid'
    summary=actor('driver').get(f'/api/order-amendments/orders/{oid}').json()
    assert summary['actual_collected_halalas']==1500 and summary['due_halalas']==summary['refund_due_halalas']==0
    post('driver',f'/api/tasks/{tid}/transition',dict(status='delivered',proof='Actual remaining items delivered'))
    with db.tx() as c:
        assert c.execute('SELECT snapshot,total_halalas FROM orders WHERE id=%s',(oid,)).fetchone()==before
        assert c.execute("SELECT document FROM fiscal_documents WHERE order_id=%s AND kind='invoice'",(oid,)).fetchone()==invoice
        if fiscal:
            credit=c.execute("SELECT document FROM fiscal_documents WHERE order_id=%s AND kind='credit_note'",(oid,)).fetchone()['document']
            assert credit['total_halalas']==1000 and credit['no_money_posted']
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
        assert c.execute('SELECT sum(amount_halalas) n FROM custody_events WHERE shift_id=%s',(driver,)).fetchone()['n']==1500
        from app.order_financial_treatments import effective_claim
        assert effective_claim(c,dict(id=oid,total_halalas=2500))==1500
        from app.order_financial_cases import guard_financial_closure
        guard_financial_closure(c,shift_id=driver)
        from app.refund_line_values import prior_quantities
        assert prior_quantities(c,oid,{'cake':1})=={'cake':1}
    close_driver(post,driver,manager,counted=1500)
    with db.tx() as c:
        closed=c.execute('SELECT submission FROM shifts WHERE id=%s',(driver,)).fetchone()['submission']
        assert closed['expected_halalas']==1500 and closed['counted_halalas']==1500
        assert closed['difference_halalas']==0 and not closed['financial_review_required']


@pytest.mark.postgres
def test_branch_error_is_independent_and_reject_does_not_change_claim(ops):
    actor,post,_,_=ops;oid,tid,_,_=setup(ops)
    pending=request(ops,tid,cause='branch_error')
    path='/api/delivery-partial-returns/'+pending['id']+'/decision'
    post('manager',path,dict(action='reject',reason='Verified all original items accepted'))
    with db.tx() as c:
        from app.order_financial_treatments import effective_claim
        assert effective_claim(c,dict(id=oid,total_halalas=2500))==2500
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='branch.error_review_required'",(oid,)).fetchone()['n']==0
    pending=request(ops,tid,cause='branch_error')
    post('manager','/api/delivery-partial-returns/'+pending['id']+'/decision',dict(action='approve',reason='Branch mistake cannot bypass minimum approval',physical_resolution='received_by_branch'),status=422)
    post('manager','/api/delivery-partial-returns/'+pending['id']+'/decision',dict(action='approve',reason='Wrong item confirmed independently from driver cash',approve_below_minimum=True,physical_resolution='received_by_branch'))
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='branch.error_review_required'",(oid,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM custody_events WHERE reference_id=%s',(pending['id'],)).fetchone()['n']==0


@pytest.mark.postgres
def test_return_after_actual_partial_collection_does_not_invent_driver_debt(ops):
    actor,post,_,_=ops;oid,tid,manager,driver=setup(ops)
    post('driver',f'/api/orders/{oid}/collect',dict(method='cash',amount_halalas=1500,tendered_halalas=1500,shortfall_reason='Customer partially received order'))
    pending=request(ops,tid)
    result=post('manager','/api/delivery-partial-returns/'+pending['id']+'/decision',dict(action='approve',reason='Physical partial return confirmed; customer paid accepted items',approve_below_minimum=True,physical_resolution='with_driver_return_to_branch'))
    assert result['corrected_total_halalas']==1500
    with db.tx() as c:
        from app.order_financial_cases import case,guard_financial_closure
        state=case(c,oid)
        assert state['actual_collected_halalas']==1500 and state['pending_amount_halalas']==0 and state['financially_complete']
        guard_financial_closure(c,shift_id=driver)
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==1
        assert c.execute('SELECT sum(amount_halalas) n FROM custody_events WHERE shift_id=%s',(driver,)).fetchone()['n']==1500


def test_physical_credit_does_not_discount_the_remaining_items_again(monkeypatch):
    from unittest.mock import Mock
    from app import delivery_partial_returns,order_financial_treatments
    from app.refund_line_values import ceiling
    order={**sale(),'id':'original'}
    monkeypatch.setattr(delivery_partial_returns,'approved',lambda *args:[dict(credit_halalas=1000)])
    monkeypatch.setattr(order_financial_treatments,'effective_total',lambda *args:1500)
    c=Mock();c.execute.return_value.fetchone.return_value={'n':0}
    valued=order_financial_treatments.valuation_order(c,order)
    assert valued['total_halalas']==2500
    assert ceiling(valued,{'coffee':2},{'cake':1})==1500
    with pytest.raises(HTTPException):ceiling(valued,{'cake':1},{'cake':1})


def test_gift_remaining_cannot_disguise_a_full_paid_item_return():
    order=sale()
    order['snapshot']['items'].append(dict(product_id='gift',quantity=1,line_total_halalas=0,is_gift=True))
    with pytest.raises(HTTPException) as exc:
        value(order,[Line(line_index=0,quantity=2),Line(line_index=1,quantity=1)],{})
    assert exc.value.detail['code']=='PARTIAL_RETURN_FULL'
    with pytest.raises(HTTPException):value(order,[Line(line_index=1,quantity=1)],{0:2})


def test_minimum_policy_uses_saved_quote_or_exact_selected_morning_rule():
    from unittest.mock import Mock
    from app.delivery_partial_returns import minimum_policy
    c=Mock();order=dict(brand_id='kps',branch_id='execution',snapshot=dict(minimum_order_halalas=2000))
    assert minimum_policy(c,order)==2000
    c.execute.assert_not_called()
    order['snapshot']=dict(internal_routing=dict(selected_branch=dict(branch_id='selected')))
    c.execute.return_value.fetchall.return_value=[dict(settings=dict(minimum_order_halalas=1000)),dict(settings=dict(minimum_order_halalas=2000))]
    assert minimum_policy(c,order)==2000
    assert c.execute.call_args.args[1]==('kps',['selected','execution'])
    order['snapshot']['delivery_period']='morning'
    c.execute.return_value.fetchall.return_value=[dict(settings=dict(minimum_order_halalas=1000))]
    assert minimum_policy(c,order)==1000
    assert c.execute.call_args.args[1]==('kps',['selected'])


@pytest.mark.postgres
def test_simultaneous_partial_return_decisions_post_one_credit_and_block_double_quantity(ops):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    actor,post,_,_=ops;oid,tid,_,_=setup(ops)
    pending=request(ops,tid)
    path='/api/delivery-partial-returns/'+pending['id']+'/decision'
    body=dict(action='approve',reason='Concurrent manager approval of documented physical return',approve_below_minimum=True,physical_resolution='with_driver_return_to_branch')
    client=actor('manager');barrier=Barrier(2)
    def run():
        barrier.wait(timeout=10)
        return client.post(path,json=body,headers={'Idempotency-Key':uuid4().hex})
    with ThreadPoolExecutor(max_workers=2) as pool:responses=list(pool.map(lambda _:run(),range(2)))
    assert sorted(r.status_code for r in responses)==[200,409],[r.text for r in responses]
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM audit_events WHERE entity_id=%s AND action='accounting.order_financial_treatment.approved'",(oid,)).fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='delivery.partial_return.approved'",(oid,)).fetchone()['n']==1
    context=actor('driver').get('/api/delivery-partial-returns/tasks/'+tid).json()
    post('driver','/api/delivery-partial-returns/tasks/'+tid,dict(version=context['version'],lines=[dict(line_index=1,quantity=1)],cause='customer_reduction',reason='Attempt to return already returned item'),status=422)


@pytest.mark.parametrize('net,claim,due,refund',[(0,1500,1500,0),(1500,1500,0,0),(750,750,0,0),(1600,1500,0,100)])
def test_collection_summary_uses_effective_claim_and_actual_net(monkeypatch,net,claim,due,refund):
    from unittest.mock import Mock
    from app import order_amendments,order_financial_treatments,order_requests
    monkeypatch.setattr(order_amendments,'balance',lambda *args:net)
    monkeypatch.setattr(order_financial_treatments,'effective_claim',lambda *args:claim)
    monkeypatch.setattr(order_financial_treatments,'effective_total',lambda *args:1500)
    monkeypatch.setattr(order_requests,'rows',lambda *args:[])
    c=Mock();c.execute.return_value.fetchall.return_value=[]
    result=order_amendments.summary(c,dict(id='order',version=1,total_halalas=2500,service='delivery',snapshot={}),dict(role='driver'))
    assert result['original_total_halalas']==2500 and result['effective_total_halalas']==1500
    assert result['actual_collected_halalas']==net and result['effective_claim_halalas']==claim
    assert result['due_halalas']==due and result['refund_due_halalas']==refund


@pytest.mark.postgres
def test_actual_refund_after_partial_return_does_not_recreate_due_amount(ops):
    actor,post,_,_=ops;oid,tid,_,_=setup(ops)
    pending=request(ops,tid)
    post('manager','/api/delivery-partial-returns/'+pending['id']+'/decision',dict(action='approve',reason='Partial return confirmed with accepted paid items remaining',approve_below_minimum=True,physical_resolution='received_by_branch'))
    post('driver',f'/api/orders/{oid}/collect',dict(method='cash',amount_halalas=1500,tendered_halalas=1500))
    post('driver',f'/api/tasks/{tid}/transition',dict(status='delivered',proof='Actual accepted items delivered'))
    post('owner',f'/api/orders/{oid}/refund',dict(method='cash',amount_halalas=750,line_quantities={'coffee':1},reason='Actual returned one remaining coffee and cash paid back'))
    response=actor('owner').get(f'/api/order-amendments/orders/{oid}')
    assert response.status_code==200,response.text
    summary=response.json()
    assert summary['original_total_halalas']==2500 and summary['effective_total_halalas']==1500
    assert summary['effective_claim_halalas']==750 and summary['actual_collected_halalas']==750
    assert summary['due_halalas']==summary['refund_due_halalas']==0


def test_task_claim_projection_batches_source_credits_and_returns():
    from unittest.mock import Mock
    from app.order_financial_treatments import claim_projections
    c=Mock()
    c.execute.return_value.fetchall.side_effect=[
        [dict(order_id='approved',details=dict(kind='price_correction',credit_halalas=1000,original_total_halalas=2500))],
        [dict(order_id='approved',amount_halalas=750)]]
    result=claim_projections(c,[dict(id='approved',total_halalas=2500),dict(id='original',total_halalas=3000)])
    assert result['approved']==dict(original_total_halalas=2500,effective_total_halalas=1500,effective_claim_halalas=750)
    assert result['original']==dict(original_total_halalas=3000,effective_total_halalas=3000,effective_claim_halalas=3000)
    assert c.execute.call_count==2


@pytest.mark.postgres
def test_pending_partial_return_must_be_rejected_before_canonical_cancellation(ops):
    actor,post,_,_=ops;oid,tid,_,_=setup(ops)
    pending=request(ops,tid)
    with db.tx() as c:
        before=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        task_before=c.execute('SELECT * FROM driver_tasks WHERE id=%s',(tid,)).fetchone()
    path=f'/api/order-financial-cases/orders/{oid}/cancel'
    payload=dict(version=before['version'],reason='Customer wants full cancellation instead of partial receipt',physical_resolution='returned_to_branch')
    response=post('manager',path,payload,status=409)
    assert 'PARTIAL_RETURN_PENDING' in str(response)
    task_cancel=post('manager',f'/api/tasks/{tid}/transition',dict(status='cancelled',reason='Attempt cancellation with partial return pending'),status=409)
    assert 'PARTIAL_RETURN_PENDING' in str(task_cancel)
    handover=post('manager',f'/api/tasks/{tid}/handover',dict(driver_shift_id=task_before['shift_id'],reason='Pending return cannot move to another task'),status=409)
    assert 'PARTIAL_RETURN_PENDING' in str(handover)
    refund=post('owner',f'/api/orders/{oid}/refund',dict(method='cash',amount_halalas=100,line_quantities={},reason='Attempt refund with partial return pending'),status=409)
    assert 'PARTIAL_RETURN_PENDING' in str(refund)
    with db.tx() as c:
        assert c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()==before
        assert c.execute('SELECT * FROM driver_tasks WHERE id=%s',(tid,)).fetchone()==task_before
        assert not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='delivery.partial_return.approved'",(oid,)).fetchone()
    post('manager','/api/delivery-partial-returns/'+pending['id']+'/decision',dict(action='reject',reason='Partial request rejected because customer wants full cancellation'))
    current=actor('manager').get(f'/api/order-amendments/orders/{oid}').json()
    cancelled=post('manager',path,{**payload,'version':current['version']})
    assert cancelled['operational_status']=='cancelled'
    with db.tx() as c:
        assert not c.execute("SELECT 1 FROM audit_events WHERE entity_id=%s AND action='accounting.order_financial_treatment.approved'",(oid,)).fetchone()
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
