"""Real cancellation separates physical work, fiscal correction, and actual cash."""
from uuid import uuid4
import pytest
from app import db
from test_operations import ops, close_driver
from test_accounting import accountant


@pytest.mark.postgres
def test_unpaid_cancel_retains_assignment_and_historical_driver_visibility(ops):
    actor,post,order,shifts=ops
    _,driver=shifts();oid=order()
    request=post('customer','/api/order-requests/orders/'+oid,{'kind':'cancel','note':'Customer requests cancellation'})
    task=post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':driver})['task']['id']
    with db.tx() as c:version=c.execute('SELECT version FROM orders WHERE id=%s',(oid,)).fetchone()['version']
    path='/api/order-financial-cases/orders/'+oid
    body={'version':version,'reason':'طلب العميل إلغاء الطلب'};key=uuid4().hex
    result=post('manager',path+'/cancel',body,key=key)
    assert result['financial_case'] is None
    assert not actor('manager').get('/api/order-amendments/inbox').json()['requests']
    assert post('manager',path+'/cancel',body,key=key)==result
    history=actor('driver').get(path+'/journey').json()
    assert history['tasks'][0]['id']==task and history['tasks'][0]['status']=='cancelled'
    assert history['timeline'][-1]['actor_id']=='ops-manager'
    assert actor('other').get(path+'/journey').status_code==404
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='order.operationally_cancelled'",(oid,)).fetchone()['n']==1


@pytest.mark.postgres
def test_paid_cancel_preserves_cash_closes_operations_and_requires_real_refund(ops):
    actor,post,order,shifts=ops;accountant()
    manager,driver=shifts();oid=order()
    task=post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':driver})['task']['id']
    post('driver','/api/tasks/'+task+'/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver','/api/tasks/'+task+'/transition',{'status':'picked_up'})
    post('driver','/api/orders/'+oid+'/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':1000})
    with db.tx() as c:
        version=c.execute('SELECT version FROM orders WHERE id=%s',(oid,)).fetchone()['version']
        custody=c.execute('SELECT * FROM custody_events WHERE shift_id=%s ORDER BY id',(driver,)).fetchall()
    path='/api/order-financial-cases/orders/'+oid
    body={'version':version,'reason':'إلغاء موثق بعد استلام السائق'}
    post('manager',path+'/cancel',body,status=409)
    body['physical_resolution']='returned_to_branch'
    result=post('manager',path+'/cancel',body)
    assert result['payment_status']=='paid' and result['financial_case']['status']=='pending'
    assert result['financial_case']['opened_at'] and not result['financial_case']['operationally_blocking']
    post('manager',path+'/resolve',{'reason':'إقفال بلا صلاحية'},status=403)
    post('accountant',path+'/resolve',{'reason':'محاولة قبل رد المبلغ'},status=409)
    context=actor('accountant').get(path+'/refund-context')
    assert context.status_code==200,context.text
    assert context.json()['order']['snapshot']['items']
    assert context.json()['methods']==[{'method':'cash','available_halalas':1000}]
    assert context.json()['manual_refund_allowed'] and not context.json()['online_payment']
    assert any(s['id']==driver and s['available_balance_halalas']==1000 for s in context.json()['shifts'])
    assert actor('manager').get(path+'/refund-context').status_code==403
    with db.tx() as c:
        assert c.execute('SELECT * FROM custody_events WHERE shift_id=%s ORDER BY id',(driver,)).fetchall()==custody
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
    close_driver(post,driver,manager,counted=1000)
    assert len(actor('accountant').get('/api/order-financial-cases').json()['cases'])==1
    # Cash is now with manager; refund explicitly uses that real open custody.
    post('accountant','/api/orders/'+oid+'/refund',{'method':'cash','amount_halalas':1000,'reason':'استرداد فعلي للعميل','shift_id':manager})
    key=uuid4().hex;resolution={'reason':'مطابقة الاسترداد الفعلي وإقفال المعالجة'}
    done=post('accountant',path+'/resolve',resolution,key=key)
    assert done['financial_case']['status']=='resolved'
    assert post('accountant',path+'/resolve',resolution,key=key)==done
    assert not actor('accountant').get('/api/order-financial-cases').json()['cases']
    journey=actor('accountant').get(path+'/journey').json()
    assert len(journey['collections'])==len(journey['refunds'])==1


@pytest.fixture
def fiscal_sale(client,request):
    from test_fiscal import fiscal_sale as source
    yield from source.__wrapped__(client,request)

