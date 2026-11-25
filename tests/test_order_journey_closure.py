"""Release regressions for both-shift, evidence-bound order interventions."""
from uuid import uuid4
import pytest
from app import db
from test_operations import ops
from test_order_amendments import amendment_case
from test_morning_manager_context_postgres import morning_manager
from test_order_interventions import controlled_order, customer_request, print_job

pytestmark=pytest.mark.postgres


def request(post,oid,**extra):
    return post('manager',f'/api/order-interventions/orders/{oid}',{
        'kind':'cancel','reason_code':'customer_cancel','version':1,**extra})['id']


def approve(post,who,oid,iid,*,status=200,key=None,**extra):
    return post(who,f'/api/order-interventions/orders/{oid}/{iid}/decision',{
        'action':'approve','reason_code':'customer_cancel','contact_result':'agreed','version':1,**extra},status=status,key=key)


@pytest.mark.parametrize('period',['morning','evening'])
def test_site_evidence_auto_links_only_matching_order_and_keeps_shift_dispatch_policy(ops,period):
    actor,post,_,_=ops
    oid=controlled_order(snapshot_overrides={'delivery_period':period,'delivery_workflow':'branch_fulfillment'})
    if period=='morning':
        with db.tx() as c:c.execute("UPDATE brands SET settings=settings||%s WHERE id='ops-brand'",(db.json({'morning_delivery':{'manager_user_ids':['ops-manager']}}),))
    foreign=controlled_order()
    wrong_rid=customer_request(post,foreign)
    post('manager',f'/api/order-interventions/orders/{oid}',{
        'kind':'cancel','reason_code':'customer_cancel','version':1,'customer_request_id':wrong_rid},status=422)
    rid=customer_request(post,oid)
    with db.tx() as c:before=c.execute('SELECT snapshot FROM orders WHERE id=%s',(oid,)).fetchone()['snapshot']
    iid=request(post,oid)
    with db.tx() as c:
        details=c.execute('SELECT details FROM order_events WHERE id=%s',(iid,)).fetchone()['details']
        source=c.execute('SELECT details FROM order_events WHERE id=%s',(rid,)).fetchone()['details']
        assert details['customer_request_id']==rid
        assert details['evidence_message_id']==source['evidence_message_id']
        assert c.execute('SELECT snapshot FROM orders WHERE id=%s',(oid,)).fetchone()['snapshot']==before
        assert c.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s',(oid,)).fetchone()['n']==0
    approve(post,'manager',oid,iid)
    with db.tx() as c:
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='cancelled'


@pytest.mark.parametrize('period',['morning','evening'])
def test_failed_whatsapp_upload_escalates_and_high_authority_executes_without_image_once(ops,period):
    actor,post,_,_=ops
    oid=controlled_order(source='delivery_center',snapshot_overrides={'delivery_period':period})
    print_job(oid)
    base=f'/api/order-interventions/orders/{oid}'
    post('manager',base,{'kind':'cancel','reason_code':'customer_cancel','version':1},status=422)
    iid=request(post,oid,attachment_unavailable=True,attachment_unavailable_reason='فشل رفع الصورة بسبب الاتصال')
    saved=actor('manager').get(base).json()['requests'][0]
    assert saved['details']['attachment_unavailable']
    assert saved['details']['pending_role']=='owner_or_cancel_delegate'
    approve(post,'manager',oid,iid,status=403)
    first=approve(post,'owner',oid,iid,key='exception-decision')
    assert first['proof']['evidence_status']=='exception_approved'
    assert first==approve(post,'owner',oid,iid,key='exception-decision')
    with db.tx() as c:
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='cancelled'
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='intervention.executed'",(oid,)).fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM chat_attachments a JOIN conversations v ON v.id=a.conversation_id WHERE v.order_id=%s",(oid,)).fetchone()['n']==0


def test_attachment_exception_without_print_still_needs_manager_approval(ops):
    actor,post,_,_=ops;oid=controlled_order(source='delivery_center')
    iid=request(post,oid,attachment_unavailable=True,attachment_unavailable_reason='تعذر التقاط الصورة')
    approve(post,'manager',oid,iid,status=403)
    approve(post,'owner',oid,iid)


def test_invoice_issuance_locks_cancellation_before_any_print_command(ops):
    actor,post,_,_=ops;oid=controlled_order();pid=uuid4().hex;number='TEST-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO fiscal_policy_decisions(id,brand_id,actor_id,decision) VALUES(%s,'ops-brand','ops-owner','{}')",(pid,))
        document={'number':number,'order_id':oid,'kind':'invoice','phase':'1','status':'issued','total_halalas':1000,'net_halalas':870,'tax_halalas':130}
        c.execute("INSERT INTO fiscal_documents(number,order_id,kind,policy_id,document) VALUES(%s,%s,'invoice',%s,%s)",(number,oid,pid,db.json(document)))
        assert c.execute('SELECT count(*) n FROM branch_print_jobs WHERE order_id=%s',(oid,)).fetchone()['n']==0
    customer_request(post,oid);iid=request(post,oid)
    assert actor('manager').get(f'/api/order-interventions/orders/{oid}').json()['printed']
    approve(post,'manager',oid,iid,status=403)
    approve(post,'owner',oid,iid)
    with db.tx() as c:
        assert c.execute('SELECT document FROM fiscal_documents WHERE number=%s',(number,)).fetchone()['document']==document


