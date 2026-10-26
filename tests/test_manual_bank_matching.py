"""Manual card batch evidence, actual bank timing and duplicate-source isolation."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from uuid import uuid4
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from psycopg.errors import RaiseException
from app import db
from app.manual_bank_matching import Batch,Receipt,Decision,check_remaining,decide_batch
from test_operations import ops
from test_accounting import accountant


@pytest.mark.parametrize('payload',[
    {'collection_ids':[],'refund_ids':[],'fees_halalas':0},
    {'collection_ids':['same','same'],'fees_halalas':0},
    {'collection_ids':['source'],'fees_halalas':True},
    {'collection_ids':['source'],'fees_halalas':1.5},
])
def test_batch_requires_actual_distinct_sources_and_integer_money(payload):
    with pytest.raises(ValidationError):Batch(batch_reference='BATCH TEST',evidence_reference='Actual evidence',note='Actual source verification',**payload)


@pytest.mark.parametrize('expected,received,amount',[(780,500,281),(780,780,1),(780,0,-1),(-210,0,210),(-210,-200,-11),(0,0,0)])
def test_bank_movement_cannot_cross_pending_amount_or_direction(expected,received,amount):
    with pytest.raises(HTTPException):check_remaining(expected,received,amount)


def test_partial_bank_movements_and_debits_are_supported_without_netting():
    check_remaining(780,0,500);check_remaining(780,500,280)
    check_remaining(-210,0,-100);check_remaining(-210,-100,-110)


def setup_sources(ops,refund=True):
    actor,post,order,_=ops;accountant();oid=order('pickup')
    post('branch',f'/api/orders/{oid}/collect',{'method':'card','amount_halalas':1000,'reference':'ACTUAL POS OPERATION'})
    if refund:post('owner',f'/api/orders/{oid}/refund',{'method':'card','amount_halalas':200,'reason':'ACTUAL CARD REFUND','line_quantities':{}})
    with db.tx() as c:
        sources=c.execute('SELECT * FROM collections WHERE order_id=%s ORDER BY id',(oid,)).fetchall()
        refunds=c.execute('SELECT * FROM refunds WHERE order_id=%s ORDER BY id',(oid,)).fetchall()
    body={'batch_reference':'PROVIDER BATCH 001','collection_ids':[r['id'] for r in sources],
        'refund_ids':[r['id'] for r in refunds],'fees_halalas':20,'evidence_reference':'Provider batch fees slip 001','note':'Actual batch lists source operations and refund fee'}
    return oid,body,sources,refunds


def decision(choice='approved'):
    return {'decision':choice,'confirmed':True,'evidence_reference':'Independent source review document','note':'Independently reviewed actual source amounts and fees'}


def receipt(amount=500,line='1'):
    return {'bank_account_reference':'TEST ACCOUNT 1234','statement_reference':'TEST ACCOUNT 1234 STATEMENT 001',
        'line_reference':line,'amount_halalas':amount,'statement_amount_halalas':amount,'received_on':datetime.now(ZoneInfo('Asia/Riyadh')).date().isoformat(),
        'evidence_reference':'Actual bank statement line 001','note':'Actual bank receipt distinct from POS gross'}


@pytest.mark.postgres
def test_bank_batch_separates_fees_refunds_timing_and_preserves_sources(ops):
    actor,post,_,_=ops;oid,body,original_collections,original_refunds=setup_sources(ops)
    base='/api/accounting/bank-reconciliation'
    listed=actor('accountant').get(base+'/sources').json()
    assert listed['collections'][0]['id']==body['collection_ids'][0]
    batch=post('accountant',base,body,key='bank-batch-proposal');bid=batch['id']
    assert post('accountant',base,body,key='bank-batch-proposal')==batch
    assert batch['gross_halalas']==1000 and batch['refund_halalas']==200 and batch['fees_halalas']==20
    assert batch['expected_net_halalas']==780 and batch['bank_received_halalas']==0
    assert batch['timing_pending_halalas']==780 and batch['state']=='awaiting_batch_approval'
    post('manager',base,body,status=403)
    assert actor('driver').get(base).status_code==403
    post('accountant',base+'/'+bid+'/decision',decision(),status=403)
    post('accountant',base+'/'+bid+'/receipts',receipt(),status=409)
    batch=post('owner',base+'/'+bid+'/decision',decision(),key='batch-independent-approval')
    assert batch['state']=='awaiting_bank' and batch['operational_work_may_continue']
    assert actor('accountant').get(base+'/sources').json()=={'collections':[],'refunds':[]}
    other_batch=post('accountant',base,{**body,'batch_reference':'DUPLICATE SOURCE BATCH'})
    post('owner',base+'/'+other_batch['id']+'/decision',decision(),status=409)
    first=post('accountant',base+'/'+bid+'/receipts',receipt(),key='actual-bank-arrival-1')
    rid=first['receipts'][0]['id']
    assert first['bank_received_halalas']==0,'Unapproved evidence is not a bank receipt'
    post('accountant',base+'/'+bid+'/receipts/'+rid+'/decision',decision(),status=403)
    partial=post('owner',base+'/'+bid+'/receipts/'+rid+'/decision',decision(),key='actual-bank-independent-1')
    assert partial['state']=='partially_received' and partial['bank_received_halalas']==500 and partial['timing_pending_halalas']==280
    duplicate=post('accountant',base+'/'+bid+'/receipts',receipt(280,'1'))
    duplicate_id=duplicate['receipts'][-1]['id']
    post('owner',base+'/'+bid+'/receipts/'+duplicate_id+'/decision',decision(),status=409)
    post('owner',base+'/'+bid+'/receipts/'+duplicate_id+'/decision',decision('rejected'))
    second=post('accountant',base+'/'+bid+'/receipts',receipt(280,'2'))
    second_id=second['receipts'][-1]['id']
    final=post('owner',base+'/'+bid+'/receipts/'+second_id+'/decision',decision(),key='actual-bank-independent-2')
    assert final['state']=='matched' and final['timing_pending_halalas']==0 and final['bank_received_halalas']==780
    assert post('owner',base+'/'+bid+'/receipts/'+second_id+'/decision',decision(),key='actual-bank-independent-2')==final
    with db.tx() as c:
        assert c.execute('SELECT * FROM collections WHERE order_id=%s ORDER BY id',(oid,)).fetchall()==original_collections
        assert c.execute('SELECT * FROM refunds WHERE order_id=%s ORDER BY id',(oid,)).fetchall()==original_refunds
        assert c.execute('SELECT count(*) n FROM bank_reconciliation_source_claims WHERE batch_id=%s',(bid,)).fetchone()['n']==2
        assert c.execute('SELECT count(*) n FROM bank_reconciliation_bank_claims').fetchone()['n']==2
    with pytest.raises(RaiseException),db.tx() as c:
        c.execute('UPDATE bank_reconciliation_batches SET fees_halalas=0 WHERE id=%s',(bid,))


@pytest.mark.postgres
def test_refund_only_bank_debit_and_scope_rejections(ops):
    actor,post,_,_=ops;_,body,_,_=setup_sources(ops)
    base='/api/accounting/bank-reconciliation'
    made=post('accountant',base,{**body,'collection_ids':[],'fees_halalas':10})
    bid=made['id'];assert made['expected_net_halalas']==-210
    post('other',base+'/'+bid+'/decision',decision(),status=404)
    post('other',base,{**body,'collection_ids':[]},status=404)
    post('owner',base+'/'+bid+'/decision',decision())
    post('accountant',base+'/'+bid+'/receipts',receipt(210),status=409)
    data=post('accountant',base+'/'+bid+'/receipts',receipt(-210));rid=data['receipts'][0]['id']
    result=post('owner',base+'/'+bid+'/receipts/'+rid+'/decision',decision())
    assert result['state']=='matched' and result['bank_received_halalas']==-210
    assert actor('other').get(base).json()['batches']==[]


@pytest.mark.postgres
def test_concurrent_batch_approval_claims_source_once(ops):
    actor,post,_,_=ops;_,body,_,_=setup_sources(ops,refund=False)
    base='/api/accounting/bank-reconciliation'
    first=post('accountant',base,body);second=post('accountant',base,{**body,'batch_reference':'SAME OPERATION OTHER BATCH'})
    with db.tx() as c:owner=c.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone()
    def approve(bid):
        try:
            decide_batch(bid,Decision(**decision()),SimpleNamespace(headers={'Idempotency-Key':uuid4().hex}),owner)
            return 200
        except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(approve,[first['id'],second['id']]))
    assert sorted(results)==[200,409]
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM bank_reconciliation_source_claims').fetchone()['n']==1


@pytest.mark.postgres
def test_one_physical_bank_line_is_explicitly_allocated_across_brands_once(ops):
    actor,post,_,_=ops;_,body,_,_=setup_sources(ops)
    base='/api/accounting/bank-reconciliation'
    first=post('accountant',base,body);bid=first['id']
    post('owner',base+'/'+bid+'/decision',decision())
    declared=receipt(780,'shared-line');declared['statement_amount_halalas']=1000
    first=post('accountant',base+'/'+bid+'/receipts',declared);rid=first['receipts'][0]['id']
    first=post('owner',base+'/'+bid+'/receipts/'+rid+'/decision',decision())
    assert first['receipts'][0]['bank_line_unallocated_halalas']==220
    # Synthetic canonical sources belong to a separate seller/branch. Neither
    # accountant can read the other seller's batch, even while the physical bank
    # deposit is explicitly shared and its original total is globally bounded.
    with db.tx() as c:
        for name,role in [('foreignpreparer','admin'),('foreignreviewer','admin'),('foreigncollector','branch_manager'),('foreigncustomer','customer')]:
            c.execute('''INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions)
                VALUES(%s,%s,%s,'ops-other','["ops-branch-other"]',%s)''',('ops-'+name,name,role,db.json(['accounting_reports','accounting_review'] if role=='admin' else [])))
        for suffix,amount in [('first',220),('extra',300)]:
            c.execute('''INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer)
                VALUES(%s,'ops-other','ops-branch-other','ops-foreigncustomer','pickup','ready','not_applicable','card',%s,0,%s,'{"items":[]}','{"name":"TEST FOREIGN CUSTOMER"}')''',('bank-foreign-'+suffix,amount,amount))
            c.execute('''INSERT INTO collections(id,order_id,collector_id,method,amount_halalas,reference)
                VALUES(%s,%s,'ops-foreigncollector','card',%s,'TEST FOREIGN POS SOURCE')''',('bank-col-'+suffix,'bank-foreign-'+suffix,amount))
    foreign=post('foreignpreparer',base,{**body,'batch_reference':'SECOND SELLER BATCH','collection_ids':['bank-col-first'],'refund_ids':[],'fees_halalas':0});foreign_bid=foreign['id']
    post('foreignreviewer',base+'/'+foreign_bid+'/decision',decision())
    declared=receipt(220,'SHARED-LINE');declared['statement_amount_halalas']=1000
    declared['bank_account_reference']=' test account 1234 '
    foreign=post('foreignpreparer',base+'/'+foreign_bid+'/receipts',declared);foreign_rid=foreign['receipts'][0]['id']
    foreign=post('foreignreviewer',base+'/'+foreign_bid+'/receipts/'+foreign_rid+'/decision',decision())
    assert foreign['state']=='matched' and foreign['receipts'][0]['bank_line_unallocated_halalas']==0
    assert len(actor('foreignpreparer').get(base).json()['batches'])==1
    assert len(actor('accountant').get(base).json()['batches'])==1
    extra=post('foreignpreparer',base,{**body,'batch_reference':'EXTRA BANK ALLOCATION','collection_ids':['bank-col-extra'],'refund_ids':[],'fees_halalas':0});extra_bid=extra['id']
    post('foreignreviewer',base+'/'+extra_bid+'/decision',decision())
    declared=receipt(300,'shared-line');declared['statement_amount_halalas']=1000
    extra=post('foreignpreparer',base+'/'+extra_bid+'/receipts',declared)
    post('foreignreviewer',base+'/'+extra_bid+'/receipts/'+extra['receipts'][0]['id']+'/decision',decision(),status=409)
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM bank_reconciliation_bank_lines').fetchone()['n']==1
        assert c.execute('SELECT sum(amount_halalas) amount FROM bank_reconciliation_bank_claims').fetchone()['amount']==1000
