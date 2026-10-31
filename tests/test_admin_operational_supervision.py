"""Explicit GM history reads preserve branch boundaries and existing write teams.

Synthetic records and native session fixtures run only in the isolated PostgreSQL
schema supplied by conftest. No production user is identified or modified.
"""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
import base64
import io

import pytest
from PIL import Image
from app import auth, db, fiscal
from tests.test_core import _make_user, _authenticate


def test_admin_read_grants_are_explicit_active_and_denial_aware():
    principal={'id':'synthetic-admin','role':'admin','active':True,'brand_id':'a',
               'branch_ids':['a-1'],'extra_permissions':['orders_view','chat_view']}
    assert auth.has_admin_read_grant(principal,'orders_view')
    assert auth.has_admin_read_grant(principal,'chat_view')
    order={'id':'o','brand_id':'a','branch_id':'a-1','service':'pickup'}
    assert auth.can_access_order(principal,order,None)
    assert not auth.can_access_order(principal,{**order,'branch_id':'a-2'},None)
    assert not auth.can_access_order(principal,{**order,'brand_id':'b'},None)
    for change in ({'active':False},{'role':'driver'},{'extra_permissions':[]},
                   {'extra_permissions':[],'permissions':['orders_view']},
                   {'extra_permissions':[],'display_name':'محمد عبدالسميع'},
                   {'permission_denials':['orders_view']}):
        assert not auth.has_admin_read_grant({**principal,**change},'orders_view')
    assert not auth.has_admin_read_grant(principal,'orders_cancel_collected')
    assert not auth.has_admin_read_grant({**principal,'extra_permissions':[],
                                       '_message_capabilities':['chat_view','chat_reply','chat_manage']},'chat_view')


@pytest.fixture
def supervisor(client, monkeypatch):
    monkeypatch.setenv('KPS_PORTAL_HOST_ROUTING_ENABLED','false')
    monkeypatch.setenv('CENTRAL_OPERATIONS_ENABLED','false')
    user=_make_user('admin',branches=['kps-branch-1'])
    customer=_make_user('customer')
    owner=_make_user('owner')
    with db.tx() as c:
        c.execute('UPDATE users SET extra_permissions=%s,permissions=%s WHERE id=%s',
                  (db.json(['orders_view','chat_view']),db.json(['products_manage']),user['id']))
        c.execute("INSERT INTO brands(id,name) VALUES('supervision-other','Synthetic other brand')")
        c.execute("INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES('supervision-other-branch',1,'supervision-other-source','supervision-other','Synthetic other branch','Test','{}')")
    records={}
    for key,service,branch,brand in [('delivery','delivery','kps-branch-1','kps'),
                                   ('pickup','pickup','kps-branch-1','kps'),
                                   ('car','car','kps-branch-1','kps'),
                                   ('outside','delivery','kps-branch-2','kps'),
                                   ('other','delivery','supervision-other-branch','supervision-other')]:
        oid='TEST-supervision-'+key+'-'+uuid4().hex
        snapshot={'order_reference':'HISTORY-'+key,'branch_name':'Frozen source branch',
                  'items':[{'product_id':'kps-01','name':'Synthetic coffee','quantity':1,
                            'line_total_halalas':1000,'unit_price_halalas':1000,'units':[]}]}
        with db.tx() as c:
            records[key]=c.execute("""INSERT INTO orders(id,brand_id,branch_id,customer_id,service,
                preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,
                total_halalas,snapshot,customer,created_at) VALUES(%s,%s,%s,%s,%s,'ready',%s,'cash',
                1000,0,1000,%s,%s,'2021-01-02T10:00:00Z') RETURNING *""",
                (oid,brand,branch,customer['id'],service,'pending' if service=='delivery' else 'not_applicable',
                 db.json(snapshot),db.json({'name':'Synthetic customer','phone':'0501234567'}))).fetchone()
            db.event(c,oid,owner['id'],'order.created',{'note':'Synthetic historical evidence'})
    _authenticate(client,user)
    return client,user,customer,owner,records


