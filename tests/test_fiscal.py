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
                {'name': 'Brownies Caramel', 'quantity': 1, 'line_total_halalas': 550, 'units': []},
                {'name': 'Brownies Caramel Box', 'quantity': 1, 'line_total_halalas': 3400, 'units': []}]}}


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
    rendered = receipt(order, {'name':'KPS COFFEE'}, 'order',language)
    text = '\n'.join(x['text'] for x in rendered['lines'])
    assert fiscal.SELLER['legal_name'] in text and '311887526600003' in text
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
def fiscal_sale(client):
    uid = 'fiscal-owner-'+uuid4().hex
    oid = 'fiscal-order-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES(%s,'Fiscal test','owner','kps','[\"kps-branch-1\"]')", (uid,))
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,delivery_status,payment_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'kps','kps-branch-1',%s,'delivery','delivered','paid','cash',3950,0,3950,%s,'{}')", (oid,uid,db.json(sample()['snapshot'])))
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
