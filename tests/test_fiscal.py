import base64
from copy import deepcopy
from datetime import datetime, timezone
import pytest
from PIL import Image
import zxingcpp
from app import fiscal
from app.branch_printing import receipt

NOW = datetime(2026, 10, 15, 23, 30, tzinfo=timezone.utc)


def sample(method='cash', discount=0, delivery=0):
    return {'id': 'synthetic-fiscal-order', 'subtotal_halalas': 3950, 'discount_halalas': discount,
            'delivery_fee_halalas': delivery, 'total_halalas': 3950-discount+delivery,
            'payment_method': method, 'snapshot': {'items': [
                {'product_id': 'synthetic-brownie', 'name': 'Brownies Caramel', 'quantity': 1, 'line_total_halalas': 550, 'units': []},
                {'product_id': 'synthetic-brownie-box', 'name': 'Brownies Caramel Box', 'quantity': 1, 'line_total_halalas': 3400, 'units': []}]}}


def decode(text):
    # Independent byte-level decoder, intentionally not shared with the encoder.
    raw = base64.b64decode(text, validate=True)
    result = {}
    while raw:
        tag, length = raw[:2]
        assert tag not in result and len(raw) >= length+2
        result[tag] = raw[2:2+length].decode('utf-8')
        raw = raw[2+length:]
    return result


@pytest.mark.parametrize('method', ['cash', 'card'])
@pytest.mark.parametrize('discount,delivery', [(0,0), (500,600), (3950,600), (3950,0)])
def test_actual_symbol_matches_frozen_document(method, discount, delivery):
    order = sample(method, discount, delivery)
    before = deepcopy(order)
    doc = fiscal.build(order, 'PREVIEW-KPS-1', NOW)
    assert order == before
    qr = fiscal.qr(doc)
    matrix = qr['matrix']; size = len(matrix)
    for pixels in (384,544):
        image = Image.new('L',(size,size),255)
        image.putdata([0 if v=='1' else 255 for row in matrix for v in row])
        image = image.resize((pixels,pixels),Image.Resampling.NEAREST)
        result = zxingcpp.read_barcode(image)
        assert result and result.text == doc['qr_text'] == qr['text']
        assert decode(result.text) == {1: fiscal.SELLER['legal_name'], 2: '311887526600003',
            3: '2026-10-15T23:30:00Z', 4: fiscal.money(doc['total_halalas']), 5: fiscal.money(doc['tax_halalas'])}
    assert doc['total_halalas'] == order['total_halalas']
    assert doc['net_halalas']+doc['tax_halalas'] == doc['total_halalas']


def test_approved_example_and_certificate():
    doc = fiscal.build(sample(), 'PREVIEW-KPS-2', NOW)
    assert (doc['total_halalas'],doc['net_halalas'],doc['tax_halalas']) == (3950,3435,515)
    assert doc['seller']['postal_code'] == '39912'
    assert 'حي الصفاء' in doc['seller']['address'] and 'الدمام' not in doc['seller']['address']
    assert 'short_national_address' not in doc['seller'] # Certificate supplies no verified short code.


@pytest.mark.parametrize('values', [(-1,0,0), (1,2,0), (1,0,-1), (True,0,0), (1.5,0,0)])
def test_reject_invalid_money(values):
    with pytest.raises(ValueError): fiscal.totals(*values)


def test_full_refund_rounding_and_over_refund():
    doc = fiscal.build(sample(), 'PREVIEW-KPS-3', NOW)
    before = deepcopy(doc)
    prior=0; taxes=0
    for i,amount in enumerate([1,7,542,3400]):
        credit = fiscal.credit(doc,amount,prior,f'PREVIEW-CN-{i}',NOW,f'refund-{i}','Authorized refund')
        prior += amount; taxes += credit['tax_halalas']
        assert credit['original_number'] == doc['number']
        assert decode(credit['qr_text'])[4] == fiscal.money(amount)
    assert taxes == doc['tax_halalas'] and doc == before
    with pytest.raises(ValueError): fiscal.credit(doc,1,3950,'CN',NOW,'r','over-refund')


