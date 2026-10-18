"""Menu file, financial variant and authorized draft import acceptance."""
from copy import deepcopy
from uuid import uuid4
import zipfile
import pytest
from fastapi import HTTPException
from app import catalog, db
from app.enbishan_catalog_import import prepare_rows, halalas
from app.menu_variants import selected_price, validated
from tools.enbishan_workbook import read_workbook, menu_rows
from tests.test_core import _make_user, _authenticate, _branch, _body
from tests.sms_policy_helpers import sms_login, sms_step_up


def rows():
    return [{'product_code': 'SYNTHETIC-JUICE', 'name': 'صنف اختبار فقط', 'name_en': 'TEST ONLY',
             'category': 'اختبار', 'size': size, 'flavour': flavour, 'price': price,
             'price_before': before, 'image': 'OWNER-IMAGE-PENDING.jpg'}
            for size, flavour, price, before in [('صغير', 'توت', '13', '15'), ('كبير', 'توت', '17', '19'),
                                                ('كبير', 'رمان', '18.50', '')]]


def choices(product, size, flavour):
    wanted = {'SIZE': size, 'FLAVOUR': flavour}
    return {g['code']: next(v['code'] for v in g['values'] if v['name'] == wanted[g['code']])
            for g in product['settings']['option_groups']}


def test_size_flavour_matrix_before_after_and_snapshot_use_server_prices():
    result = prepare_rows(rows())
    product = result['products'][0]
    assert result['ordering_enabled'] is False and product['settings']['enabled'] is False
    assert product['settings']['source_verified'] is False
    assert product['price_halalas'] == 1300
    assert result['unresolved_images'] == ['OWNER-IMAGE-PENDING.jpg']
    units = [catalog.Unit(options=choices(product, size, 'توت')) for size in ('صغير', 'كبير')]
    total, snapshot = catalog._price_units(product, catalog.OrderItem(product_id=product['id'], quantity=2, units=units))
    assert total == 3000
    assert [s['price_halalas'] for s in snapshot] == [1300, 1700]
    assert [s['compare_at_halalas'] for s in snapshot] == [1500, 1900]
    missing = choices(product, 'صغير', 'رمان')
    with pytest.raises(HTTPException) as error:
        selected_price(product, missing)
    assert error.value.detail['code'] == 'PRODUCT_VARIANT_UNAVAILABLE'
    approved = deepcopy(product)
    approved['settings']['source_verified'] = True
    approved['settings']['owner_approved_digest'] = catalog.commercial_digest(approved)
    assert catalog.source_approved(approved)
    approved['settings']['variant_price_matrix'][0]['compare_at_halalas'] += 100
    assert not catalog.source_approved(approved)


@pytest.mark.parametrize('bad', [True, None, 'NaN', 'Infinity', '-1', '0', '1.001', '100001'])
def test_money_cannot_be_missing_negative_boolean_or_silently_rounded(bad):
    with pytest.raises(ValueError):
        halalas(bad)


def test_arabic_money_and_decimal_values_are_exact():
    assert halalas('١٨٫٥٠') == 1850
    assert halalas('17.00') == 1700
    assert halalas('0.01') == 1


@pytest.mark.parametrize('change', ['duplicate', 'missing_axis', 'different_identity', 'bad_before', 'cross_brand', 'missing_code'])
def test_ambiguous_or_injected_file_rows_fail_closed(change):
    values = rows()
    if change == 'duplicate': values.append(values[0].copy())
    if change == 'missing_axis': values[0]['size'] = ''
    if change == 'different_identity': values[0]['name'] = 'Conflicting name'
    if change == 'bad_before': values[0]['price_before'] = '12'
    if change == 'cross_brand': values[0]['brand_id'] = 'kps'
    if change == 'missing_code': values[0]['product_code'] = ''
    with pytest.raises(ValueError): prepare_rows(values)


def test_plain_product_and_foreign_brand_matrix_do_not_inherit_pricing():
    plain = prepare_rows([{'product_code': 'ONE', 'name': 'TEST', 'category': 'TEST', 'price': '5'}])['products'][0]
    assert selected_price(plain, {})['price_halalas'] == 500
    plain['brand_id'] = 'kps'
    with pytest.raises(ValueError): validated(plain)
    legacy = {'id':'old', 'brand_id':'kps','name':'TEST','category':'TEST','price_halalas':1000,'settings':{}}
    assert validated(legacy) is None
    assert catalog._price_units(legacy, catalog.OrderItem(product_id='old', quantity=2))[0] == 2000


