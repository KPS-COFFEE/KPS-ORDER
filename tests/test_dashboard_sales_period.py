"""Completion-day dashboard detail stays scoped and excludes later adjustments."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from app import db
from app.accounting import period
from test_operations import ops

pytestmark=pytest.mark.postgres
TZ=ZoneInfo('Asia/Riyadh')


def dashboard(actor,lo,hi,who='manager'):
    response=actor(who).get('/api/dashboard',params={'start':lo.isoformat(),'end':hi.isoformat()})
    assert response.status_code==200,response.text
    return response.json()


def complete(c,oid,created,completed):
    c.execute("UPDATE orders SET created_at=%s,completed_at=%s,preparation_status='completed' WHERE id=%s",(created,completed,oid))


def test_completion_at_four_belongs_to_same_summary_items_and_branches(ops):
    actor,post,order,_=ops
    lo=datetime(2026,10,23,4,tzinfo=TZ);hi=lo+timedelta(days=1)
    included=order('pickup');next_day=order('pickup')
    with db.tx() as c:
        complete(c,included,lo-timedelta(microseconds=1),lo)
        complete(c,next_day,lo+timedelta(minutes=15),hi)
        c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,
          delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer,created_at,completed_at)
          VALUES('dashboard-foreign','ops-other','ops-branch-other','ops-other','pickup','completed',
          'not_applicable','cash',90000,0,90000,%s,'{}',%s,%s)""",
          (db.json({'items':[{'product_id':'foreign-only','name':'Foreign private product','quantity':1,'line_total_halalas':90000}]}),lo,lo))
        original=c.execute('SELECT id,created_at,completed_at,total_halalas,snapshot FROM orders ORDER BY id').fetchall()
    result=dashboard(actor,lo,hi)
    assert result['summary']['orders']==1  # Created intake remains a separate clock.
    assert result['summary']['completed']==1
    assert result['summary']['gross_completed_halalas']==result['summary']['net_completed_halalas']==1000
    assert [(r['branch_id'],r['completed'],r['net_halalas']) for r in result['top_branches']]==[('ops-branch',1,1000)]
    assert [(r['product_id'],r['quantity'],r['gross_halalas'],r['net_halalas']) for r in result['top_items']]==[('ops-product',1,1000,1000)]
    previous=dashboard(actor,lo-timedelta(days=1),lo)
    assert previous['summary']['orders']==1 and previous['summary']['completed']==0
    assert previous['top_items']==previous['top_branches']==[]
    assert actor('other').get('/api/dashboard',params={'brand_id':'ops-brand','start':lo.isoformat(),'end':hi.isoformat()}).status_code==403
    with db.tx() as c:
        assert c.execute('SELECT id,created_at,completed_at,total_halalas,snapshot FROM orders ORDER BY id').fetchall()==original


def test_later_refund_does_not_rewrite_earlier_product_or_branch_sales(ops):
    actor,post,order,_=ops
    day=(datetime.now(TZ)-timedelta(days=2)).date().isoformat()
    _,_,lo,hi=period(day,day)
    oid=order('pickup',items=[{'product_id':'ops-product','name':'Coffee','quantity':2,'unit_price_halalas':500,'line_total_halalas':1000,'units':[]}])
    post('branch',f'/api/orders/{oid}/collect',{'method':'card','amount_halalas':1000,'reference':'Synthetic actual terminal receipt'})
    with db.tx() as c:complete(c,oid,lo-timedelta(hours=1),lo+timedelta(hours=1))
    before=dashboard(actor,lo,hi)
    post('owner',f'/api/orders/{oid}/refund',{'method':'card','amount_halalas':500,'reason':'Actual return occurred after historical reporting period','line_quantities':{'ops-product':1}})
    after=dashboard(actor,lo,hi)
    assert after['summary']['completed']==1
    assert after['summary']['gross_completed_halalas']==after['summary']['net_completed_halalas']==1000
    assert after['summary']['refund_halalas']==0
    assert after['top_items']==before['top_items']
    assert after['top_items'][0]['quantity']==2 and after['top_items'][0]['net_halalas']==1000
    assert after['top_branches']==before['top_branches']
    current=actor('manager').get('/api/dashboard').json()
    assert current['summary']['completed']==0 and current['summary']['refund_halalas']==500
    assert current['summary']['net_completed_halalas']==-500
    assert current['top_branches'][0]['net_halalas']==-500


def test_later_source_credit_does_not_rewrite_earlier_product_sales(ops):
    actor,_,order,_=ops
    day=(datetime.now(TZ)-timedelta(days=2)).date().isoformat()
    _,_,lo,hi=period(day,day)
    oid=order('pickup')
    with db.tx() as c:
        complete(c,oid,lo-timedelta(hours=1),lo+timedelta(hours=1))
    before=dashboard(actor,lo,hi)
    with db.tx() as c:
        db.audit(c,'ops-owner','accounting.order_financial_treatment.approved','order',oid,
          {'kind':'price_correction','credit_halalas':200,'original_total_halalas':1000,'note':'Synthetic source correction approved after reporting period'})
    after=dashboard(actor,lo,hi)
    assert after['summary']['net_completed_halalas']==1000
    assert after['top_items']==before['top_items']
    assert after['top_branches']==before['top_branches']