@pytest.mark.postgres
def test_all_service_history_and_invoice_search_are_scoped_and_revocation_is_immediate(supervisor):
    client,user,customer,owner,orders=supervisor
    before=deepcopy(orders['delivery']['snapshot'])
    doc=fiscal.build(orders['delivery'],'TEST-ARCHIVED-INVOICE',datetime(2021,1,2,tzinfo=timezone.utc))
    doc['status']='issued'
    with db.tx() as c:
        c.execute("INSERT INTO fiscal_policy_decisions(id,brand_id,actor_id,decision) VALUES('TEST-history-policy','kps',%s,'{}')",(owner['id'],))
        c.execute("INSERT INTO fiscal_documents(number,order_id,kind,policy_id,document) VALUES(%s,%s,'invoice','TEST-history-policy',%s)",(doc['number'],orders['delivery']['id'],db.json(doc)))
    result=client.get('/api/orders?period=all&sort=newest&limit=2')
    assert result.status_code==200,result.text
    first=result.json();assert first['period']=={'all_history':True}
    assert len(first['orders'])==2 and first['next_offset']==2
    second=client.get('/api/orders?period=all&sort=newest&limit=2&offset=2').json()
    assert {o['id'] for o in first['orders']+second['orders']}=={orders[k]['id'] for k in ('delivery','pickup','car')}
    assert second['next_offset'] is None
    for term in (doc['number'],'HISTORY-delivery',str(orders['delivery']['order_no']).zfill(8),
                 str(orders['delivery']['order_no']).translate(str.maketrans('0123456789','٠١٢٣٤٥٦٧٨٩'))):
        found=client.get('/api/orders',params={'period':'all','search':term})
        assert found.status_code==200,found.text
        assert [o['id'] for o in found.json()['orders']]==[orders['delivery']['id']]
    assert not client.get('/api/orders?period=all&branch_id=kps-branch-2').json()['orders']
    assert client.get('/api/orders?period=all&start=2021-01-01').status_code==422
    detail=client.get('/api/orders/'+orders['delivery']['id'])
    assert detail.status_code==200,detail.text
    assert detail.json()['order']['issued_receipt']['fiscal_document']['number']==doc['number']
    assert any(e['details'].get('note')=='Synthetic historical evidence' for e in detail.json()['events'])
    for key in ('outside','other'):
        assert client.get('/api/orders/'+orders[key]['id']).status_code==404
        assert client.get('/api/order-financial-cases/orders/'+orders[key]['id']+'/journey').status_code==404
        assert client.get('/api/orders/'+orders[key]['id']+'/delivery-photos').status_code==404
    journey=client.get('/api/order-financial-cases/orders/'+orders['delivery']['id']+'/journey')
    assert journey.status_code==200,journey.text
    assert journey.json()['timeline']
    assert client.get('/api/orders/'+orders['delivery']['id']+'/delivery-photos').status_code==200
    assert client.get('/api/fiscal/orders/'+orders['delivery']['id']+'/documents').json()['documents'][0]['number']==doc['number']
    with db.tx() as c:
        assert c.execute('SELECT snapshot FROM orders WHERE id=%s',(orders['delivery']['id'],)).fetchone()['snapshot']==before
        current=c.execute('SELECT * FROM users WHERE id=%s',(user['id'],)).fetchone()
        assert current['role']=='admin' and 'products_manage' in auth.employee_permissions.effective(current)
        c.execute('UPDATE users SET permission_denials=%s WHERE id=%s',(db.json(['orders_view']),user['id']))
    assert client.get('/api/orders?period=all').status_code==403
    assert client.get('/api/orders/'+orders['delivery']['id']).status_code==403
    assert client.get('/api/order-financial-cases/orders/'+orders['delivery']['id']+'/journey').status_code==403
    assert client.get('/api/fiscal/orders/'+orders['delivery']['id']+'/documents').status_code==404


@pytest.mark.postgres
def test_supervisory_reads_cannot_issue_collect_refund_cancel_or_change_preparation(supervisor):
    client,user,customer,owner,orders=supervisor
    oid=orders['delivery']['id']
    def post(path,body):return client.post(path,json=body,headers={'Idempotency-Key':uuid4().hex})
    response=post('/api/fiscal/orders/'+oid+'/print',{})
    assert response.status_code==403,response.text
    assert response.json()['detail']['code']=='FISCAL_READ_ONLY'
    assert post('/api/orders/'+oid+'/preparation',{'version':1,'status':'preparing'}).status_code==403
    assert post('/api/orders/'+oid+'/collect',{'method':'cash','amount_halalas':1000,'tendered_halalas':1000}).status_code==403
    assert post('/api/orders/'+oid+'/refund',{'method':'cash','amount_halalas':1000,'reason':'Synthetic forbidden refund'}).status_code==403
    assert post('/api/order-financial-cases/orders/'+oid+'/cancel',{'version':1,'reason':'Synthetic forbidden cancellation'}).status_code==403
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM fiscal_documents WHERE order_id=%s',(oid,)).fetchone()
        assert not c.execute('SELECT 1 FROM collections WHERE order_id=%s',(oid,)).fetchone()
        assert not c.execute('SELECT 1 FROM refunds WHERE order_id=%s',(oid,)).fetchone()
        assert c.execute('SELECT preparation_status FROM orders WHERE id=%s',(oid,)).fetchone()['preparation_status']=='ready'


def conversation_row(order,customer,*,category='help',route='delivery',branchless=False):
    cid='TEST-conversation-'+uuid4().hex
    with db.tx() as c:
        c.execute("""INSERT INTO conversations(id,order_id,brand_id,branch_id,customer_id,route,context_service,category,created_at)
            VALUES(%s,%s,%s,%s,%s,%s,'delivery',%s,'2021-01-02T10:00:00Z')""",
            (cid,None if branchless else order['id'],order['brand_id'],None if branchless else order['branch_id'],customer['id'],route,category))
        c.execute('INSERT INTO chat_messages(id,conversation_id,sender_id,text) VALUES(%s,%s,%s,%s)',
                  (uuid4().hex,cid,customer['id'],'Synthetic historical conversation'))
    return cid