@pytest.mark.parametrize('language', ['ar','en'])
def test_preserved_thermal_renderer_has_supplier_tax_and_real_qr(language):
    doc = fiscal.build(sample(), 'PREVIEW-KPS-4', NOW)
    order = fiscal.print_order(doc)
    rendered = receipt(order, {'name':'فرع الاختبار'}, 'order',language)
    text = '\n'.join(x['text'] for x in rendered['lines'])
    brand = 'كبز قهوة مختصة' if language == 'ar' else 'KPS COFFEE'
    assert sum(line['text'] == brand for line in rendered['lines']) == 1
    assert '311887526600003' in text
    assert fiscal.SELLER['legal_name'] not in text
    assert '5.15' in text and '34.35' in text and '39.50' in text
    assert ('معاينة فقط' if language=='ar' else 'PREVIEW ONLY') in text
    assert next(x['matrix'] for x in rendered['lines'] if x['role']=='qr') == fiscal.qr(doc)['matrix']
    assert not any(x['role']=='qr' for x in receipt(order,{'name':'KPS COFFEE'},'preparation',language)['lines'])


def test_fail_closed_phase2_and_mismatched_saved_amounts():
    doc = fiscal.build(sample(),'P',NOW)
    with pytest.raises(ValueError): fiscal.encode_qr({**doc,'phase':2})
    assert fiscal.readiness()['official_issuance_enabled'] is False
    order = sample();order['total_halalas'] += 1
    with pytest.raises(ValueError): fiscal.build(order,'P',NOW)
    with pytest.raises(ValueError): fiscal.build(sample(),'P',NOW.replace(tzinfo=None))

from app import auth, db
from uuid import uuid4

@pytest.fixture
def fiscal_sale(client, request):
    branch = getattr(request, "param", "kps-branch-1")
    revision = branch.get('amendment_revision',0) if isinstance(branch,dict) else 0
    branch = branch.get('branch','kps-branch-1') if isinstance(branch,dict) else branch
    uid = 'fiscal-owner-'+uuid4().hex
    oid = 'fiscal-order-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES(%s,'Fiscal test','owner','kps','[\"kps-branch-1\"]')", (uid,))
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,delivery_status,payment_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer,amendment_revision) VALUES(%s,'kps',%s,%s,'delivery','delivered','paid','cash',3950,0,3950,%s,'{}',%s)", (oid,branch,uid,db.json(sample()['snapshot']),revision))
        user = c.execute('SELECT * FROM users WHERE id=%s',(uid,)).fetchone()
    client.app.dependency_overrides[auth.current_user]=lambda:user
    yield client,oid,user
    client.app.dependency_overrides.clear()

@pytest.mark.postgres
@pytest.mark.parametrize('method',['cash','card'])
def test_saved_original_retries_immutability_and_credits(fiscal_sale,method):
    client,oid,user=fiscal_sale
    def post(suffix,key):
        return client.post(f'/api/fiscal/orders/{oid}/{suffix}',headers={'Idempotency-Key':key})
    with db.tx() as c:
        c.execute('UPDATE orders SET payment_method=%s WHERE id=%s',(method,oid))
    first=post('preview','fiscal-test-0001')
    assert first.status_code==200,first.text
    original=first.json()['document']
    assert post('preview','fiscal-test-0001').json()==first.json()
    assert post('preview','fiscal-test-0002').json()==first.json()
    assert post('issue','fiscal-test-0003').status_code==409
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM fiscal_previews WHERE order_id=%s',(oid,)).fetchone()['n']==1
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='fiscal.preview_saved' AND entity_id=%s",(oid,)).fetchone()['n']==1
        c.execute("INSERT INTO collections(id,order_id,collector_id,method,amount_halalas) VALUES(%s,%s,%s,%s,3950)",('col-'+oid,oid,user['id'],method))
        for i,amount in enumerate([550,3400]):
            c.execute("INSERT INTO refunds(id,collection_id,order_id,method,amount_halalas,reason,authorized_by) VALUES(%s,%s,%s,%s,%s,'Authorized test refund',%s)", (f'refund-{i}-'+oid,'col-'+oid,oid,method,amount,user['id']))
    credits=post('credit-previews','fiscal-test-0004')
    assert credits.status_code==200,credits.text
    docs=credits.json()['documents']
    assert sum(x['tax_halalas'] for x in docs)==515
    assert sum(x['total_halalas'] for x in docs)==3950
    assert post('credit-previews','fiscal-test-0005').json()==credits.json()
    assert post('preview','fiscal-test-0006').json()['document']==original
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException):
        with db.tx() as c:c.execute("UPDATE fiscal_previews SET document='{}' WHERE order_id=%s",(oid,))
    with pytest.raises(psycopg.errors.RaiseException):
        with db.tx() as c:c.execute('DELETE FROM fiscal_previews WHERE order_id=%s',(oid,))

