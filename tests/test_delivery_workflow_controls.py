"""Actual tender evidence, exception routing and operational/accounting separation."""
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import db
from app.order_financial_cases import guard_financial_closure
from test_operations import ops, close_driver
from test_accounting import accountant

pytestmark = pytest.mark.postgres


def picked_up(ops, total=2500):
    actor, post, order, shifts = ops
    manager, driver = shifts()
    oid = order(total=total, items=[
        {'product_id':'coffee','quantity':1,'unit_price_halalas':total,'line_total_halalas':total,'name':'Coffee'},
        {'product_id':'cookie','quantity':1,'unit_price_halalas':0,'line_total_halalas':0,'name':'Free cookie'}])
    tid = post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    post('driver',f'/api/tasks/{tid}/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver',f'/api/tasks/{tid}/transition',{'status':'picked_up'})
    return oid, tid, manager, driver


def test_25_invoice_1950_actual_preserves_gift_and_routes_accounting(ops):
    actor, post, _, _ = ops
    accountant()
    oid, tid, manager, driver = picked_up(ops)
    req=post('customer',f'/api/order-requests/orders/{oid}',{'kind':'cancel','note':'Please cancel'})
    payload={'method':'cash','amount_halalas':1950,'tendered_halalas':1950,'shortfall_reason':'Free cookie returned; amount deducted by mistake'}
    key=uuid4().hex
    first=post('driver',f'/api/orders/{oid}/collect',payload,key=key)
    assert post('driver',f'/api/orders/{oid}/collect',payload,key=key)==first
    assert first['payment_status']=='unpaid' and first['remaining_halalas']==550
    post('owner',f'/api/order-requests/orders/{oid}/{req["id"]}/decision',{'action':'rejected','note':'Customer kept remaining order; free cookie returned'})
    post('driver',f'/api/tasks/{tid}/transition',{'status':'delivered','proof':'Actual delivery'})
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert o['total_halalas']==2500 and o['payment_status']=='unpaid' and o['delivery_status']=='delivered'
        assert o['snapshot']['items'][1]['line_total_halalas']==0
        assert c.execute('SELECT sum(amount_halalas) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==1950
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
    path=f'/api/order-financial-cases/orders/{oid}'
    pending=actor('manager').get(path+'/journey').json()['financial_case']
    assert pending['kind']=='collection_shortfall' and pending['pending_amount_halalas']==550
    assert not pending['operationally_blocking']
    post('manager',path+'/resolve',{'reason':'Cannot resolve money'},status=403)
    post('accountant',path+'/resolve',{'reason':'A note is not payment'},status=409)
    assert actor('accountant').get(path+'/refund-context').status_code==409
    report=close_driver(post,driver,manager,counted=1950)
    with pytest.raises(HTTPException) as exc:
        with db.tx() as c:guard_financial_closure(c,shift_id=driver)
    assert exc.value.detail['code']=='COLLECTION_CASE_PENDING'
    post('accountant','/api/accounting/reviews/'+report['report_id'],{'note':'Cannot finalize pending collection'},status=409)
    context=actor('accountant').get(path+'/recovery-context').json()
    recovery={'version':context['version'],'method':'cash','amount_halalas':550,'shift_id':manager,'reference':'Verified cash receipt 001','reason':'Customer paid remaining balance'}
    post('manager',path+'/recover',recovery,status=403)
    post('other',path+'/recover',recovery,status=404)
    key=uuid4().hex
    recovered=post('accountant',path+'/recover',recovery,key=key)
    assert post('accountant',path+'/recover',recovery,key=key)==recovered
    assert recovered['financial_case']['pending_amount_halalas']==0
    post('accountant',path+'/resolve',{'reason':'Receipt verified; recovered full balance'})
    with db.tx() as c:
        guard_financial_closure(c,shift_id=driver)
        assert c.execute('SELECT submission FROM shifts WHERE id=%s',(driver,)).fetchone()['submission']['counted_halalas']==1950
        assert c.execute('SELECT total_halalas,payment_status FROM orders WHERE id=%s',(oid,)).fetchone()=={'total_halalas':2500,'payment_status':'paid'}


@pytest.mark.parametrize('cash,cards',[(0,[1000,1500]),(500,[700,1300])])
def test_multiple_card_transactions_are_separate_and_cash_only_enters_custody(ops,cash,cards):
    _,post,_,_=ops
    oid,_,_,driver=picked_up(ops)
    body={'method':'split' if cash else 'card','amount_halalas':2500,'card_payments':[{'amount_halalas':n,'reference':'receipt-'+str(i)} for i,n in enumerate(cards)]}
    if cash:body.update(cash_halalas=cash,card_halalas=sum(cards),tendered_halalas=cash)
    result=post('driver',f'/api/orders/{oid}/collect',body)
    assert len(result['collection_ids'])==len(cards)+bool(cash)
    with db.tx() as c:
        payments=c.execute("SELECT amount_halalas,reference FROM collections WHERE order_id=%s AND method='card' ORDER BY reference",(oid,)).fetchall()
        assert [p['amount_halalas'] for p in payments]==cards
        assert c.execute('SELECT coalesce(sum(amount_halalas),0) n FROM custody_events WHERE shift_id=%s',(driver,)).fetchone()['n']==cash


@pytest.mark.parametrize('patch',[
    {'amount_halalas':1950}, {'amount_halalas':2600,'shortfall_reason':'too much'},
    {'card_payments':[{'amount_halalas':2499}]}, {'card_payments':[]},
    {'card_payments':[{'amount_halalas':2500.5}]}, {'shortfall_reason':'no shortfall'},
])
def test_invalid_tender_is_atomic(ops,patch):
    _,post,_,_=ops
    oid,_,_,_=picked_up(ops)
    post('driver',f'/api/orders/{oid}/collect',{'method':'card','amount_halalas':2500,**patch},status=422)
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==0
        assert not c.execute("SELECT 1 FROM order_events WHERE order_id=%s AND event_type='financial_case.opened'",(oid,)).fetchone()


def test_manager_cannot_overwrite_submitted_driver_amounts(ops):
    _,post,_,shifts=ops
    _,driver=shifts()
    post('manager',f'/api/shifts/{driver}/review-amounts',{'counted_halalas':0,'terminal_total_halalas':0,'reason':'Attempt overwrite'},status=403)


def test_reassignment_before_pickup_is_immediate_and_after_pickup_needs_acceptance(ops):
    _,post,order,shifts=ops
    manager,driver=shifts()
    second=post('driver2','/api/shifts',{'kind':'driver','branch_ids':['ops-branch'],'manager_shift_id':manager})['shift']['id']
    oid=order();tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    result=post('manager',f'/api/tasks/{tid}/handover',{'driver_shift_id':second,'reason':'Available replacement','reassign_before_pickup':True})
    assert result['acceptance_required'] is False
    new=result['task_id']
    post('driver',f'/api/tasks/{tid}/transition',{'status':'accepted'},status=409)
    post('driver2',f'/api/tasks/{new}/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver2',f'/api/tasks/{new}/transition',{'status':'picked_up'})
    result=post('manager',f'/api/tasks/{new}/handover',{'driver_shift_id':driver,'reason':'Physical handover to replacement'})
    assert result['acceptance_required'] is True
    with db.tx() as c:
        assert c.execute('SELECT driver_id,status FROM driver_tasks WHERE id=%s',(new,)).fetchone()=={'driver_id':'ops-driver2','status':'picked_up'}
    post('driver',f'/api/tasks/{new}/accept-handover')


def test_free_item_return_has_zero_financial_effect_and_cannot_be_refunded(ops):
    actor,post,_,_=ops
    oid,tid,_,_=picked_up(ops)
    post('driver',f'/api/orders/{oid}/collect',{'method':'cash','amount_halalas':2500,'tendered_halalas':2500})
    post('owner',f'/api/orders/{oid}/refund',{'method':'cash','amount_halalas':550,'reason':'Attempt refund free cookie','line_quantities':{'cookie':1}},status=422)
    path=f'/api/order-financial-cases/orders/{oid}'
    context=actor('manager').get(path+'/journey').json()
    payload={'version':context['version'],'line_index':1,'quantity':1,'received_by':'driver','reason':'Customer returned the free cookie'}
    key=uuid4().hex
    result=post('manager',path+'/free-item-return',payload,key=key)
    assert result['financial_effect_halalas']==0 and result['invoice_total_halalas']==2500
    assert post('manager',path+'/free-item-return',payload,key=key)==result
    latest=actor('manager').get(path+'/journey').json()
    assert latest['free_items'][0]['remaining']==0
    post('manager',path+'/free-item-return',{**payload,'version':latest['version']},status=422)
    with db.tx() as c:
        assert c.execute('SELECT total_halalas,payment_status FROM orders WHERE id=%s',(oid,)).fetchone()=={'total_halalas':2500,'payment_status':'paid'}
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