def test_changed_order_version_rejects_old_request_even_if_new_version_submitted(ops):
    actor,post,_,_=ops;oid=controlled_order();customer_request(post,oid);iid=request(post,oid)
    with db.tx() as c:c.execute('UPDATE orders SET version=version+1 WHERE id=%s',(oid,))
    approve(post,'owner',oid,iid,version=2,status=409)
    with db.tx() as c:
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='ready'
        assert c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s AND event_type='intervention.approved'",(oid,)).fetchone()['n']==0


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
def test_real_morning_header_authorizes_scoped_intervention_without_dispatch_changes(morning_manager,monkeypatch):
    client,ids,before=morning_manager
    monkeypatch.setenv('DELIVERY_INTERVENTIONS_ENFORCED','true')
    with db.tx() as c:
        original=c.execute('SELECT snapshot FROM orders WHERE id=%s',(ids['morning'],)).fetchone()['snapshot']
        tasks=c.execute('SELECT id,order_id,driver_id,status FROM driver_tasks ORDER BY id').fetchall()
    path='/api/order-interventions/orders/'+ids['morning']
    response=client.post(path,json={'kind':'problem','reason_code':'wrong_address','version':1},headers={'Idempotency-Key':uuid4().hex})
    assert response.status_code==200,response.text
    detail=client.get(path);assert detail.status_code==200,detail.text
    assert detail.json()['enabled']
    foreign=client.post('/api/order-interventions/orders/'+ids['evening'],json={'kind':'problem','reason_code':'wrong_address','version':1},headers={'Idempotency-Key':uuid4().hex})
    assert foreign.status_code==404,foreign.text
    with db.tx() as c:
        assert c.execute('SELECT snapshot FROM orders WHERE id=%s',(ids['morning'],)).fetchone()['snapshot']==original
        assert c.execute('SELECT id,order_id,driver_id,status FROM driver_tasks ORDER BY id').fetchall()==tasks
        assert c.execute("SELECT permission_denials FROM users WHERE id='ops-manager'").fetchone()['permission_denials']==before['permission_denials']


@pytest.mark.parametrize('attempt',range(5))
def test_driver_pickup_and_cancellation_hold_serialize_on_same_order(ops,attempt):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import base64,io
    from PIL import Image
    from fastapi import HTTPException
    from starlette.requests import Request
    from app import operations,order_interventions
    actor,post,_,shifts=ops;_,sid=shifts();oid=controlled_order()
    task=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':sid})['task']['id']
    post('driver',f'/api/tasks/{task}/transition',{'status':'accepted'})
    with db.tx() as c:
        c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
        version=c.execute('SELECT version FROM orders WHERE id=%s',(oid,)).fetchone()['version']
        manager=c.execute("SELECT * FROM users WHERE id='ops-manager'").fetchone()
        driver=c.execute("SELECT * FROM users WHERE id='ops-driver'").fetchone()
    image=io.BytesIO();Image.new('RGB',(12,12),'white').save(image,'PNG')
    response=actor('driver').post(f'/api/tasks/{task}/photos',json={'stage':'picked_up','data_base64':base64.b64encode(image.getvalue()).decode(),'content_type':'image/png'},headers={'Idempotency-Key':uuid4().hex})
    assert response.status_code==200,response.text
    photo=response.json()['id'];barrier=Barrier(2)
    def pickup():
        barrier.wait()
        try:
            operations.task_transition(task,operations.TaskTransition(status='picked_up',photo_id=photo),
                Request({'type':'http','headers':[(b'idempotency-key',uuid4().hex.encode())]}),driver)
            return 200
        except HTTPException as exc:return exc.status_code
    def hold():
        barrier.wait()
        try:
            order_interventions.create(oid,order_interventions.RequestBody(kind='cancel',reason_code='no_answer',version=version),manager,uuid4().hex)
            return 200
        except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(pickup);second=pool.submit(hold);pickup_status,hold_status=first.result(),second.result()
    assert hold_status in (200,409)
    assert pickup_status in (200,409)
    assert 200 in (hold_status,pickup_status)
    if hold_status==409:
        assert pickup_status==200
        with db.tx() as c:current_version=c.execute('SELECT version FROM orders WHERE id=%s',(oid,)).fetchone()['version']
        order_interventions.create(oid,order_interventions.RequestBody(kind='cancel',reason_code='no_answer',version=current_version),manager,uuid4().hex)
    with db.tx() as c:
        row=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        event=c.execute("SELECT created_at FROM order_events WHERE order_id=%s AND event_type='intervention.requested'",(oid,)).fetchone()
        saved=c.execute('SELECT picked_up_at FROM driver_tasks WHERE id=%s',(task,)).fetchone()
        if saved['picked_up_at'] is not None:assert saved['picked_up_at']<=event['created_at']
        assert row['preparation_status']!='cancelled'
    with pytest.raises(HTTPException) as blocked:
        operations.task_transition(task,operations.TaskTransition(status='picked_up',photo_id=photo),
            Request({'type':'http','headers':[(b'idempotency-key',uuid4().hex.encode())]}),driver)
    assert blocked.value.status_code==409


