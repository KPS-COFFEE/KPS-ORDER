"""Prior refund allocation adds evidence and quantity lineage, never money."""
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app import db
from app.refund_item_allocations import Proposal,Approval
from test_operations import ops
from test_accounting import accountant


@pytest.mark.parametrize('body',[
    dict(refund_ids=[],line_quantities={'coffee':1},evidence_reference='Original receipt',note='Documented return'),
    dict(refund_ids=['old'],line_quantities={'coffee':True},evidence_reference='Original receipt',note='Documented return'),
    dict(refund_ids=['old'],line_quantities={'coffee':1},evidence_reference='\x00bad',note='Documented return'),
])
def test_item_allocation_strict_source_and_quantity(body):
    with pytest.raises(ValidationError):Proposal(**body)


def source(oid):
    with db.tx() as c:
        return dict(order=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone(),
            refunds=c.execute('SELECT * FROM refunds WHERE order_id=%s ORDER BY id',(oid,)).fetchall(),
            collections=c.execute('SELECT * FROM collections WHERE order_id=%s ORDER BY id',(oid,)).fetchall(),
            custody=c.execute('SELECT * FROM custody_events ORDER BY id').fetchall())


def paid_order(ops):
    actor,post,order,shifts=ops
    manager,driver=shifts()
    oid=order(total=2500,items=[
        {'product_id':'coffee','quantity':2,'unit_price_halalas':1000,'line_total_halalas':2000,'name':'Coffee'},
        {'product_id':'extra','quantity':1,'unit_price_halalas':500,'line_total_halalas':500,'name':'Other'},
        {'product_id':'gift','quantity':1,'unit_price_halalas':0,'line_total_halalas':0,'name':'Free cookie'}])
    tid=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    post('driver',f'/api/tasks/{tid}/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver',f'/api/tasks/{tid}/transition',{'status':'picked_up'})
    post('driver',f'/api/orders/{oid}/collect',{'method':'cash','amount_halalas':2500,'tendered_halalas':2500})
    refund=post('owner',f'/api/orders/{oid}/refund',{'method':'cash','amount_halalas':500,'reason':'Actual prior monetary refund needs item evidence'})
    return oid,refund['refund_ids']


@pytest.mark.postgres
def test_independent_prior_refund_allocation_preserves_money_and_prevents_duplicate_quantities(ops):
    actor,post,_,_=ops;accountant()
    oid,ids=paid_order(ops);url='/api/accounting/refund-item-allocations/orders/'+oid
    original=source(oid)
    payload=dict(refund_ids=ids,line_quantities={'coffee':1},evidence_reference='Return form R-001',note='Original refund covered one returned coffee')
    post('driver',url,payload,status=403)
    post('manager',url,payload,status=403)
    assert actor('other').get(url).status_code==404
    key=uuid4().hex
    pending=post('accountant',url,payload,key=key)
    assert post('accountant',url,payload,key=key)==pending
    approve_url=url+'/'+pending['id']+'/approve'
    approval=dict(confirmed=True,evidence_reference='Original signed return R-001 verified',note='Independent original invoice and quantity check')
    post('accountant',approve_url,approval,status=403)
    key=uuid4().hex
    approved=post('owner',approve_url,approval,key=key)
    assert post('owner',approve_url,approval,key=key)==approved
    assert approved['no_money_posted'] and approved['status']=='approved'
    assert source(oid)==original
    current=actor('accountant').get(url).json()
    assert current['unallocated_refunds']==[] and current['previous_quantities']['coffee']==1
    post('accountant',url,payload,status=409)
    # The approved overlay consumes quantity without another refund. Only the
    # second physical coffee remains available for a later actual refund.
    post('owner',f'/api/orders/{oid}/refund',dict(method='cash',amount_halalas=1000,
        reason='Actual second returned coffee',line_quantities={'coffee':1}))
    post('owner',f'/api/orders/{oid}/refund',dict(method='cash',amount_halalas=1000,
        reason='Attempted duplicate returned coffee',line_quantities={'coffee':1}),status=422)


@pytest.mark.postgres
def test_prior_paid_money_cannot_be_allocated_to_free_item(ops):
    actor,post,_,_=ops;accountant()
    oid,ids=paid_order(ops);url='/api/accounting/refund-item-allocations/orders/'+oid
    original=source(oid)
    result=post('accountant',url,dict(refund_ids=ids,line_quantities={'gift':1},evidence_reference='Free gift receipt',note='Attempted paid refund against free gift'),status=422)
    assert result['detail']['code']=='REFUND_ITEM_ALLOCATION_VALUE'
    assert source(oid)==original
