"""Channel seller, immutable snapshots, private documents and reviewed allocations."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
import pytest
from fastapi import HTTPException
from app import channel_commercial as channel, invoice_profiles, catalog, auth, db
from app.internal_documents import agreement_html
from app.branch_printing import receipt
from test_invoice_profiles import sample

QUOTE = {'subtotal_halalas':2000,'delivery_fee_halalas':300,'total_halalas':2300}

@pytest.mark.parametrize('branch,beneficiary',[('kps-branch-1','reem'),('kps-branch-11','sami')])
def test_channel_seller_independent_of_branch_and_preserves_inputs(branch,beneficiary):
    config=invoice_profiles.defaults('kps');before=deepcopy(config)
    commercial=channel.capture(config,branch,QUOTE,'test-owner')
    profile=invoice_profiles.capture(None,'kps',branch,brand_settings={'invoice_profiles':config},channel='kps_order')
    assert config==before
    assert profile['entity_id']=='reem' and profile['registration_number']=='7037181620'
    assert profile['vat_number']=='311887526600003' and profile['short_national_address']=='EDPC7048'
    assert commercial['settlement_beneficiary']==beneficiary and commercial['branch_owner']==beneficiary
    assert commercial['fulfilment_branch']==branch and commercial['tax_invoice_issuer']=='reem'
    assert commercial['settlement_amount_halalas']==2000 and commercial['delivery_fee_halalas']==300
    assert commercial['tax_halalas']==300 and commercial['delivery_revenue_beneficiary']=='reem'
    assert commercial['audit_actor']=='test-owner' and not commercial['tax_document_issued']
    public=catalog.customer_quote_view({**QUOTE,'channel_commercial':commercial})
    assert 'channel_commercial' not in public and config==before

@pytest.mark.parametrize('key,value',[('vat_number',''),('vat_enabled',False),('legal_name','Different seller')])
def test_conflicting_persisted_channel_seller_fails_closed(key,value):
    config=invoice_profiles.defaults('kps');config['entities']['reem'][key]=value
    with pytest.raises(HTTPException) as exc:channel.capture(config,'kps-branch-1',QUOTE,'test-owner')
    assert exc.value.status_code==409


def test_unknown_owner_and_unbalanced_amounts_fail_closed():
    config=invoice_profiles.defaults('kps')
    with pytest.raises(HTTPException):channel.capture(config,'unknown',QUOTE,'test-owner')
    with pytest.raises(HTTPException):channel.capture(config,'kps-branch-1',{**QUOTE,'total_halalas':999},'test-owner')


def test_amended_amounts_preserve_original_and_frozen_beneficiary():
    old=channel.capture(invoice_profiles.defaults('kps'),'kps-branch-11',QUOTE,'original-actor');before=deepcopy(old)
    new=channel.amounts(old,{**QUOTE,'subtotal_halalas':3150,'total_halalas':3450})
    assert old==before and new['settlement_beneficiary']=='sami'
    assert new['tax_halalas']==450 and new['settlement_amount_halalas']==3150

@pytest.mark.parametrize('lang',['ar','en'])
@pytest.mark.parametrize('claimed_status',['not_enabled','enabled','fiscal_ready'])
def test_no_snapshot_flag_can_enable_tax_issuance(lang,claimed_status):
    order=sample();order['snapshot']['receipt_profile']={**channel.REEM,'issuance_status':claimed_status,'tax_issuance_enabled':True}
    text=' '.join(x['text'] for x in receipt(order,{'name':'Test branch'},'order',lang)['lines'])
    assert ('ليست فاتورة ضريبية' if lang=='ar' else 'not a tax invoice') in text
    assert 'Simplified Tax Invoice' not in text and 'فاتورة ضريبية مبسطة' not in text
    assert 'KPS_ORDER_RECEIPT_NOT_TAX_INVOICE' in invoice_profiles.ordinary_qr(order)['text']


def test_agreement_full_identity_dates_rtl_and_no_old_signatures():
    html=agreement_html()
    for value in (channel.REEM['legal_name'],channel.SAMI_NAME,channel.SAMI_CR,channel.REEM['registration_number'],channel.REEM['vat_number'],'EDPC7048','5323 حي أحد','الدمام 32263'):
        assert value in html
    assert html.count('15-10-2026')==2 and html.count('<table dir="rtl"')==2
    assert 'نهائية لإعادة التوقيع' in html and 'غير مخصص للنشر للعملاء' in html
    assert html.count('<img ')==1 and 'data:image/png;base64,' in html


def test_social_colors_and_no_replacement_brand_asset():
    root=Path(__file__).resolve().parents[1]
    for name,colors in {'whatsapp':['#25D366'],'instagram':['linearGradient','#D62976'],'snapchat':['#FFFC00'],'tiktok':['#25F4EE','#FE2C55']}.items():
        svg=(root/f'public/assets/social/{name}.svg').read_text()
        assert all(c in svg for c in colors)

@pytest.fixture
def channel_client(client):
    users={}
    with db.tx() as c:
        for role in ('owner','customer','delivery_manager','branch_manager','driver','accountant'):
            uid='channel-'+role
            # Match the existing accounting persona; accountant is not a DB role.
            stored_role='admin' if role=='accountant' else role
            permissions=['accounting_reports','accounting_review'] if role=='accountant' else []
            c.execute('INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions) VALUES(%s,%s,%s,\'kps\',%s,%s)',(uid,uid,stored_role,db.json(['kps-branch-11']),db.json(permissions)))
            users[role]={'id':uid,'display_name':uid,'role':stored_role,'permissions':permissions,'brand_id':'kps','branch_ids':['kps-branch-11'],'active':True}
        commercial=channel.capture(invoice_profiles.defaults('kps'),'kps-branch-11',QUOTE,users['owner']['id'])
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES('channel-order','kps','kps-branch-11','channel-customer','pickup','ready','not_applicable','cash',2000,300,2300,%s,'{}')",(db.json({**QUOTE,'items':[],'channel_commercial':commercial}),))
        c.execute("INSERT INTO collections(id,order_id,collector_id,method,amount_halalas) VALUES('channel-payment','channel-order','channel-owner','cash',2300)")
        c.execute("INSERT INTO refunds(id,collection_id,order_id,method,amount_halalas,reason,authorized_by) VALUES('channel-refund','channel-payment','channel-order','cash',300,'TEST quality','channel-owner')")
    def actor(role):
        client.app.dependency_overrides[auth.current_user]=lambda:users[role]
        return client
    yield actor,users

@pytest.mark.postgres
def test_private_document_and_ledger_scope(channel_client):
    actor,users=channel_client
    for role in ('customer','driver','branch_manager','delivery_manager','accountant'):
        assert actor(role).get('/api/internal-documents/kps-operating-agreement').status_code==403
        assert actor(role).get('/api/channel-settlements/orders/channel-order').status_code==403
        assert actor(role).get('/api/customer/legal/internal').status_code==403
    result=actor('owner').get('/api/internal-documents/kps-operating-agreement')
    # The global security middleware deliberately normalizes protected API responses.
    assert result.status_code==200 and set(result.headers['cache-control'].split(', ')) == {'private', 'no-store'}
    users['owner']['brand_id']='other'
    assert actor('owner').get('/api/internal-documents/kps-operating-agreement').status_code==404
    assert actor('owner').get('/api/channel-settlements/orders/channel-order').status_code==404

@pytest.mark.postgres
def test_refund_allocation_root_cause_idempotency_audit_and_no_order_mutation(channel_client):
    actor,users=channel_client;c=actor('owner');url='/api/channel-settlements/orders/channel-order'
    with db.tx() as conn:before=conn.execute("SELECT row_to_json(o) value FROM orders o WHERE id='channel-order'").fetchone()['value']
    body={'kind':'refund','refund_id':'channel-refund','cause':'branch','amount_halalas':200,'reason':'Reviewed quality issue'}
    key={'Idempotency-Key':uuid4().hex}
    first=c.post(url+'/allocations',json=body,headers=key)
    assert first.status_code==200,first.text
    assert c.post(url+'/allocations',json=body,headers=key).json()==first.json()
    assert first.json()['beneficiary']=='sami'
    assert c.post(url+'/allocations',json=body,headers={'Idempotency-Key':uuid4().hex}).status_code==409
    assert c.post(url+'/allocations',json={**body,'refund_id':'other-order-refund'},headers={'Idempotency-Key':uuid4().hex}).status_code==409
    second=c.post(url+'/allocations',json={**body,'cause':'delivery_channel','amount_halalas':100},headers={'Idempotency-Key':uuid4().hex})
    assert second.status_code==200 and second.json()['beneficiary']=='reem'
    result=c.get(url).json()
    assert result['branch_settlement_halalas']==1800 and result['delivery_channel_effect_halalas']==-100
    assert result['unallocated_refund_halalas']==0 and not result['payout_enabled']
    with db.tx() as conn:
        assert conn.execute("SELECT row_to_json(o) value FROM orders o WHERE id='channel-order'").fetchone()['value']==before
        assert conn.execute("SELECT count(*) n FROM audit_events WHERE action='channel_settlement.allocated'").fetchone()['n']==2
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException):
        with db.tx() as conn:conn.execute("UPDATE channel_settlement_allocations SET amount_halalas=1")

@pytest.mark.postgres
def test_anonymous_cannot_access_internal_and_public_metadata_is_safe(client):
    assert client.get('/api/internal-documents/kps-operating-agreement').status_code==401
    assert client.get('/api/customer/legal/internal').status_code==401
    assert client.get('/api/channel-settlements/orders/anything').status_code==401
    assert client.get('/api/customer/legal').json()['sellers']==[]
    for path in ('/docs/internal-agreement.html','/internal_documents.py','/app/internal_documents.py'):
        assert client.get(path).status_code==404


@pytest.mark.postgres
def test_nonempty_channel_allocations_export_and_restore(channel_client,tmp_path):
    import os
    import json
    import psycopg
    from psycopg import sql
    from app.exports import build_archive,verify_archive,restore_archive
    actor,_=channel_client
    response=actor('owner').post('/api/channel-settlements/orders/channel-order/allocations',
        json={'kind':'refund','refund_id':'channel-refund','cause':'branch','amount_halalas':200,'reason':'Reviewed quality issue'},
        headers={'Idempotency-Key':uuid4().hex})
    assert response.status_code==200,response.text
    table='channel_settlement_allocations'
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        original=conn.execute('SELECT row_to_json(a) value FROM channel_settlement_allocations a').fetchone()['value']
        contents,manifest=build_archive(conn,full=True)
        business,business_manifest=build_archive(conn,brand_id='kps')
        other,other_manifest=build_archive(conn,brand_id='other')
    assert manifest['counts'][table]==business_manifest['counts'][table]==1
    assert other_manifest['counts'][table]==0
    archive,_=verify_archive(business)
    exported=json.loads(archive.read('data/'+table+'.json'))
    assert len(exported)==1
    # PostgreSQL JSON trims fractional trailing zeroes; Python ISO serialization
    # retains six digits. Compare the exact instant, including every microsecond,
    # and still require exact equality of all remaining fields and keys.
    from datetime import datetime
    assert datetime.fromisoformat(exported[0]['created_at'])==datetime.fromisoformat(original['created_at'])
    assert {**exported[0],'created_at':original['created_at']}==original
    assert table not in business_manifest['excluded_tables']
    base=os.environ['TEST_DATABASE_URL'];target='kps_restore_'+uuid4().hex
    try:
        result=restore_archive(contents,target_url=base,target_schema=target,asset_directory=tmp_path/'assets')
        assert result['verified'] and result['counts']==manifest['counts']
        with psycopg.connect(base) as conn:
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(target)))
            assert conn.execute('SELECT row_to_json(a) FROM channel_settlement_allocations a').fetchone()[0]==original
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute('DELETE FROM channel_settlement_allocations')
    finally:
        with psycopg.connect(base,autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(target)))
