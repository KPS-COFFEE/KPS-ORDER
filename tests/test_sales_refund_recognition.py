"""Repayments and sale adjustments have separate, conserved reporting clocks."""
from datetime import timedelta
from uuid import uuid4
import pytest
from app import db
from app.accounting import period
from app.financial_totals import sales_adjustment_at
from test_operations import ops
from test_accounting import accountant


def test_sale_adjustment_requires_completion_and_never_precedes_it():
    _,_,lo,_=period('2026-10-01','2026-10-01')
    assert sales_adjustment_at(None,lo) is None
    assert sales_adjustment_at(lo+timedelta(hours=1),lo)==lo+timedelta(hours=1)
    assert sales_adjustment_at(lo,lo+timedelta(hours=1))==lo+timedelta(hours=1)


def seed_money(order,mode,credit=False):
    oid=order('pickup')
    _,_,lo,_=period('2026-10-01','2026-10-01')
    completed=None if mode=='cancelled' else lo+timedelta(days=1,hours=1) if mode=='before' else lo+timedelta(hours=1)
    movement=lo+timedelta(days=1,hours=2) if mode=='after' else lo+timedelta(hours=2)
    cid='recognition-collection-'+uuid4().hex
    with db.tx() as c:
        c.execute("UPDATE orders SET created_at=%s,completed_at=%s,preparation_status=%s WHERE id=%s",(lo,completed,'cancelled' if mode=='cancelled' else 'completed',oid))
        c.execute("INSERT INTO collections(id,order_id,collector_id,method,amount_halalas,created_at) VALUES(%s,%s,'ops-branch','card',1000,%s)",(cid,oid,lo+timedelta(minutes=30)))
        if credit:
            c.execute("INSERT INTO audit_events(id,actor_id,action,entity_type,entity_id,details,created_at) VALUES(%s,'ops-owner','accounting.order_financial_treatment.approved','order',%s,%s,%s)",
                ('recognition-credit-'+uuid4().hex,oid,db.json(dict(kind='price_correction',credit_halalas=200,original_total_halalas=1000,note='Synthetic verified source price')),movement))
        else:
            c.execute("INSERT INTO refunds(id,collection_id,order_id,method,amount_halalas,reason,authorized_by,created_at) VALUES(%s,%s,%s,'card',200,'Synthetic actual repayment','ops-owner',%s)",
                ('recognition-refund-'+uuid4().hex,cid,oid,movement))
    return oid


def reports(actor,day):
    _,_,lo,hi=period(day,day)
    accounting=actor('accountant').get('/api/accounting/reports',params=dict(start=day,end=day))
    dashboard=actor('owner').get('/api/dashboard',params=dict(start=lo.isoformat(),end=hi.isoformat()))
    assert accounting.status_code==dashboard.status_code==200,(accounting.text,dashboard.text)
    return accounting.json()['rows'][0],dashboard.json()


@pytest.mark.postgres
@pytest.mark.parametrize('mode,first_sales,second_sales,first_refund,second_refund',[
    ('cancelled',0,0,200,0),
    ('before',0,800,200,0),
    ('after',1000,-200,0,200),
])
def test_cash_repayment_and_sales_recognition_agree_across_periods(ops,mode,first_sales,second_sales,first_refund,second_refund):
    actor,_,order,_=ops
    accountant();seed_money(order,mode)
    for day,expected,refund in [('2026-10-01',first_sales,first_refund),('2026-10-02',second_sales,second_refund)]:
        row,dash=reports(actor,day)
        assert row['net_sales']==dash['summary']['net_completed_halalas']==expected
        assert row['refunds']==dash['summary']['refund_halalas']==refund
        assert sum(r['net_halalas'] for r in dash['top_branches'])==expected
        assert row['total']==dash['summary']['net_receipts_halalas']


@pytest.mark.postgres
def test_precompletion_price_credit_is_documented_now_but_reverses_sale_at_completion(ops):
    actor,_,order,_=ops
    accountant();seed_money(order,'before',credit=True)
    first,first_dash=reports(actor,'2026-10-01')
    second,second_dash=reports(actor,'2026-10-02')
    assert first['price_credits']==first_dash['summary']['price_credit_halalas']==200
    assert first['net_sales']==first_dash['summary']['net_completed_halalas']==0
    assert second['price_credits']==second_dash['summary']['price_credit_halalas']==0
    assert second['net_sales']==second_dash['summary']['net_completed_halalas']==800
    assert second['sales_price_credits']==second_dash['summary']['recognized_price_credit_halalas']==200
    assert second['refunds']==second_dash['summary']['refund_halalas']==0


@pytest.mark.postgres
def test_complimentary_completion_keeps_count_without_reference_revenue(ops):
    actor,_,_,_=ops
    oid='recognition-complimentary-'+uuid4().hex
    _,_,lo,hi=period('2026-10-01','2026-10-01')
    snapshot=dict(items=[],complimentary=dict(kind='hospitality',original_total_halalas=1000,original_delivery_fee_halalas=300))
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer,completed_at) VALUES(%s,'ops-brand','ops-branch','ops-customer','pickup','completed','not_applicable','cash',0,0,0,%s,'{}',%s)",
            (oid,db.json(snapshot),lo+timedelta(hours=1)))
    response=actor('owner').get('/api/dashboard',params=dict(start=lo.isoformat(),end=hi.isoformat()))
    assert response.status_code==200,response.text
    result=response.json()
    assert result['summary']['completed']==1
    assert result['summary']['gross_completed_halalas']==result['summary']['net_completed_halalas']==0
    assert result['top_branches'][0]['completed']==1 and result['top_branches'][0]['net_halalas']==0


def test_reporting_clock_does_not_rewrite_nested_authorization_dates():
    from app.financial_totals import _at
    original="o.brand_id=%s AND o.created_at>=%s AND o.created_at<%s AND (o.created_at>=%s AND o.created_at<%s)"
    replaced=_at(original,'o.completed_at')
    assert replaced=="o.brand_id=%s AND o.completed_at>=%s AND o.completed_at<%s AND (o.created_at>=%s AND o.created_at<%s)"