@pytest.mark.postgres
def test_preview_access_and_incomplete_sale(fiscal_sale):
    client,oid,user=fiscal_sale
    path=f'/api/fiscal/orders/{oid}/preview'
    with db.tx() as c:c.execute("UPDATE orders SET payment_status='unpaid' WHERE id=%s",(oid,))
    assert client.post(path,headers={'Idempotency-Key':'fiscal-test-0001'}).status_code==409
    for role in ['driver','customer','delivery_manager']:
        client.app.dependency_overrides[auth.current_user]=lambda:{**user,'role':role}
        assert client.post(path,headers={'Idempotency-Key':'fiscal-test-0002'}).status_code==403
    client.app.dependency_overrides[auth.current_user]=lambda:{**user,'brand_id':'other'}
    assert client.post(path,headers={'Idempotency-Key':'fiscal-test-0003'}).status_code==404

@pytest.mark.postgres
@pytest.mark.parametrize("fiscal_sale", ["kps-branch-11"], indirect=True)
def test_concurrent_preview_retries_and_sami_fulfilment(fiscal_sale):
    from concurrent.futures import ThreadPoolExecutor
    client,oid,user=fiscal_sale
    def prepare(i):
        return client.post(f'/api/fiscal/orders/{oid}/preview',headers={'Idempotency-Key':f'fiscal-concurrent-{i}'})
    with ThreadPoolExecutor(max_workers=4) as pool:responses=list(pool.map(prepare,range(4)))
    assert all(r.status_code==200 for r in responses),[r.text for r in responses]
    assert len({r.json()['document']['number'] for r in responses})==1
    assert all(r.json()['document']['seller']==fiscal.SELLER for r in responses)
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM fiscal_previews WHERE order_id=%s',(oid,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==0
    before=responses[0].json()['document']
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException, match='approved immutable revision'):
        with db.tx() as c:
            c.execute("UPDATE orders SET snapshot=jsonb_set(snapshot,'{items,0,name}','\"Changed operational text\"'::jsonb),amendment_revision=amendment_revision+1 WHERE id=%s",(oid,))
    with db.tx() as c:
        c.execute("UPDATE orders SET payment_method='card' WHERE id=%s",(oid,))
    assert prepare(5).json()['document']==before



def test_occasion_header_frozen_and_kept_out_of_fiscal_qr():
    order = sample()
    baseline = fiscal.build(order, 'PREVIEW-1', NOW)
    phrase = 'كل عام وأنتم بخير\nكبز تجمعنا'
    order['snapshot']['receipt_occasion_text'] = phrase
    doc = fiscal.build(order, 'PREVIEW-1', NOW)
    order['snapshot']['receipt_occasion_text'] = 'changed later'
    assert doc['receipt_occasion_text'] == phrase
    assert doc['qr_text'] == baseline['qr_text']
    printed = fiscal.print_order(doc)
    lines = receipt(printed, {'name': 'Test branch'}, 'order', 'ar')['lines']
    assert [line['text'] for line in lines if line['role'] == 'occasion'] == phrase.splitlines()
    assert [line['role'] for line in lines[:3]] == ['occasion', 'occasion', 'brand']
    assert not any(line['role'] == 'occasion' for line in receipt(printed, {'name': 'Test branch'}, 'preparation', 'ar')['lines'])
    assert not any(line['role'] == 'occasion' for line in receipt(fiscal.print_order(baseline), {'name': 'Test branch'}, 'order', 'ar')['lines'])


def test_occasion_style_is_frozen_and_used_by_device_renderer():
    order = sample()
    style = {'font':'Tahoma','size':26,'bold':False,'align':'right'}
    order['snapshot'].update(receipt_occasion_text='كل عام وأنتم بخير', receipt_occasion_style=style)
    doc = fiscal.build(order, 'PREVIEW-STYLE', NOW)
    style['size'] = 12
    assert doc['receipt_occasion_style']['size'] == 26
    rendered = receipt(fiscal.print_order(doc), {'name':'Test'}, 'order','ar')
    assert rendered['occasion_style'] == {'font':'Tahoma','size':26,'bold':False,'align':'right'}
    line = next(line for line in rendered['lines'] if line['role']=='occasion')
    assert line['align']=='right' and line['bold'] is False and line['center'] is False