def workbook(path, formula=False):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="المنيو" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        cells = [('A','كود الصنف'),('B','اسم الصنف'),('C','القسم'),('D','السعر بعد'),('E','السعر قبل')]
        header = ''.join(f'<c r="{c}1" t="inlineStr"><is><t>{v}</t></is></c>' for c,v in cells)
        cells = [('A','SYNTHETIC'),('B','TEST ONLY'),('C','TEST'),('D','13'),('E','15')]
        data = ''.join(f'<c r="{c}2" t="inlineStr">'+('<f>1+1</f>' if formula and c=='D' else '')+f'<is><t>{v}</t></is></c>' for c,v in cells)
        z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row>'+header+'</row><row>'+data+'</row></sheetData></worksheet>')


def test_actual_xlsx_is_inspected_compiled_and_never_applied(tmp_path):
    path = tmp_path/'SYNTHETIC.xlsx';workbook(path)
    inspected = read_workbook(path)
    data = menu_rows(inspected, 'المنيو')
    assert prepare_rows(data)['products'][0]['price_halalas'] == 1300
    assert inspected['ordering_enabled'] is False and len(inspected['source_sha256']) == 64
    path = tmp_path/'FORMULA.xlsx';workbook(path, formula=True)
    with pytest.raises(ValueError, match='formulas'): read_workbook(path)


@pytest.mark.postgres
def test_owner_review_sms_scope_draft_import_replay_and_stale_preview(client, monkeypatch):
    kps = _make_user('owner');_authenticate(client,kps)
    body = {'source_sha256':'a'*64, 'rows':rows()}
    assert client.post('/api/admin/enbishan-menu/preview',json=body).status_code == 403
    with db.tx() as c:
        c.execute("INSERT INTO brands(id,name) VALUES('enbishan','TEST ONLY')")
        c.execute("INSERT INTO app_settings(brand_id,settings) VALUES('enbishan','{\"employee_sms_rollout\":{\"mode\":\"active\"}}')")
    owner = _make_user('owner',brand='enbishan')
    sms_login(client,owner,monkeypatch)
    preview = client.post('/api/admin/enbishan-menu/preview',json=body)
    assert preview.status_code == 200,preview.text
    values = preview.json()
    apply = {**body, 'reviewed_digest':values['catalog_digest'], 'expected_catalog_digest':values['expected_catalog_digest']}
    path = '/api/admin/enbishan-menu/apply';headers={'Idempotency-Key':'SYNTHETIC-menu-import-0001'}
    assert client.post(path,json=apply,headers=headers).status_code == 403
    sms_step_up(client,owner,monkeypatch)
    saved = client.post(path,json=apply,headers=headers)
    assert saved.status_code == 200,saved.text
    assert saved.json()['imported_drafts'] == 1 and saved.json()['ordering_enabled'] is False
    assert client.post(path,json=apply,headers=headers).json() == saved.json()
    assert client.post(path,json=apply,headers={'Idempotency-Key':'SYNTHETIC-menu-import-stale'}).status_code == 409
    product = values['products'][0]
    with db.tx() as c:
        row=c.execute('SELECT * FROM products WHERE id=%s',(product['id'],)).fetchone()
        assert row['brand_id']=='enbishan' and row['settings']['lifecycle']=='draft'
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='enbishan.menu.imported_draft'").fetchone()['n']==1
    approved=client.patch('/api/admin/products/'+product['id'],json={'settings':{'source_verified':True,'lifecycle':'active'}},headers={'Idempotency-Key':'SYNTHETIC-menu-approve-0001'})
    assert approved.status_code==200,approved.text
    fresh=client.post('/api/admin/enbishan-menu/preview',json=body).json()
    apply['expected_catalog_digest']=fresh['expected_catalog_digest']
    assert client.post(path,json=apply,headers={'Idempotency-Key':'SYNTHETIC-active-menu-denied'}).status_code==409
    with db.tx() as c:
        c.execute('UPDATE users SET active=false WHERE id=%s',(owner['id'],))
    assert client.post(path,json=apply,headers=headers).status_code in (401,403)
