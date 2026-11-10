import copy
import json
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from PIL import Image

from app import db, invoice_profiles as invoices
from app.branch_printing import receipt
from test_operations import ops


def sample(identifier='order_test1'):
    return {'id':identifier,'order_no':1,'created_at':datetime(2026,10,12,1,0,tzinfo=timezone.utc),
            'snapshot':{'items':[],'order_reference':'REF-'+identifier,'total_halalas':2300,
                        'receipt_profile':{'legal_name':'مؤسسة تجريبية'}},
            'total_halalas':2300,'payment_method':'cash','payment_status':'unpaid',
            'customer':{'name':'PRIVATE NAME','phone':'PRIVATE PHONE','address':'PRIVATE ADDRESS'}}


def test_qr_is_scannable_unique_stable_private_and_has_quiet_zone():
    import zxingcpp
    original=sample(); before=copy.deepcopy(original)
    qr=invoices.ordinary_qr(original)
    assert qr==invoices.ordinary_qr(original)
    assert qr['text']!=invoices.ordinary_qr(sample('order_test2'))['text']
    assert 'PRIVATE' not in qr['text']
    assert json.loads(qr['text'])['total_sar']=='23.00'
    matrix=qr['matrix']; size=len(matrix)
    assert all(set(row)=={'0'} for row in matrix[:4]+matrix[-4:])
    assert all(row.startswith('0000') and row.endswith('0000') for row in matrix)
    # Decode the actual generated symbol at both 58mm and 80mm printer widths.
    for pixels in (384,544):
        image=Image.new('L',(size,size),255)
        image.putdata([0 if cell=='1' else 255 for row in matrix for cell in row])
        image=image.resize((pixels,pixels),Image.Resampling.NEAREST)
        result=zxingcpp.read_barcode(image)
        assert result and result.text==qr['text']
    assert original==before


def test_qr_is_only_on_sale_receipt_not_preparation():
    order=sample()
    assert any(line['role']=='qr' for line in receipt(order,{'name':'Branch'},'order','en')['lines'])
    assert not any(line.get('role')=='qr' for line in receipt(order,{'name':'Branch'},'preparation','en')['lines'])


def test_owner_defaults_and_vat_validation():
    cfg=invoices.defaults('kps')
    assert cfg['assignments']['kps-branch-15']=='reem'
    assert cfg['assignments']['kps-branch-11']=='sami'
    assert cfg['entities']['reem']['vat_number']=='311887526600003'
    assert not cfg['entities']['sami']['vat_enabled']
    assert invoices.defaults('unrelated')['entities']=={}
    for enabled,number in [(True,''),(False,'311887526600003'),(True,'x'*15)]:
        with pytest.raises(HTTPException):
            invoices.validate(invoices.ProfileBody(entities={'test':{'legal_name':'Test','vat_enabled':enabled,'vat_number':number}},assignments={},reason='Testing',revision=''))


@pytest.mark.postgres
def test_profiles_owner_scope_audit_future_only_and_preview(ops):
    actor,post,order,shifts=ops
    with db.tx() as conn:
        conn.execute("INSERT INTO app_settings(brand_id,settings) VALUES('ops-brand','{}')")
    assert actor('manager').get('/api/invoice-profiles').status_code==403
    initial=actor('owner').get('/api/invoice-profiles').json()
    entity={'legal_name':'Test seller','vat_enabled':True,'vat_number':'311887526600003'}
    body={'entities':{'test':entity},'assignments':{'ops-branch':'test'},'reason':'Initial testing','revision':initial['revision']}
    post('manager','/api/invoice-profiles',body,status=403)
    post('owner','/api/invoice-profiles',{**body,'assignments':{'ops-branch-other':'test'}},status=422)
    post('owner','/api/invoice-profiles',body)
    post('owner','/api/invoice-profiles',body,status=409)
    with db.tx() as conn:
        frozen=invoices.capture(conn,'ops-brand','ops-branch')
        assert frozen['vat_enabled'] and frozen['issuance_status']=='not_enabled'
    doc=sample();doc['snapshot']['receipt_profile']=frozen
    old_qr=invoices.ordinary_qr(doc)
    latest=actor('owner').get('/api/invoice-profiles').json()
    body['revision']=latest['revision'];body['entities']['test']={'legal_name':'New seller','vat_enabled':False,'vat_number':''}
    post('owner','/api/invoice-profiles',body)
    assert invoices.ordinary_qr(doc)==old_qr
    with db.tx() as conn:
        assert not invoices.capture(conn,'ops-brand','ops-branch')['vat_enabled']
        assert conn.execute("SELECT count(*) AS n FROM audit_events WHERE action='invoice_profiles.changed'").fetchone()['n']==2
    preview=post('owner','/api/invoice-profiles/preview',{'entity':entity})
    assert preview['tax_halalas']==300 and preview['net_halalas']==2000
    assert preview['preview_only'] and not preview['tax_issuance_enabled']
    assert actor('customer').get('/api/invoice-profiles').status_code==403
