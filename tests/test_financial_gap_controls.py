from datetime import datetime,timezone
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import db
from app.refund_line_values import ceiling
from test_operations import ops
from test_delivery_workflow_controls import picked_up


def test_selected_quantity_ceiling_excludes_free_gift_and_delivery_fee():
    order={'total_halalas':2500,'delivery_fee_halalas':500,'snapshot':{'items':[
        {'product_id':'paid','quantity':2,'line_total_halalas':2000},
        {'product_id':'gift','quantity':1,'line_total_halalas':0}]}}
    assert ceiling(order,{'paid':1,'gift':1},{})==1000
    assert ceiling(order,{'gift':1},{})==0
    order['total_halalas']=2000
    assert ceiling(order,{'paid':1},{})==750
    assert ceiling(order,{'paid':1},{'paid':1})==750
    with pytest.raises(HTTPException):ceiling(order,{'paid':0},{})

@pytest.mark.postgres
def test_evidenced_zero_receipt_opens_case_without_money_and_allows_delivery(ops):
    actor,post,_,_=ops
    oid,tid,manager,driver=picked_up(ops)
    version=actor('manager').get(f'/api/order-financial-cases/orders/{oid}/journey').json()['version']
    path=f'/api/order-financial-cases/orders/{oid}/nonreceipt'
    body={'version':version,'reason':'Customer did not pay','evidence':'Verified call and actual handover','confirmed_zero_received':True}
    key=uuid4().hex
    first=post('driver',path,body,key=key)
    assert post('driver',path,body,key=key)==first
    assert first['actual_collected_halalas']==0 and first['financial_case']['pending_amount_halalas']==2500
    post('driver',f'/api/tasks/{tid}/transition',{'status':'delivered','proof':'Customer received actual order'})
    post('owner',f'/api/order-financial-cases/orders/{oid}/resolve',{'reason':'A note cannot settle zero collection'},status=409)
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==0
        assert c.execute("SELECT payment_status,total_halalas FROM orders WHERE id=%s",(oid,)).fetchone()=={'payment_status':'unpaid','total_halalas':2500}

@pytest.mark.postgres
def test_manager_delivery_recovery_accepts_documented_partial_collection(ops):
    actor,post,_,_=ops
    oid,tid,manager,driver=picked_up(ops)
    post('driver',f'/api/orders/{oid}/collect',{'method':'cash','amount_halalas':1950,'tendered_halalas':1950,'shortfall_reason':'Partial actual payment received'})
    with db.tx() as c:
        at=c.execute('SELECT now() n').fetchone()['n']
    assert actor('manager').get(f'/api/task-recovery/{tid}').json()['can_resolve_delivery'] is True
    result=post('manager',f'/api/tasks/{tid}/resolve-delivery',{'confirmed':True,'reason':'Driver reports actual delivery','evidence':'Verified delivery call','delivered_at':at.isoformat()})
    assert result['task']['status']=='delivered'
    with db.tx() as c:assert c.execute('SELECT payment_status FROM orders WHERE id=%s',(oid,)).fetchone()['payment_status']=='unpaid'

@pytest.mark.postgres
def test_selected_line_cannot_refund_other_line_or_free_gift_value(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    oid=order(total=2500,items=[
        {'product_id':'coffee','quantity':2,'unit_price_halalas':1000,'line_total_halalas':2000,'name':'Coffee'},
        {'product_id':'extra','quantity':1,'unit_price_halalas':500,'line_total_halalas':500,'name':'Other item'},
        {'product_id':'cookie','quantity':1,'unit_price_halalas':0,'line_total_halalas':0,'name':'Free cookie'}])
    tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    post('driver',f'/api/tasks/{tid}/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver',f'/api/tasks/{tid}/transition',{'status':'picked_up'})
    post('driver',f'/api/orders/{oid}/collect',{'method':'cash','amount_halalas':2500,'tendered_halalas':2500})
    result=post('owner',f'/api/orders/{oid}/refund',{'method':'cash','amount_halalas':1500,'reason':'Return one coffee and free cookie','line_quantities':{'coffee':1,'cookie':1}},status=422)
    assert result['detail']['code']=='REFUND_LINE_VALUE_EXCEEDED'

@pytest.mark.postgres
def test_partial_cash_then_card_retains_actual_split_classification(ops):
    actor,post,_,_=ops
    oid,tid,manager,driver=picked_up(ops)
    post('driver',f'/api/orders/{oid}/collect',{'method':'cash','amount_halalas':1950,'tendered_halalas':1950,'shortfall_reason':'Card portion follows'})
    post('driver',f'/api/orders/{oid}/collect',{'method':'card','amount_halalas':550})
    with db.tx() as c:
        assert c.execute('SELECT payment_method,payment_status FROM orders WHERE id=%s',(oid,)).fetchone()=={'payment_method':'split','payment_status':'paid'}
