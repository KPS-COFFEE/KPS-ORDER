from copy import deepcopy
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import catalog, db, order_amendments as amendments
from test_operations import ops


def test_short_map_redirect(monkeypatch):
    import httpx
    seen=[]
    real=httpx.Client
    def handle(request):
        seen.append(str(request.url))
        return httpx.Response(302,headers={'location':'https://www.google.com/maps?q=28.427401,45.907025'})
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real(transport=httpx.MockTransport(handle),**kwargs))
    result=catalog.parse_map_url(catalog.MapURLBody(url='https://maps.app.goo.gl/SYNTHETIC'))
    assert result['location']=={'lat':28.427401,'lng':45.907025}
    assert len(seen)==1


@pytest.mark.parametrize('destination',['https://evil.example/maps?q=1,2','http://127.0.0.1/maps?q=1,2','https://www.google.com.evil.test/maps?q=1,2','https://www.google.com/maps/@28,45,12z'])
def test_short_map_never_fetches_untrusted_redirect(monkeypatch,destination):
    import httpx
    real=httpx.Client;seen=[]
    def handle(request):
        seen.append(str(request.url));return httpx.Response(302,headers={'location':destination})
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real(transport=httpx.MockTransport(handle),**kwargs))
    with pytest.raises(HTTPException):catalog.parse_map_url(catalog.MapURLBody(url='https://maps.app.goo.gl/SYNTHETIC'))
    assert len(seen)==1


def test_map_timeout_is_clear(monkeypatch):
    import httpx
    real=httpx.Client
    def handle(request):raise httpx.ReadTimeout('timeout')
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real(transport=httpx.MockTransport(handle),**kwargs))
    with pytest.raises(HTTPException) as error:catalog.parse_map_url(catalog.MapURLBody(url='https://maps.app.goo.gl/SYNTHETIC'))
    assert error.value.detail['code']=='MAP_SHORT_LINK_UNAVAILABLE'


@pytest.fixture
def amendment_case(ops,monkeypatch):
    actor,post,order,shifts=ops
    def quote(c,b,for_update=False):
        items=[{'product_id':x.product_id,'name':'قهوة تجربة','quantity':x.quantity,'unit_price_halalas':1000,'line_total_halalas':1000*x.quantity,'units':[],'note':x.note} for x in b.items]
        total=sum(x['line_total_halalas'] for x in items)
        return {'brand_id':'ops-brand','branch_id':'ops-branch','branch_name':'فرع اختبار','service':b.service,'items':items,'notes':b.notes,'subtotal_halalas':total,'delivery_fee_halalas':0,'total_halalas':total}
    monkeypatch.setattr(catalog,'quote_order',quote)
    def make(service='pickup',quantity=2,source=None):
        oid=order(service=service,total=1000*quantity,source=source,items=[{'product_id':'ops-product','name':'قهوة تجربة','quantity':quantity,'unit_price_halalas':1000,'line_total_halalas':1000*quantity,'units':[],'note':''}])
        with db.tx() as c:c.execute("UPDATE orders SET preparation_status='accepted' WHERE id=%s",(oid,))
        return oid
    def propose(oid,qty=1,who='customer'):
        o=actor(who).get('/api/orders/'+oid).json()['order']
        body={'version':o['version'],'items':[{'product_id':'ops-product','quantity':qty}],'customer':{'name':'اختبار'},'reason':'طلب العميل','notes':''}
        return post(who,'/api/order-amendments/orders/'+oid,body)['amendment_id'],body
    def decision(aid,action,who='branch',**kwargs):
        return post(who,'/api/order-amendments/'+aid+'/decision',{'action':action,**kwargs})
    return actor,post,make,propose,decision