@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
@pytest.mark.parametrize('tamper',[False,True])
def test_issued_amendment_requires_director_exact_approval_then_links_correction(ops,amendment_case,tamper):
    from datetime import datetime,timezone
    from copy import deepcopy
    from app import fiscal,fiscal_issuance
    actor,post,_,_=ops
    oid=amendment_case[2]('delivery',quantity=1);pid='policy-'+uuid4().hex;number='TEST-'+uuid4().hex
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        original=fiscal.build(o,number,datetime.now(timezone.utc));original.update(status='issued',uuid=uuid4().hex,policy_id=pid)
        c.execute("INSERT INTO fiscal_policy_decisions(id,brand_id,actor_id,decision) VALUES(%s,'kps','ops-owner',%s)",(pid,db.json({'enabled':True,'phase':'unconfirmed','issuance_mode':'owner_requested'})))
        c.execute("INSERT INTO fiscal_documents(number,order_id,kind,policy_id,document) VALUES(%s,%s,'invoice',%s,%s)",(number,oid,pid,db.json(original)))
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,extra_permissions) VALUES('ops-director','محمد عبدالسميع','admin','kps','[\"kps-branch-1\"]',%s)",(db.json(['orders_view','orders_cancel_after_print']),))
    body={'version':1,'items':[{'product_id':'ops-product','quantity':2}],'customer':{'name':'اختبار'},'reason':'زيادة عدد القهوة بطلب العميل'}
    aid=post('customer',f'/api/order-amendments/orders/{oid}',body)['amendment_id']
    inbox=actor('director').get('/api/order-amendments/inbox')
    assert inbox.status_code==200,inbox.text
    assert any(row['amendment_id']==aid for row in inbox.json()['orders'])
    path='/api/order-amendments/'+aid+'/decision'
    post('manager',path,{'action':'offer'},status=403)
    post('director',path,{'action':'offer','note':'جهز القهوة الإضافية وسلمها مع نفس الطلب'})
    with db.tx() as c:
        approval=c.execute("SELECT details FROM order_events WHERE order_id=%s AND event_type='amendment.manager_approved'",(oid,)).fetchone()['details']
        assert approval['amendment_id']==aid and approval['after_total_halalas']==2000
        if tamper:
            proposed=c.execute('SELECT proposal FROM order_amendments WHERE id=%s',(aid,)).fetchone()['proposal']
            changed=deepcopy(proposed);changed['customer']['address']='تغيير إضافي غير معتمد'
            c.execute('UPDATE order_amendments SET proposal=%s WHERE id=%s',(db.json(changed),aid))
    result=post('customer',path,{'action':'accept'},status=409 if tamper else 200)
    with db.tx() as c:
        assert fiscal_issuance.original(c,oid)==original
        saved=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        documents=c.execute("SELECT kind,document FROM fiscal_documents WHERE order_id=%s AND kind!='invoice'",(oid,)).fetchall()
        if tamper:
            assert result['detail']['code']=='AMENDMENT_APPROVAL_STALE'
            assert saved['total_halalas']==1000 and saved['amendment_revision']==0 and documents==[]
        else:
            assert result['status']=='applied' and saved['total_halalas']==2000
            assert len(documents)==1 and documents[0]['kind']=='debit_note'
            assert documents[0]['document']['amendment_id']==aid
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0

    if not tamper:
        _,sid=ops[3]()
        with db.tx() as c:
            c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by) VALUES(%s,%s,'ops-driver',%s,'assigned','ops-manager')",(uuid4().hex,oid,sid))
        driver_view=actor('driver').get('/api/order-amendments/orders/'+oid)
        assert driver_view.status_code==200,driver_view.text
        assert driver_view.json()['operational_instructions']=='جهز القهوة الإضافية وسلمها مع نفس الطلب'
        customer_view=actor('customer').get('/api/order-amendments/orders/'+oid)
        assert customer_view.status_code==200,customer_view.text
        assert customer_view.json()['operational_instructions'] is None
