"""Manager-day regression using isolated PostgreSQL records."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from uuid import uuid4
import pytest
from app import db
from app.accounting_export import contract
from test_operations import ops
from test_accounting import accountant

TZ=ZoneInfo('Asia/Riyadh')

def duty(c,label,day,morning=False,driver_day=None,manager_user='ops-manager',manager_status='closed',driver_status='closed'):
    manager='test-manager-'+label;driver='test-driver-'+label
    opened=datetime.fromisoformat(day+('T06:00:00' if morning else 'T16:00:00')).replace(tzinfo=TZ)
    for sid,kind,user,parent in [(manager,'manager',manager_user,None),(driver,'driver','ops-driver',manager)]:
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,status,opened_at,closed_at,delivery_operational_date,delivery_window_snapshot) VALUES(%s,%s,%s,'ops-brand','[\"ops-branch\"]',%s,%s,%s,%s,%s,%s)",
          (sid,kind,user,parent,manager_status if kind=='manager' else driver_status,opened,opened+timedelta(hours=14),driver_day if kind=='driver' and driver_day else day,db.json({'period':'morning' if morning else 'evening'})))
    return driver

def sale(order,c,shift,amount,created,collected,complete=True,split=False):
    oid='test-order-'+uuid4().hex
    snap={'items':[{'product_id':'ops-product','name':'TEST coffee','quantity':1,'unit_price_halalas':amount,'line_total_halalas':amount,'units':[],'note':''}]}
    c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'ops-brand','ops-branch','ops-customer','delivery','ready','pending','card',%s,0,%s,%s,'{}')",(oid,amount,amount,db.json(snap)))
    created=datetime.fromisoformat(created).replace(tzinfo=TZ);collected=datetime.fromisoformat(collected).replace(tzinfo=TZ)
    c.execute("UPDATE orders SET created_at=%s,completed_at=%s,preparation_status=%s,delivery_status=%s,payment_status='collected' WHERE id=%s",(created,collected if complete else None,'completed' if complete else 'ready','delivered' if complete else 'pending',oid))
    c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,created_at,delivered_at) VALUES(%s,%s,'ops-driver',%s,%s,'ops-manager',%s,%s)",(uuid4().hex,oid,shift,'delivered' if complete else 'assigned',created,collected if complete else None))
    for method,value in ([('cash',amount//2),('card',amount-amount//2)] if split else [('card',amount)]):
        c.execute("INSERT INTO collections(id,order_id,shift_id,collector_id,method,amount_halalas,created_at) VALUES(%s,%s,%s,'ops-driver',%s,%s,%s)",(uuid4().hex,oid,shift,method,value,collected))
    return oid

def report(actor,day,group='daily'):
    r=actor('accountant').get('/api/accounting/reports',params={'start':day,'end':day,'group':group})
    assert r.status_code==200,r.text
    return r.json()

@pytest.mark.postgres
def test_late_31_and_16_50_follow_manager_not_clock_or_child_day(ops):
    actor,_,order,_=ops;accountant()
    with db.tx() as c:
        old=duty(c,'27','2026-10-27',driver_day='2026-10-28')
        evening=duty(c,'28','2026-10-28');morning=duty(c,'morning28','2026-10-28',morning=True)
        ids=[sale(order,c,old,1100,'2026-10-28T03:55:00','2026-10-28T04:15:00'),
             sale(order,c,old,2000,'2026-10-28T03:32:00','2026-10-28T04:27:00'),
             sale(order,c,morning,21350,'2026-10-28T08:00:00','2026-10-28T08:30:00'),
             sale(order,c,evening,231600,'2026-10-28T19:00:00','2026-10-28T19:30:00',split=True),
             sale(order,c,evening,1650,'2026-10-29T03:48:00','2026-10-29T04:19:00')]
        before=c.execute('SELECT * FROM collections WHERE order_id=ANY(%s) ORDER BY id',(ids,)).fetchall()
    a=report(actor,'2026-10-27');b=report(actor,'2026-10-28');after=report(actor,'2026-10-29')
    assert a['totals']['total']==3100 and a['totals']['gross']==3100
    assert b['totals']['total']==254600 and b['totals']['gross']==254600
    assert b['totals']['correct']==3 and b['totals']['orders']==3
    assert after['totals']['total']==0 and after['totals']['gross']==0
    span=actor('accountant').get('/api/accounting/reports?start=2026-10-27&end=2026-10-29').json()
    assert span['totals']['total']==257700
    for group in ('branch','driver','driver_daily','items'):
        result=report(actor,'2026-10-28',group)
        assert result['totals']['gross' if group=='items' else 'total']==254600
    assert b['period']['basis']=='manager_shift'
    assert b['rows'][0]['period']=='الصباحي والمسائي حسب شفت المدير'
    assert contract(b,'en')['totals']['total']==254600
    for fmt in ('pdf','xlsx'):
        r=actor('accountant').get('/api/accounting/reports',params={'start':'2026-10-28','end':'2026-10-28','format':fmt})
        assert r.status_code==200 and r.content.startswith(b'%PDF' if fmt=='pdf' else b'PK')
    with db.tx() as c:
        assert c.execute('SELECT * FROM collections WHERE order_id=ANY(%s) ORDER BY id',(ids,)).fetchall()==before

@pytest.mark.postgres
def test_pending_payment_never_manufactures_completed_sale(ops):
    actor,_,order,_=ops;accountant()
    with db.tx() as c:
        shift=duty(c,'pending','2026-10-27')
        sale(order,c,shift,1100,'2026-10-28T03:55:00','2026-10-28T05:00:00',complete=False)
    result=report(actor,'2026-10-27')
    assert result['totals']['orders']==1 and result['totals']['total']==1100
    assert result['totals']['gross']==0 and result['totals']['correct']==0
    assert report(actor,'2026-10-28')['totals']['total']==0

@pytest.mark.postgres
def test_variance_uses_manager_day_when_driver_date_differs(ops):
    actor,_,_,_=ops;accountant()
    with db.tx() as c:
        sid=duty(c,'variance','2026-10-27',driver_day='2026-10-28')
        snapshot={'shift':{'id':sid,'user_id':'ops-driver','submission':{'difference_halalas':-100}},'summary':{'completed_orders':2,'card_collected_halalas':3100,'net_collections_halalas':3100}}
        c.execute("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES('test-variance-report',%s,'driver','ops-brand','[\"ops-branch\"]','ops-driver',%s)",(sid,db.json(snapshot)))
    old=report(actor,'2026-10-27','deficits')
    assert len(old['rows'])==1 and old['rows'][0]['day']=='2026-10-27'
    assert old['totals']['shortage']==100
    assert report(actor,'2026-10-28','deficits')['rows']==[]

@pytest.mark.postgres
def test_later_refund_uses_processing_duty_preserving_original_receipt(ops):
    actor,_,order,_=ops;accountant()
    with db.tx() as c:
        old=duty(c,'refund27','2026-10-27');later=duty(c,'refund28','2026-10-28')
        oid=sale(order,c,old,2000,'2026-10-28T03:32:00','2026-10-28T04:27:00')
        payment=c.execute('SELECT id FROM collections WHERE order_id=%s',(oid,)).fetchone()['id']
        c.execute("INSERT INTO refunds(id,collection_id,order_id,shift_id,method,amount_halalas,reason,authorized_by,created_at) VALUES(%s,%s,%s,%s,'card',500,'TEST actual later refund','ops-owner','2026-10-29T05:00:00+03')",(uuid4().hex,payment,oid,later))
    old=report(actor,'2026-10-27');later=report(actor,'2026-10-28');after=report(actor,'2026-10-29')
    assert old['totals']['total']==2000 and old['totals']['net_sales']==2000
    assert later['totals']['total']==-500 and later['totals']['net_sales']==-500
    assert later['totals']['refund_card']==500 and later['totals']['gross']==0
    assert after['totals']['total']==0

@pytest.mark.postgres
@pytest.mark.parametrize('association',['creation','acceptance'])
def test_unassigned_order_keeps_recorded_manager_day(ops,association):
    actor,_,order,_=ops;accountant()
    oid=order() if association=='acceptance' else 'test-unassigned-'+uuid4().hex
    with db.tx() as c:
        duty(c,'unassigned','2026-10-28')
        manager='test-manager-unassigned'
        if association=='creation':
            c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer,created_at) VALUES(%s,'ops-brand','ops-branch','ops-customer','delivery','ready','pending','card',1000,0,1000,%s,'{}','2026-10-29T04:30:00+03')",(oid,db.json({'manager_shift_id':manager,'items':[]})))
        else:
            c.execute("UPDATE orders SET created_at='2026-10-29T04:30:00+03' WHERE id=%s",(oid,))
            db.event(c,oid,'ops-manager','preparation.accepted',{'manager_shift_id':manager})
    result=report(actor,'2026-10-28')
    assert result['totals']['orders']==1 and result['totals']['gross']==0
    assert report(actor,'2026-10-29')['totals']['orders']==0

@pytest.mark.postgres
def test_transferred_task_and_split_payment_count_once_and_scope_stays_closed(ops):
    actor,_,order,_=ops;accountant()
    with db.tx() as c:
        old=duty(c,'transfer27','2026-10-27');target=duty(c,'transfer28','2026-10-28')
        oid=sale(order,c,target,3100,'2026-10-28T03:50:00','2026-10-29T05:00:00',split=True)
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,created_at) VALUES(%s,%s,'ops-driver',%s,'transferred','ops-manager','2026-10-28T03:40:00+03')",(uuid4().hex,oid,old))
    r=report(actor,'2026-10-28')
    assert r['totals']['orders']==1 and r['totals']['correct']==1
    assert r['totals']['cash']==1550 and r['totals']['card']==1550
    assert report(actor,'2026-10-27')['totals']['total']==0
    assert report(actor,'2026-10-29')['totals']['total']==0
    assert actor('accountant').get('/api/accounting/reports?branch_ids=ops-branch-other').status_code==403
    assert actor('driver').get('/api/accounting/reports').status_code==403

@pytest.mark.postgres
def test_all_manager_duties_remain_visible_before_financial_allocation(ops):
    actor,post,_,_=ops;accountant()
    with db.tx() as c:
        c.execute("UPDATE users SET display_name='أحمد عبدالناصر' WHERE id='ops-manager'")
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('test-ali','علي','delivery_manager','ops-brand','[\"ops-branch\"]'),('test-open-manager','TEST OPEN','delivery_manager','ops-brand','[\"ops-branch\"]')")
        duty(c,'ahmed','2026-10-28',morning=True)
        duty(c,'ali','2026-10-28',manager_user='test-ali')
        duty(c,'open','2026-10-29',manager_user='test-open-manager',manager_status='open')
    r=actor('accountant').get('/api/accounting/manager-cards')
    assert r.status_code==200,r.text
    cards={c['manager_name']:c for c in r.json()['cards']}
    assert set(cards)=={'علي','أحمد عبدالناصر'}
    assert cards['علي']['day']=='2026-10-28' and cards['علي']['period']=='evening'
    assert cards['أحمد عبدالناصر']['period']=='morning'
    assert cards['علي']['operationally_closed'] and cards['علي']['financial_ledger_closed']
    assert cards['علي']['pending_driver_count']==1
    assert cards['علي']['total']==0 and cards['علي']['completion_percent']==0
    assert not cards['علي']['ready'] and cards['علي']['manager_report_id'] is None
    assert cards['علي']['pending_drivers'][0]['id']=='test-driver-ali'
    assert cards['علي']['pending_drivers'][0]['reason']=='driver_report_missing'
    assert cards['علي']['pending_drivers'][0]['responsible']=='delivery_manager'
    assert cards['علي']['remaining_count']==1
    assert cards['علي']['branch_names'] and cards['علي']['opened_at'] and cards['علي']['closed_at']
    assert cards['علي']['blockers']==[{'reason':'driver_records_pending','responsible':'delivery_manager'}]
    post('accountant','/api/accounting/manager-cards/test-manager-ali/approve-day',{'note':'TEST no premature financial approval'},status=409)
    assert actor('other').get('/api/accounting/manager-cards').json()['cards']==[]
    assert actor('driver').get('/api/accounting/manager-cards').status_code==403

@pytest.mark.postgres
def test_accounting_card_identifies_open_child_after_midnight(ops):
    actor,_,_,_=ops;accountant()
    with db.tx() as c:
        driver=duty(c,'late-open','2026-10-28',driver_day='2026-10-29',driver_status='open')
    response=actor('accountant').get('/api/accounting/manager-cards')
    assert response.status_code==200,response.text
    card=next(card for card in response.json()['cards'] if card['id']=='test-manager-late-open')
    assert card['day']=='2026-10-28'
    assert card['pending_drivers'][0]['reason']=='driver_shift_open'
    assert card['pending_drivers'][0]['responsible']=='delivery_manager'
    assert card['remaining_count']==1
    assert not card['ready'] and card['completion_percent']<100

@pytest.mark.postgres
def test_accounting_shift_cards_page_without_losing_archive_or_scope(ops):
    actor,_,_,_=ops;accountant()
    with db.tx() as c:
        for index in range(23):duty(c,'page-'+str(index),'2026-10-28')
    first=actor('accountant').get('/api/accounting/manager-cards').json()
    second=actor('accountant').get('/api/accounting/manager-cards?offset=20').json()
    assert len(first['cards'])==20 and first['has_more']
    assert len(second['cards'])==3 and not second['has_more']
    assert not ({row['id'] for row in first['cards']}&{row['id'] for row in second['cards']})
    assert actor('other').get('/api/accounting/manager-cards').json()['cards']==[]
    focus=first['cards'][0]['id']
    focused=actor('accountant').get('/api/accounting/manager-cards',params={'offset':20,'focus_id':focus}).json()
    assert len(focused['cards'])==4 and focused['cards'][-1]['id']==focus
    assert actor('other').get('/api/accounting/manager-cards',params={'focus_id':focus}).json()['cards']==[]
    assert actor('accountant').get('/api/accounting/manager-cards?page_size=51').status_code==422
