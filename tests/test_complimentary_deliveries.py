"""Complimentary delivery is operational work, never customer collection."""
from copy import deepcopy
from datetime import datetime,timezone
import pytest
from pydantic import ValidationError
from app import db
from app.complimentary import prepare,is_free
from app.orders import ComplimentaryOrder
from app.branch_printing import receipt
from app.accounting_export import render
from test_operations import ops


def test_zero_collection_keeps_original_reference_value_and_items():
    quote={'subtotal_halalas':1500,'delivery_fee_halalas':500,'total_halalas':2000,
           'items':[{'name':'قهوة','quantity':1,'unit_price_halalas':1500,'line_total_halalas':1500,'units':[{'price_halalas':1500}]}]}
    original=deepcopy(quote)
    result=prepare(quote,'compensation','منتج تالف','manager')
    assert quote==original and result['total_halalas']==0
    assert result['items'][0]['line_total_halalas']==0
    assert result['complimentary']['catalog_value_halalas']==1500
    row={'payment_status':'not_required','total_halalas':0,'snapshot':result}
    assert is_free(row)
    assert not is_free({**row,'total_halalas':1})
    assert not is_free({**row,'payment_status':'unpaid'})
    assert not is_free({'payment_status':'not_required','total_halalas':0,'snapshot':{}})


@pytest.mark.parametrize('service',['pickup','car'])
def test_complimentary_is_delivery_only(service):
    with pytest.raises(ValidationError):
        ComplimentaryOrder(service=service,branch_id='b',kind='hospitality',reason='TEST',items=[{'product_id':'p','quantity':1}],customer={'name':'TEST'})


@pytest.mark.parametrize('lang',['ar','en'])
def test_receipt_shows_type_only_and_accounting_exports_remain_available(lang):
    snapshot=prepare({'subtotal_halalas':1000,'delivery_fee_halalas':500,'total_halalas':1500,'items':[]},'advertising','TEST advertiser','manager')
    order={'id':'test','order_no':1,'brand_id':'ops-brand','created_at':datetime.now(timezone.utc),'snapshot':snapshot,'payment_method':'cash','payment_status':'not_required','total_halalas':0}
    text=str(receipt(order,{'name':'TEST BRANCH'},'order',lang))
    assert 'بدون تحصيل' not in text and 'NO COLLECTION' not in text and 'No collection' not in text
    assert 'TEST advertiser' not in text and '0.00' not in text
    assert ('إعلان' if lang=='ar' else 'Advertising') in text
    data={'group':'complimentary','rows':[],'period':{'start':'2026-10-21','end':'2026-10-21'},'definitions':'TEST reference values only; no collection'}
    assert render(data,'pdf',lang).startswith(b'%PDF')
    assert render(data,'xlsx',lang).startswith(b'PK')


@pytest.mark.postgres
@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_real_order_dispatch_delivery_and_accounting_without_any_collection(ops):
    from test_core import _prepare_catalog,_body
    actor,post,_,shifts=ops
    _prepare_catalog()
    manager,driver=shifts()
    body={**_body('delivery'),'kind':'compensation','reason':'TEST ONLY damaged product'}
    body['customer']['phone']='0501234567'
    body.pop('expected_total_halalas',None)
    q=post('manager','/api/orders/complimentary/quote',body)['quote']
    assert q['total_halalas']==0 and q['complimentary']=={'kind':'compensation'}
    body['expected_total_halalas']=0
    result=post('manager','/api/orders/complimentary',body,key='comp-order');oid=result['order']['id']
    assert result['order']['payment_status']=='not_required'
    assert post('manager','/api/orders/complimentary',body,key='comp-order')==result
    post('driver','/api/orders/complimentary',body,status=403)
    post('other','/api/orders/complimentary',body,status=403)
    task=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver,'accept_order':True})['task']['id']
    post('driver',f'/api/tasks/{task}/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver',f'/api/tasks/{task}/transition',{'status':'picked_up'})
    post('driver',f'/api/orders/{oid}/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':1000},status=409)
    driver_view=actor('driver').get(f'/api/orders/{oid}').json()['order']
    assert driver_view['snapshot']['complimentary']=={'kind':'compensation'}
    post('driver',f'/api/tasks/{task}/transition',{'status':'delivered'})
    with db.tx() as c:
        order=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert order['delivery_status']=='delivered' and order['total_halalas']==0
        financial=order['snapshot']['complimentary']
        assert financial['catalog_value_halalas']>0 and financial['reason']==body['reason']
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM fiscal_documents WHERE order_id=%s',(oid,)).fetchone()['n']==0
    for who in ('manager','branch'):
        visible=actor(who).get(f'/api/orders/{oid}').json()['order']
        assert visible['snapshot']['complimentary']=={'kind':'compensation'}
    counts=actor('driver').get('/api/tasks').json()['counts']
    assert counts['completed']==1
    manager_orders=actor('manager').get('/api/orders?service=delivery').json()['orders']
    assert any(row['id']==oid and row['delivery_status']=='delivered' for row in manager_orders)
    rows=actor('owner').get('/api/accounting/reports?group=complimentary').json()['rows']
    assert any(r['kind']=='تعويض' and r['reason']==body['reason'] for r in rows)
    entry=next(r for r in rows if r['kind']=='تعويض' and r['reason']==body['reason'])
    assert entry['amount']==financial['catalog_value_halalas']
    assert entry['delivery_value']==financial['delivery_value_halalas']
    assert entry['payable']==0
    daily=actor('owner').get('/api/accounting/reports').json()
    assert daily['totals'].get('gross',0)==0 and daily['totals'].get('orders',0)==0