def activate_phase1(client):
    response=client.post('/api/fiscal/policy',json={'enabled':True,'phase':'phase1',
        'evidence_reference':'Synthetic Phase 1 applicability evidence — isolated test only',
        'reason':'Isolated synthetic acceptance'},headers={'Idempotency-Key':'fiscal-policy-test-01'})
    assert response.status_code==200,response.text
    assert response.json()['official_issuance_enabled'] is True
    return response.json()


@pytest.mark.postgres
@pytest.mark.parametrize('method',['cash','card'])
def test_actual_issuance_and_credit_before_refund_atomicity(fiscal_sale,method):
    from app import fiscal_issuance
    import psycopg
    client,oid,user=fiscal_sale
    activate_phase1(client)
    with db.tx() as c:
        c.execute('UPDATE orders SET payment_method=%s WHERE id=%s',(method,oid))
        c.execute('INSERT INTO collections(id,order_id,collector_id,method,amount_halalas) VALUES(%s,%s,%s,%s,3950)',('actual-col-'+oid,oid,user['id'],method))
    def issue(key):
        return client.post(f'/api/fiscal/orders/{oid}/issue',headers={'Idempotency-Key':key})
    first=issue('actual-invoice-0001')
    assert first.status_code==200,first.text
    doc=first.json()['document']
    assert doc['status']=='issued' and doc['number'].startswith('KPS-')
    assert doc['seller']==fiscal.SELLER and doc['payment_method']==method
    assert issue('actual-invoice-0001').json()==first.json()
    assert issue('actual-invoice-0002').json()==first.json()
    # A missing refund causes deferred FK failure; the credit must roll back.
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.tx() as c:
            order=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone()
            fiscal_issuance.credit_before_refund(c,order,user,'absent-refund',550,'test rollback')
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM fiscal_documents WHERE order_id=%s AND kind='credit_note'",(oid,)).fetchone()['n']==0
    # Bypassing credit generation cannot commit a refund against an issued sale.
    with pytest.raises(psycopg.errors.RaiseException,match='matching fiscal credit'):
        with db.tx() as c:
            c.execute("INSERT INTO refunds(id,collection_id,order_id,method,amount_halalas,reason,authorized_by) VALUES(%s,%s,%s,%s,100,'bypass',%s)",('bypass-'+oid,'actual-col-'+oid,oid,method,user['id']))
    for i,amount in enumerate([550,3400]):
        with db.tx() as c:
            order=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone()
            rid=f'actual-refund-{i}-'+oid
            note=fiscal_issuance.credit_before_refund(c,order,user,rid,amount,'Accepted return')
            assert note['original_number']==doc['number'] and note['original_uuid']==doc['uuid']
            assert c.execute('SELECT 1 FROM refunds WHERE id=%s',(rid,)).fetchone() is None
            c.execute("INSERT INTO refunds(id,collection_id,order_id,method,amount_halalas,reason,authorized_by) VALUES(%s,%s,%s,%s,%s,'Accepted return',%s)",(rid,'actual-col-'+oid,oid,method,amount,user['id']))
    result=client.get(f'/api/fiscal/orders/{oid}/documents')
    assert result.status_code==200,result.text
    docs=result.json()['documents']
    assert len(docs)==3 and sum(d['tax_halalas'] for d in docs if d['kind']=='credit_note')==515
    assert sum(d['total_halalas'] for d in docs if d['kind']=='credit_note')==3950
    assert client.get(f'/api/orders/{oid}').json()['order']['issued_receipt']['fiscal_document']==doc
    for d in docs:
        symbol=fiscal.qr(d)['matrix']; side=len(symbol)
        image=Image.new('L',(side,side),255)
        image.putdata([0 if v=='1' else 255 for row in symbol for v in row])
        decoded=zxingcpp.read_barcode(image.resize((544,544),Image.Resampling.NEAREST))
        assert decoded and decode(decoded.text)=={1:d['seller']['legal_name'],2:d['seller']['vat_number'],3:d['issued_at'],4:fiscal.money(d['total_halalas']),5:fiscal.money(d['tax_halalas'])}
        for lang in ['ar','en']:
            lines=receipt(fiscal.print_order(d),{'name':'Test'},'order',lang)['lines']
            assert not any('PREVIEW ONLY' in x['text'] or 'معاينة فقط' in x['text'] for x in lines)
    for sql in ['DELETE FROM fiscal_documents WHERE order_id=%s',"UPDATE fiscal_documents SET document='{}' WHERE order_id=%s"]:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.tx() as c:c.execute(sql,(oid,))