@pytest.mark.postgres
def test_issued_unpaid_invoice_waits_for_accounting_without_invented_refund(fiscal_sale):
    client,oid,user=fiscal_sale
    with db.tx() as c:c.execute("UPDATE orders SET payment_status='unpaid',delivery_status='unassigned',preparation_status='new' WHERE id=%s",(oid,))
    def post(path,body):
        return client.post(path,json=body,headers={'Idempotency-Key':uuid4().hex})
    r=post('/api/fiscal/policy',{'enabled':True,'phase':'unconfirmed','issuance_mode':'owner_requested','reason':'Test early invoice policy'})
    assert r.status_code==200,r.text
    issued=post('/api/fiscal/orders/'+oid+'/print',{});assert issued.status_code==200,issued.text
    with db.tx() as c:version=c.execute('SELECT version FROM orders WHERE id=%s',(oid,)).fetchone()['version']
    assert post('/api/orders/'+oid+'/preparation',{'version':version,'status':'cancelled','reason':'Legacy route must not credit automatically'}).status_code==409
    path='/api/order-financial-cases/orders/'+oid
    cancelled=post(path+'/cancel',{'version':version,'reason':'Customer requested cancellation'})
    assert cancelled.status_code==200,cancelled.text
    pending=cancelled.json()['financial_case']
    assert pending['status']=='pending' and pending['invoice_number'] and pending['pending_amount_halalas']==0
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM fiscal_documents WHERE order_id=%s AND kind='credit_note'",(oid,)).fetchone()['n']==0
    resolved=post(path+'/resolve',{'reason':'Accounting confirms no collection and credits invoice'})
    assert resolved.status_code==200,resolved.text
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
        assert c.execute("SELECT count(*) n FROM fiscal_documents WHERE order_id=%s AND kind='credit_note'",(oid,)).fetchone()['n']==1


def test_case_permissions_and_request_contract_fail_closed():
    from app.order_financial_cases import accounting,Cancellation,resolve,journey
    from app.employee_permissions import required_for
    from pydantic import ValidationError
    assert not accounting({'role':'delivery_manager','permissions':['accounting_review']})
    assert not accounting({'role':'admin','permissions':[]})
    assert accounting({'role':'admin','permissions':['accounting_review']})
    assert required_for(resolve,'admin')=='accounting_review'
    assert required_for(journey,'driver')=='tasks_view'
    with pytest.raises(ValidationError):Cancellation(version=True,reason='Valid reason')
    with pytest.raises(ValidationError):Cancellation(version=1,reason='   ')
    with pytest.raises(ValidationError):Cancellation(version=1,reason='Valid reason',physical_resolution='refunded')


@pytest.mark.postgres
def test_postclose_card_case_refund_keeps_frozen_closure_and_collection_lineage(ops):
    actor,post,order,shifts=ops;accountant()
    manager,driver=shifts();oid=order()
    task=post('manager','/api/orders/'+oid+'/assign',{'driver_shift_id':driver})['task']['id']
    post('driver','/api/tasks/'+task+'/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver','/api/tasks/'+task+'/transition',{'status':'picked_up'})
    post('driver','/api/orders/'+oid+'/collect',{'method':'card','amount_halalas':1000})
    with db.tx() as c:version=c.execute('SELECT version FROM orders WHERE id=%s',(oid,)).fetchone()['version']
    path='/api/order-financial-cases/orders/'+oid
    post('manager',path+'/cancel',{'version':version,'reason':'إلغاء بعد الشبكة وإعادة الطلب للفرع','physical_resolution':'returned_to_branch'})
    def card_post(who,path,body=None,**kwargs):
        if path.endswith('/submit'):body={**body,'terminal_total_halalas':1000}
        return post(who,path,body,**kwargs)
    close_driver(card_post,driver,manager,counted=0)
    with db.tx() as c:
        before=c.execute('SELECT snapshot FROM closure_reports WHERE shift_id=%s',(driver,)).fetchone()
        collection=c.execute('SELECT * FROM collections WHERE order_id=%s',(oid,)).fetchone()
        custody=c.execute('SELECT * FROM custody_events WHERE shift_id=%s ORDER BY id',(driver,)).fetchall()
    post('accountant','/api/orders/'+oid+'/refund',{'amount_halalas':1000,'method':'card','reason':'تأكيد تنفيذ استرداد الشبكة الفعلي'})
    with db.tx() as c:
        assert c.execute('SELECT snapshot FROM closure_reports WHERE shift_id=%s',(driver,)).fetchone()==before
        assert c.execute('SELECT * FROM collections WHERE order_id=%s',(oid,)).fetchone()==collection
        assert c.execute('SELECT * FROM custody_events WHERE shift_id=%s ORDER BY id',(driver,)).fetchall()==custody
        refund=c.execute('SELECT * FROM refunds WHERE order_id=%s',(oid,)).fetchone()
        assert refund['shift_id'] is None and refund['collection_id']==collection['id']
        assert c.execute('SELECT status FROM shifts WHERE id=%s',(driver,)).fetchone()['status']=='closed'
    current=actor('accountant').get(path+'/journey').json()
    assert current['financial_case']['pending_amount_halalas']==0
    assert current['collections'][0]['amount_halalas']==current['refunds'][0]['amount_halalas']==1000
    post('accountant',path+'/resolve',{'reason':'مطابقة المبلغ ورد الشبكة الفعلي'})


@pytest.mark.postgres
def test_zero_collectible_complimentary_cancellation_does_not_create_impossible_refund_case(ops):
    _,post,order,_=ops;oid=order(total=0)
    with db.tx() as c:
        c.execute("UPDATE orders SET payment_status='not_required',snapshot=snapshot||%s WHERE id=%s",(db.json({'complimentary':{'kind':'hospitality'}}),oid))
    result=post('manager','/api/order-financial-cases/orders/'+oid+'/cancel',{'version':1,'reason':'إلغاء تجهيز الضيافة'})
    assert result['financial_case'] is None and result['payment_status']=='not_required'