def test_collection_chart_uses_approved_tender_without_reposting_money(ops,monkeypatch):
    from test_collection_tender_corrections import setup,body,approval
    actor,post,oid,manager,driver,original,path=setup(ops,monkeypatch)
    source=actor('accountant').get(path).json()
    proposal=post('accountant',path,body(source))
    post('owner',path+'/'+proposal['id']+'/approve',approval(source))
    result=actor('manager').get('/api/dashboard').json()
    assert result['collections']==[{'method':'card','amount_halalas':1000}]
    assert result['summary']['net_receipts_halalas']==1000
    with db.tx() as c:
        assert c.execute('SELECT * FROM collections WHERE id=%s',(original['id'],)).fetchone()==original
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==1


def discounted_order(ops,delivery=0):
    actor,post,order,_=ops
    items=[{'product_id':pid,'name':pid,'quantity':1,'unit_price_halalas':1000,
            'line_total_halalas':1000,'units':[]} for pid in ('a','b','c')]
    items.append({'product_id':'gift','name':'Free gift','quantity':1,'unit_price_halalas':0,
                  'line_total_halalas':0,'units':[],'is_gift':True})
    oid=db.new_id('dashboard-discount-')
    snapshot={'items':items,'subtotal_halalas':3000,'discount_halalas':1000,
              'delivery_fee_halalas':delivery,'total_halalas':2000+delivery,'branch_name':'Synthetic branch'}
    with db.tx() as c:
        c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,
          delivery_status,payment_method,subtotal_halalas,discount_halalas,delivery_fee_halalas,
          total_halalas,snapshot,customer) VALUES(%s,'ops-brand','ops-branch','ops-customer',
          'pickup','ready','not_applicable','cash',3000,1000,%s,%s,%s,%s)""",
          (oid,delivery,2000+delivery,db.json(snapshot),db.json({'name':'Synthetic customer'})))
    return oid


@pytest.mark.parametrize('delivery',[0,500])
def test_discounted_product_net_conserves_twenty_excluding_delivery_and_gift(ops,delivery):
    actor,_,_,_=ops
    oid=discounted_order(ops,delivery)
    _,_,lo,hi=period('','')
    with db.tx() as c:complete(c,oid,lo,lo+timedelta(seconds=1))
    result=dashboard(actor,lo,hi)
    products={r['product_id']:r for r in result['top_items']}
    assert [products[pid]['net_halalas'] for pid in ('a','b','c')]==[666,667,667]
    assert products['gift']['net_halalas']==0
    assert sum(r['net_halalas'] for r in products.values())==2000
    assert result['summary']['delivery_fee_halalas']==delivery
    assert result['summary']['net_completed_halalas']==2000+delivery


def test_discounted_refund_projection_uses_actual_money_without_extra_hellalas(ops):
    actor,post,_,_=ops
    oid=discounted_order(ops)
    post('branch',f'/api/orders/{oid}/collect',{'method':'card','amount_halalas':2000,'reference':'Synthetic discounted receipt'})
    _,_,lo,hi=period('','')
    with db.tx() as c:complete(c,oid,lo,lo+timedelta(seconds=1))
    post('owner',f'/api/orders/{oid}/refund',{'method':'card','amount_halalas':666,
      'reason':'Return one actually discounted item','line_quantities':{'a':1}})
    result=dashboard(actor,lo,hi)
    products={r['product_id']:r for r in result['top_items']}
    assert products['a']['quantity']==0 and products['gift']['net_halalas']==0
    assert sum(r['net_halalas'] for r in products.values())==1334
    assert result['summary']['net_completed_halalas']==1334


def test_discounted_item_fee_projection_never_adds_fee_to_item_net(ops):
    actor,post,_,_=ops
    oid=discounted_order(ops,500)
    post('branch',f'/api/orders/{oid}/collect',{'method':'card','amount_halalas':2500,'reference':'Synthetic discounted receipt with delivery'})
    _,_,lo,hi=period('','')
    with db.tx() as c:complete(c,oid,lo,lo+timedelta(seconds=1))
    post('owner',f'/api/orders/{oid}/refund',{'method':'card','amount_halalas':500,
      'reason':'Actual documented monetary return after discounted sale'})
    result=dashboard(actor,lo,hi)
    assert sum(r['net_halalas'] for r in result['top_items'])==1600  # 2000 item net * 2000 remaining / 2500 original.
    assert result['summary']['net_completed_halalas']==2000