@pytest.mark.postgres
def test_conversation_history_does_not_expand_existing_routed_write_authority(supervisor):
    client,user,customer,owner,orders=supervisor
    delivery=conversation_row(orders['delivery'],customer)
    privacy=conversation_row(orders['pickup'],customer,category='privacy',route='privacy')
    outside=conversation_row(orders['outside'],customer)
    other=conversation_row(orders['other'],customer)
    branchless=conversation_row(orders['delivery'],customer,category='privacy',route='privacy',branchless=True)
    with db.tx() as c:
        c.execute("UPDATE app_settings SET settings=settings||%s WHERE brand_id='kps'",
                  (db.json({'customer_message_routing':{'delivery':[user['id']],'care':[]}}),))
        aid=uuid4().hex
        c.execute("INSERT INTO chat_attachments(id,conversation_id,uploader_id,content_type,size_bytes,sha256,data) VALUES(%s,%s,%s,'image/png',4,'synthetic',%s)",(aid,privacy,customer['id'],b'test'))
    result=client.get('/api/conversations')
    assert result.status_code==200,result.text
    assert {c['id'] for c in result.json()['conversations']}=={delivery,privacy}
    filtered=client.get('/api/conversations',params={'order_id':orders['pickup']['id']}).json()
    assert [c['id'] for c in filtered['conversations']]==[privacy]
    history=client.get('/api/conversations/'+privacy+'/messages')
    assert history.status_code==200,history.text
    assert history.json()['conversation']['can_reply'] is False
    assert history.json()['conversation']['can_manage'] is False
    assert history.json()['messages'][0]['text']=='Synthetic historical conversation'
    assert client.get('/api/chat-attachments/'+aid+'?context=work').content==b'test'
    own=client.get('/api/conversations/'+delivery+'/messages').json()['conversation']
    assert own['can_reply'] and own['can_manage']
    for cid in (outside,other,branchless):assert client.get('/api/conversations/'+cid+'/messages').status_code==404
    # The admin has global chat_reply/manage through the delivery response team,
    # but the new read grant cannot extend those writes to privacy conversations.
    for suffix,body in [('messages',{'text':'Must not be sent'}),('claim',{}),('status',{'status':'resolved'}),('transfer',{'target':'delivery','reason':'Must not be transferred'})]:
        blocked=client.post('/api/conversations/'+privacy+'/'+suffix,json=body,headers={'Idempotency-Key':uuid4().hex})
        assert blocked.status_code==404,blocked.text
    picture=io.BytesIO();Image.new('RGB',(4,4),'white').save(picture,'PNG')
    upload=client.post('/api/conversations/'+privacy+'/attachments',json={'content_type':'image/png','data_base64':base64.b64encode(picture.getvalue()).decode()},headers={'Idempotency-Key':uuid4().hex})
    assert upload.status_code==404,upload.text
    created=client.post('/api/conversations',json={'order_id':orders['car']['id'],'category':'privacy','details':'Must not create'},headers={'Idempotency-Key':uuid4().hex})
    assert created.status_code==403,created.text
    key=uuid4().hex;body={'text':'Allowed existing delivery response'}
    sent=client.post('/api/conversations/'+delivery+'/messages',json=body,headers={'Idempotency-Key':key})
    assert sent.status_code==200,sent.text
    with db.tx() as c:
        c.execute("UPDATE app_settings SET settings=settings||%s WHERE brand_id='kps'",
                  (db.json({'customer_message_routing':{'delivery':[],'care':[user['id']]}}),))
    # Still has chat_reply via care, and still reads delivery through supervision.
    # Replaying a previously successful write must recheck its canonical team.
    replay=client.post('/api/conversations/'+delivery+'/messages',json=body,headers={'Idempotency-Key':key})
    assert replay.status_code==404,replay.text
    assert client.get('/api/conversations/'+delivery+'/messages').json()['conversation']['can_reply'] is False
    assert client.get('/api/conversations/'+branchless+'/messages').status_code==200
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM chat_messages WHERE conversation_id=%s AND sender_id=%s',(delivery,user['id'])).fetchone()['n']==1
        c.execute('UPDATE users SET permission_denials=%s WHERE id=%s',(db.json(['chat_view']),user['id']))
    assert client.get('/api/conversations/'+privacy+'/messages').status_code==403
    assert client.get('/api/chat-attachments/'+aid+'?context=work').status_code==403


@pytest.mark.postgres
def test_conversation_pagination_keeps_order_and_brand_scope_on_every_page(supervisor):
    client,user,customer,owner,orders=supervisor
    target={conversation_row(orders['delivery'],customer) for _ in range(52)}
    unrelated=conversation_row(orders['pickup'],customer)
    outside=conversation_row(orders['outside'],customer)
    params={'order_id':orders['delivery']['id']}
    first=client.get('/api/conversations',params=params).json()
    assert len(first['conversations'])==50 and first['next_offset']==50
    second=client.get('/api/conversations',params={**params,'offset':50}).json()
    seen={c['id'] for c in first['conversations']+second['conversations']}
    assert seen==target and unrelated not in seen and outside not in seen
    assert len(second['conversations'])==2 and second['next_offset'] is None