@pytest.mark.postgres
def test_issuance_disabled_phase2_scope_policy_permissions_and_races(fiscal_sale):
    from concurrent.futures import ThreadPoolExecutor
    client,oid,user=fiscal_sale
    path=f'/api/fiscal/orders/{oid}/issue'
    assert client.post(path,headers={'Idempotency-Key':'disabled-issue-01'}).status_code==409
    for phase in ['phase2','unconfirmed']:
        r=client.post('/api/fiscal/policy',json={'enabled':True,'phase':phase,'reason':'test','evidence_reference':'test'},headers={'Idempotency-Key':'disabled-policy-'+phase})
        assert r.status_code==409
    for role in ['driver','delivery_manager','branch_manager','customer']:
        client.app.dependency_overrides[auth.current_user]=lambda:{**user,'role':role}
        assert client.post('/api/fiscal/policy',json={'reason':'test'},headers={'Idempotency-Key':'deny-policy-'+role}).status_code==403
    client.app.dependency_overrides[auth.current_user]=lambda:user
    activate_phase1(client)
    def call(i):return client.post(path,headers={'Idempotency-Key':f'concurrent-issued-{i}'})
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(call,range(4)))
    assert all(r.status_code==200 for r in results),[r.text for r in results]
    assert len({r.json()['document']['number'] for r in results})==1
    client.app.dependency_overrides[auth.current_user]=lambda:{**user,'brand_id':'other'}
    assert client.get(f'/api/fiscal/orders/{oid}/documents').status_code==404
    assert call(5).status_code==404

from test_operations import ops

@pytest.mark.postgres
@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
@pytest.mark.parametrize('method',['cash','card'])
def test_completed_driver_sale_automatically_issues_once_and_refunds(ops,method):
    actor,post,order,shifts=ops
    activate_phase1(actor('owner'))
    manager,driver=shifts()
    oid=order(total=3950)
    task=post('manager',f'/api/orders/{oid}/assign',{'driver_shift_id':driver})['task']['id']
    post('driver',f'/api/tasks/{task}/transition',{'status':'accepted'})
    with db.tx() as c:c.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s",(oid,))
    post('driver',f'/api/tasks/{task}/transition',{'status':'picked_up'})
    payment={'method':method,'amount_halalas':3950,'tendered_halalas':3950,'reference':'synthetic-terminal'}
    paid=post('driver',f'/api/orders/{oid}/collect',payment,key='fiscal-driver-payment')
    assert post('driver',f'/api/orders/{oid}/collect',payment,key='fiscal-driver-payment')==paid
    post('driver',f'/api/orders/{oid}/collect',payment,key='fiscal-driver-duplicate',status=409)
    with db.tx() as c:assert c.execute('SELECT count(*) n FROM fiscal_documents WHERE order_id=%s',(oid,)).fetchone()['n']==0
    post('driver',f'/api/tasks/{task}/transition',{'status':'delivered','proof':'synthetic delivery'},key='fiscal-driver-delivered')
    original=actor('owner').get(f'/api/fiscal/orders/{oid}/documents').json()['documents']
    assert len(original)==1 and original[0]['total_halalas']==3950 and original[0]['tax_halalas']==515
    # Completed orders leave the driver's current scope: preserve the canonical
    # 404 contract and verify both money and the original remain unchanged.
    post('driver',f'/api/orders/{oid}/collect',payment,key='fiscal-driver-payment',status=404)
    with db.tx() as c:
        collections=c.execute('SELECT id,amount_halalas FROM collections WHERE order_id=%s',(oid,)).fetchall()
        assert collections==[{'id':paid['collection_id'],'amount_halalas':3950}]
        assert c.execute('SELECT count(*) n FROM fiscal_documents WHERE order_id=%s',(oid,)).fetchone()['n']==1
    assert actor('owner').get(f'/api/fiscal/orders/{oid}/documents').json()['documents']==original
    refund={'amount_halalas':550,'method':method,'reason':'Authorized partial return','line_quantities':{}}
    first=post('manager',f'/api/orders/{oid}/refund',refund,key='fiscal-driver-refund')
    assert post('manager',f'/api/orders/{oid}/refund',refund,key='fiscal-driver-refund')==first
    post('manager',f'/api/orders/{oid}/refund',{**refund,'amount_halalas':3400},key='fiscal-driver-refund-rest')
    documents=actor('owner').get(f'/api/fiscal/orders/{oid}/documents').json()['documents']
    assert len(documents)==3
    assert [d for d in documents if d['kind']=='invoice']==original
    assert sum(d['tax_halalas'] for d in documents if d['kind']=='credit_note')==515
    assert sum(d['total_halalas'] for d in documents if d['kind']=='credit_note')==3950

