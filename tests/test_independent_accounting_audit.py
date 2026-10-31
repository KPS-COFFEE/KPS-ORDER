"""Owner-requested independent accounting probes. Synthetic local data only.

Acceptance for the three independently reproduced accounting regressions.
All application writes are confined to isolated synthetic test schemas.
"""
from datetime import timedelta
import pytest
from app import db
from app.accounting import period
from app.refund_line_values import ceiling
from test_operations import ops
from test_accounting import accountant


def test_discounted_full_item_return_preserves_every_halalah():
    order = dict(total_halalas=2000, delivery_fee_halalas=0, snapshot=dict(items=[
        dict(product_id=pid,quantity=1,line_total_halalas=1000) for pid in ('a','b','c')]))
    actual = ceiling(order,dict(a=1,b=1,c=1),{})
    print('AUDIT_ROUNDING actual_halalas=',actual,' expected_halalas=2000')
    assert actual == 2000


@pytest.mark.postgres
@pytest.mark.parametrize("method", ["cash", "card", "split"])
def test_cancel_before_completion_then_actual_refund_has_zero_sales(ops, method):
    actor,post,order,shifts = ops
    accountant()
    oid=order('pickup')
    payment=dict(method=method,amount_halalas=1000,reference='Synthetic terminal slip')
    if method=='cash':payment['tendered_halalas']=1000
    if method=='split':payment.update(cash_halalas=400,card_halalas=600,tendered_halalas=400)
    post('branch',f'/api/orders/{oid}/collect',payment)
    with db.tx() as c:
        version=c.execute('SELECT version FROM orders WHERE id=%s',(oid,)).fetchone()['version']
    post('owner',f'/api/order-financial-cases/orders/{oid}/cancel',dict(version=version,reason='Customer cancelled before fulfilment'))
    for tender,amount in ([('cash',400),('card',600)] if method=='split' else [(method,1000)]):
        post('owner',f'/api/orders/{oid}/refund',dict(method=tender,amount_halalas=amount,reason='Actual full reversal of prepaid cancelled order'))
    result=actor('accountant').get('/api/accounting/reports').json()
    with db.tx() as c:
        source=c.execute('SELECT completed_at,preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()
    assert source['completed_at'] is None and source['preparation_status']=='cancelled'
    row=result['rows'][0]
    print('AUDIT_CANCEL_REPORT',row)
    assert row['gross']==0 and row['total']==0
    assert row['net_sales']==0


@pytest.mark.postgres
def test_late_completion_dashboard_summary_and_branch_totals_agree(ops):
    actor,post,order,_=ops
    accountant()
    oid=order('pickup')
    post('branch',f'/api/orders/{oid}/collect',dict(method='card',amount_halalas=1000,reference='Synthetic terminal slip'))
    ds,de,lo,hi=period('','')
    with db.tx() as c:
        c.execute("UPDATE orders SET created_at=%s,completed_at=%s,preparation_status='completed' WHERE id=%s",(lo-timedelta(hours=2),lo+timedelta(hours=1),oid))
    response=actor('manager').get('/api/dashboard',params=dict(start=lo.isoformat(),end=hi.isoformat()))
    assert response.status_code==200,response.text
    result=response.json()
    branches=sum(r['net_halalas'] for r in result['top_branches'])
    print('AUDIT_DATE_SUMMARY',result['summary'],'AUDIT_DATE_BRANCHES',result['top_branches'])
    assert result['summary']['net_completed_halalas']==1000
    assert result['summary']['completed']==1
    assert result['summary']['orders']==0
    assert sum(row['gross_halalas'] for row in result['top_items'])==1000
    assert branches==1000
