from app.refund_line_values import unallocated_prior_ids


def refund(identifier,amount,quantities=None,**kw):
    return dict(id=identifier,amount_halalas=amount,line_quantities=quantities or {},**kw)


def test_prior_money_without_item_quantity_needs_accounting_allocation():
    assert unallocated_prior_ids([refund('old',500)],[])==['old']


def test_split_collection_refund_uses_exact_immutable_group_evidence():
    rows=[refund('first',300,{'coffee':1}),refund('second',200)]
    event={'details':dict(refund_ids=['first','second'],amount_halalas=500,line_quantities={'coffee':1})}
    assert unallocated_prior_ids(rows,[event])==[]
    event['details']['amount_halalas']=499
    assert unallocated_prior_ids(rows,[event])==['second']


def test_amendment_refund_and_test_void_do_not_invent_returned_quantities():
    rows=[refund('amendment',500,amendment_id='approved-amendment'),refund('void',500,record_kind='test_void')]
    assert unallocated_prior_ids(rows,[])==[]


def test_event_cannot_reuse_id_or_attach_another_unrelated_refund():
    rows=[refund('first',300,{'coffee':1}),refund('second',200)]
    event={'details':dict(refund_ids=['first','second','second'],amount_halalas=700,line_quantities={'coffee':1})}
    assert unallocated_prior_ids(rows,[event])==['second']


import pytest
from app import db
from test_operations import ops


@pytest.mark.postgres
def test_selected_return_after_unallocated_money_refund_preserves_original(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    oid=order(total=2500,items=[
        {'product_id':'coffee','quantity':2,'unit_price_halalas':1000,'line_total_halalas':2000,'name':'Coffee'},
        {'product_id':'extra','quantity':1,'unit_price_halalas':500,'line_total_halalas':500,'name':'Other'}])
    tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    post('driver',f'/api/tasks/{tid}/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver',f'/api/tasks/{tid}/transition',{'status':'picked_up'})
    post('driver',f'/api/orders/{oid}/collect',{'method':'cash','amount_halalas':2500,'tendered_halalas':2500})
    post('owner',f'/api/orders/{oid}/refund',{'method':'cash','amount_halalas':500,'reason':'Documented monetary refund before line allocation'})
    with db.tx() as c:before=c.execute('SELECT * FROM refunds WHERE order_id=%s ORDER BY id',(oid,)).fetchall()
    result=post('owner',f'/api/orders/{oid}/refund',{'method':'cash','amount_halalas':1000,'reason':'Returning coffee after prior ambiguous refund','line_quantities':{'coffee':1}},status=409)
    assert result['detail']['code']=='REFUND_PRIOR_ITEM_ALLOCATION_REQUIRED'
    with db.tx() as c:assert c.execute('SELECT * FROM refunds WHERE order_id=%s ORDER BY id',(oid,)).fetchall()==before


@pytest.mark.parametrize('change',[
    {'total_halalas':None},{'delivery_fee_halalas':None},{'delivery_fee_halalas':False},
    {'snapshot':{}},{'snapshot':{'items':[{'product_id':'coffee','quantity':1,'line_total_halalas':False}]}},
    {'snapshot':{'items':[{'product_id':'coffee','quantity':'1','line_total_halalas':1000}]}}])
def test_item_refund_ceiling_does_not_infer_missing_or_coerced_source_values(change):
    from fastapi import HTTPException
    from app.refund_line_values import ceiling
    order=dict(total_halalas=1000,delivery_fee_halalas=0,snapshot=dict(items=[dict(product_id='coffee',quantity=1,line_total_halalas=1000)]))
    order.update(change)
    with pytest.raises(HTTPException) as error:ceiling(order,{'coffee':1},{})
    assert error.value.status_code==409
    assert error.value.detail['code']=='REFUND_LINE_SNAPSHOT'


def test_item_quantity_report_overlay_preserves_money_and_original_split_rows():
    from copy import deepcopy
    from app.refund_line_values import overlay_item_quantities
    rows=[refund('first',300),refund('second',200)]
    before=deepcopy(rows)
    projected=overlay_item_quantities(rows,[dict(refund_ids=['first','second'],line_quantities={'coffee':1})])
    assert rows==before
    assert projected[0]['line_quantities']=={'coffee':1} and projected[1]['line_quantities']=={}
    assert sum(r['amount_halalas'] for r in projected)==500
    assert overlay_item_quantities(rows,[])==rows