@pytest.mark.postgres
def test_disabling_policy_preserves_original_and_blocks_credit_money_effects(fiscal_sale):
    from app import fiscal_issuance
    client,oid,user=fiscal_sale
    cfg=activate_phase1(client)
    first=client.post(f'/api/fiscal/orders/{oid}/issue',headers={'Idempotency-Key':'enable-then-disable-issue'})
    assert first.status_code==200,first.text
    response=client.post('/api/fiscal/policy',json={'enabled':False,'phase':'phase2','reason':'Phase 2 applicability discovered','expected_policy_id':cfg['policy_id']},headers={'Idempotency-Key':'disable-for-phase2'})
    assert response.status_code==200 and response.json()['official_issuance_enabled'] is False
    # Existing documents stay available and keep their original identity.
    assert client.post(f'/api/fiscal/orders/{oid}/issue',headers={'Idempotency-Key':'read-after-disable'}).json()==first.json()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failure:
        with db.tx() as c:
            order=c.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE',(oid,)).fetchone()
            fiscal_issuance.credit_before_refund(c,order,user,'not-recorded',100,'test')
    assert failure.value.detail['code']=='FISCAL_CREDIT_DISABLED'
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM fiscal_documents WHERE order_id=%s',(oid,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0

@pytest.mark.parametrize('language',['ar','en'])
def test_issued_device_header_and_footer_never_mark_invoice_as_preview(language):
    doc=fiscal.build(sample(),'KPS-SYNTHETIC',NOW)
    doc['status']='issued'  # Rendering unit fixture, not an actual issuance.
    lines=receipt(fiscal.print_order(doc),{'name':'Test'},'order',language)['lines']
    assert not any('PREVIEW ONLY' in x['text'] or 'معاينة فقط' in x['text'] for x in lines)
    assert any(('فاتورة ضريبية مبسطة' if language=='ar' else 'Simplified tax invoice')==x['text'] for x in lines)
    assert next(x['matrix'] for x in lines if x['role']=='qr')==fiscal.qr(doc)['matrix']


@pytest.mark.postgres
@pytest.mark.parametrize('fiscal_sale',[{'amendment_revision':1}],indirect=True)
def test_issued_refund_spans_multiple_collections_without_decimal_loss(fiscal_sale):
    client,oid,user=fiscal_sale
    activate_phase1(client)
    with db.tx() as c:
        c.execute("UPDATE orders SET payment_method='card' WHERE id=%s",(oid,))
        for index,amount in enumerate((1000,2950)):
            c.execute("INSERT INTO collections(id,order_id,collector_id,method,amount_halalas,amendment_revision) VALUES(%s,%s,%s,'card',%s,%s)",(f'multi-{index}-'+oid,oid,user['id'],amount,index))
    issued=client.post(f'/api/fiscal/orders/{oid}/issue',headers={'Idempotency-Key':'multi-issue'})
    assert issued.status_code==200,issued.text
    original=issued.json()['document']
    body={'amount_halalas':3950,'method':'card','reason':'Full refund across two collections','line_quantities':{'synthetic-brownie':1,'synthetic-brownie-box':1}}
    result=client.post(f'/api/orders/{oid}/refund',json=body,headers={'Idempotency-Key':'multi-refund'})
    assert result.status_code==200,result.text
    assert client.post(f'/api/orders/{oid}/refund',json=body,headers={'Idempotency-Key':'multi-refund'}).json()==result.json()
    docs=client.get(f'/api/fiscal/orders/{oid}/documents').json()['documents']
    assert [d for d in docs if d['kind']=='invoice']==[original]
    credits=[d for d in docs if d['kind']=='credit_note']
    assert len(credits)==2
    assert sorted(d['total_halalas'] for d in credits)==[1000,2950]
    assert sum(d['tax_halalas'] for d in credits)==515
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==2
        rows=c.execute('SELECT line_quantities FROM refunds WHERE order_id=%s',(oid,)).fetchall()
        assert sum(row['line_quantities'].get('synthetic-brownie',0) for row in rows)==1
        assert sum(row['line_quantities'].get('synthetic-brownie-box',0) for row in rows)==1
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s',(oid,)).fetchone()['payment_status']=='refunded'