@pytest.mark.postgres
def test_customer_amendment_replay_and_branch_receipt(amendment_case):
    actor,post,make,propose,decision=amendment_case;oid=make();aid,p=propose(oid)
    post('branch','/api/orders/'+oid+'/preparation',{'version':1,'status':'preparing'},status=409)
    decision(aid,'offer');key=uuid4().hex
    first=post('customer','/api/order-amendments/'+aid+'/decision',{'action':'accept'},key=key)
    assert first==post('customer','/api/order-amendments/'+aid+'/decision',{'action':'accept'},key=key)
    o=actor('customer').get('/api/orders/'+oid).json()['order']
    assert o['total_halalas']==1000 and o['amendment_revision']==1
    post('branch','/api/orders/'+oid+'/preparation',{'version':o['version'],'status':'preparing'},status=409)
    post('branch','/api/order-amendments/orders/'+oid+'/receipt',{'revision':1,'kind':'branch'})
    post('branch','/api/orders/'+oid+'/preparation',{'version':o['version'],'status':'preparing'})
    with db.tx() as c:
        revisions=c.execute('SELECT * FROM order_revisions WHERE order_id=%s',(oid,)).fetchall()
        assert len(revisions)==1 and revisions[0]['before_total']==2000
    with pytest.raises(Exception,match='append-only'):
        with db.tx() as c:c.execute('DELETE FROM order_revisions WHERE order_id=%s',(oid,))
    with pytest.raises(Exception,match='approved immutable revision'):
        with db.tx() as c:c.execute("UPDATE orders SET snapshot='{}' WHERE id=%s",(oid,))


@pytest.mark.postgres
def test_manager_offsite_consent_branch_feasibility_and_scope(amendment_case):
    actor,post,make,propose,decision=amendment_case;oid=make('delivery')
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='preparing' WHERE id=%s",(oid,))
    aid,p=propose(oid,who='manager')
    post('manager','/api/order-amendments/'+aid+'/decision',{'action':'offer'},status=409)
    decision(aid,'confirm_branch');decision(aid,'offer',who='manager')
    post('manager','/api/order-amendments/'+aid+'/decision',{'action':'accept'},status=422)
    decision(aid,'accept',who='manager',channel='whatsapp',note='وافق العميل على الإجمالي عبر واتساب')
    assert actor('other').get('/api/order-amendments/orders/'+oid).status_code==404
    with db.tx() as c:
        r=c.execute('SELECT * FROM order_revisions WHERE order_id=%s',(oid,)).fetchone()
        assert r['consent_channel']=='whatsapp' and r['consent_by']=='ops-manager'


@pytest.mark.postgres
@pytest.mark.parametrize('source', [None, 'delivery_center'])
def test_manager_amendment_preserves_original_order_source(amendment_case, ops, source):
    actor, post, make, propose, decision = amendment_case
    ops[3]()
    oid = make('delivery',source=source)
    expected = {'manager':int(source == 'delivery_center'), 'website':int(source is None)}
    path = '/api/orders?dispatch_workspace=true'
    assert actor('manager').get(path).json()['workspace_counts']['current']['sources'] == expected
    aid, _ = propose(oid, who='manager')
    decision(aid, 'confirm_branch')
    decision(aid, 'offer', who='manager')
    decision(aid, 'accept', who='manager', channel='whatsapp', note='وافق العميل')
    assert actor('manager').get(path).json()['workspace_counts']['current']['sources'] == expected
    with db.tx() as c:
        assert c.execute('SELECT snapshot FROM orders WHERE id=%s',(oid,)).fetchone()['snapshot'].get('order_source') == source


@pytest.mark.postgres
def test_paid_reduction_refunds_difference_without_rewriting_collection(amendment_case):
    actor,post,make,propose,decision=amendment_case;oid=make()
    collected=post('branch','/api/orders/'+oid+'/collect',{'method':'cash','amount_halalas':2000,'tendered_halalas':2000})
    aid,p=propose(oid);decision(aid,'offer');decision(aid,'accept',who='customer')
    view=actor('customer').get('/api/order-amendments/orders/'+oid).json()
    assert view['refund_due_halalas']==1000 and view['due_halalas']==0
    post('branch','/api/orders/'+oid+'/refund',{'method':'cash','amount_halalas':1001,'reason':'فرق التعديل','amendment_id':aid},status=422)
    result=post('branch','/api/orders/'+oid+'/refund',{'method':'cash','amount_halalas':1000,'reason':'رد فعلي','amendment_id':aid})
    assert result['payment_status']=='paid'
    with db.tx() as c:
        assert c.execute('SELECT amount_halalas FROM collections WHERE id=%s',(collected['collection_id'],)).fetchone()['amount_halalas']==2000
        assert c.execute('SELECT amendment_id FROM refunds WHERE order_id=%s',(oid,)).fetchone()['amendment_id']==aid
        c.execute("UPDATE orders SET preparation_status='completed',completed_at=now() WHERE id=%s",(oid,))
    report=actor('owner').get('/api/accounting/reports').json()
    assert report['totals']['net_sales']==1000
    assert report['totals']['total']==1000
    assert report['totals']['correct']==1
    post('owner','/api/orders/'+oid+'/refund',{'method':'cash','amount_halalas':1000,'reason':'استرداد كامل لاحق'})
    assert actor('owner').get('/api/orders/'+oid).json()['order']['payment_status']=='refunded'


@pytest.mark.postgres
def test_paid_increase_collects_only_difference(amendment_case):
    actor,post,make,propose,decision=amendment_case;oid=make(quantity=1)
    post('branch','/api/orders/'+oid+'/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':1000})
    aid,p=propose(oid,qty=2);decision(aid,'offer');decision(aid,'accept',who='customer')
    o=actor('branch').get('/api/orders/'+oid).json()['order']
    post('branch','/api/orders/'+oid+'/preparation',{'version':o['version'],'status':'cancelled','reason':'طلب العميل'},status=409)
    post('branch','/api/order-amendments/orders/'+oid+'/receipt',{'revision':1,'kind':'branch'})
    post('branch','/api/orders/'+oid+'/collect',{'method':'cash','amount_halalas':2000,'tendered_halalas':2000},status=422)
    post('branch','/api/orders/'+oid+'/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':1000})
    post('branch','/api/orders/'+oid+'/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':1000},status=409)
    with db.tx() as c:assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==2


@pytest.mark.postgres
def test_departed_items_and_expired_offer_are_blocked(amendment_case):
    actor,post,make,propose,decision=amendment_case;oid=make('delivery');aid,p=propose(oid)
    with db.tx() as c:c.execute("UPDATE order_amendments SET expires_at=now()-interval '1 second' WHERE id=%s",(aid,))
    post('manager','/api/order-amendments/'+aid+'/decision',{'action':'offer'},status=409)
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('manager','/api/order-amendments/orders/'+oid,p,status=409)


def test_frontend_integrates_isolated_editor():
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'public'
    for path in ('ops.html','index.html'):assert 'order-amendments.js' in (root/path).read_text()
    assert 'KPSAmendments.open' in (root/'ops.js').read_text()
    assert 'KPSAmendments.open' in (root/'customer.js').read_text()


@pytest.mark.postgres
def test_parallel_requests_have_one_active_winner(amendment_case):
    from concurrent.futures import ThreadPoolExecutor
    actor,post,make,propose,decision=amendment_case;oid=make()
    client=actor('customer')
    payload={'version':1,'items':[{'product_id':'ops-product','quantity':1}], 'customer':{'name':'اختبار'},'reason':'تعديل متزامن','notes':''}
    def submit(_):return client.post('/api/order-amendments/orders/'+oid,json=payload,headers={'Idempotency-Key':uuid4().hex}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(submit,range(2)))==[200,409]
    with db.tx() as c:assert c.execute('SELECT count(*) n FROM order_amendments WHERE order_id=%s',(oid,)).fetchone()['n']==1


@pytest.mark.postgres
def test_amended_order_and_original_survive_full_restore(amendment_case,tmp_path):
    import os,psycopg
    from psycopg import sql
    from app.exports import build_archive,restore_archive
    actor,post,make,propose,decision=amendment_case;oid=make();aid,p=propose(oid)
    decision(aid,'offer');decision(aid,'accept',who='customer')
    post('branch','/api/order-amendments/orders/'+oid+'/receipt',{'revision':1,'kind':'branch'})
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        contents,manifest=build_archive(c,full=True)
    assert manifest['counts']['order_revisions']==1
    assert actor('other').get('/api/order-amendments/orders/'+oid).status_code==404
    target='kps_restore_'+uuid4().hex;base=os.environ['TEST_DATABASE_URL']
    try:
        result=restore_archive(contents,target_url=base,target_schema=target,asset_directory=tmp_path/'assets')
        assert result['verified'] and result['counts']==manifest['counts']
        with psycopg.connect(base) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
            assert c.execute('SELECT before_total,after_total FROM order_revisions WHERE order_id=%s',(oid,)).fetchone()==(2000,1000)
            assert c.execute('SELECT total_halalas,amendment_revision FROM orders WHERE id=%s',(oid,)).fetchone()==(1000,1)
    finally:
        with psycopg.connect(base,autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(target)))
