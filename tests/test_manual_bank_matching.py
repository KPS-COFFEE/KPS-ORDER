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


@pytest.fixture(autouse=True)
def explicit_test_bank_preparation(monkeypatch):
    monkeypatch.setenv('KPS_MANUAL_BANK_MATCHING_ENABLED','true')


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
    with db.tx() as c:c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids,permissions) VALUES('ops-bankreviewer','Independent bank reviewer','admin','ops-brand','[\"ops-branch\"]','[\"accounting_reports\",\"accounting_review\"]')")
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
@pytest.mark.parametrize('runtime_environment',['testing','production'])
def test_bank_batch_separates_fees_refunds_timing_and_preserves_sources(ops,monkeypatch,runtime_environment):
    actor,post,_,_=ops;oid,body,original_collections,original_refunds=setup_sources(ops)
    # Isolated fixture setup remains testing-only. The complete guarded bank
    # journey then runs with the real production environment condition too.
    monkeypatch.setenv('APP_ENV',runtime_environment)
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
    batch=post('bankreviewer',base+'/'+bid+'/decision',decision(),key='batch-independent-approval')
    assert batch['state']=='awaiting_bank' and batch['operational_work_may_continue']
    assert actor('accountant').get(base+'/sources').json()=={'collections':[],'refunds':[]}
    other_batch=post('accountant',base,{**body,'batch_reference':'DUPLICATE SOURCE BATCH'})
    post('bankreviewer',base+'/'+other_batch['id']+'/decision',decision(),status=409)
    first=post('accountant',base+'/'+bid+'/receipts',receipt(),key='actual-bank-arrival-1')
    rid=first['receipts'][0]['id']
    assert first['bank_received_halalas']==0,'Unapproved evidence is not a bank receipt'
    post('accountant',base+'/'+bid+'/receipts/'+rid+'/decision',decision(),status=403)
    partial=post('bankreviewer',base+'/'+bid+'/receipts/'+rid+'/decision',decision(),key='actual-bank-independent-1')
    assert partial['state']=='partially_received' and partial['bank_received_halalas']==500 and partial['timing_pending_halalas']==280
    duplicate=post('accountant',base+'/'+bid+'/receipts',receipt(280,'1'))
    duplicate_id=duplicate['receipts'][-1]['id']
    post('bankreviewer',base+'/'+bid+'/receipts/'+duplicate_id+'/decision',decision(),status=409)
    post('bankreviewer',base+'/'+bid+'/receipts/'+duplicate_id+'/decision',decision('rejected'))
    second=post('accountant',base+'/'+bid+'/receipts',receipt(280,'2'))
    second_id=second['receipts'][-1]['id']
    final=post('bankreviewer',base+'/'+bid+'/receipts/'+second_id+'/decision',decision(),key='actual-bank-independent-2')
    assert final['state']=='matched' and final['timing_pending_halalas']==0 and final['bank_received_halalas']==780
    assert post('bankreviewer',base+'/'+bid+'/receipts/'+second_id+'/decision',decision(),key='actual-bank-independent-2')==final
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
    post('bankreviewer',base+'/'+bid+'/decision',decision())
    post('accountant',base+'/'+bid+'/receipts',receipt(210),status=409)
    data=post('accountant',base+'/'+bid+'/receipts',receipt(-210));rid=data['receipts'][0]['id']
    result=post('bankreviewer',base+'/'+bid+'/receipts/'+rid+'/decision',decision())
    assert result['state']=='matched' and result['bank_received_halalas']==-210
    assert actor('other').get(base).json()['batches']==[]


@pytest.mark.postgres
def test_concurrent_batch_approval_claims_source_once(ops):
    actor,post,_,_=ops;_,body,_,_=setup_sources(ops,refund=False)
    base='/api/accounting/bank-reconciliation'
    first=post('accountant',base,body);second=post('accountant',base,{**body,'batch_reference':'SAME OPERATION OTHER BATCH'})
    with db.tx() as c:owner=c.execute("SELECT * FROM users WHERE id='ops-bankreviewer'").fetchone()
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
    post('bankreviewer',base+'/'+bid+'/decision',decision())
    declared=receipt(780,'shared-line');declared['statement_amount_halalas']=1000
    first=post('accountant',base+'/'+bid+'/receipts',declared);rid=first['receipts'][0]['id']
    first=post('bankreviewer',base+'/'+bid+'/receipts/'+rid+'/decision',decision())
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


def test_bank_closure_status_does_not_invent_missing_evidence_or_allocate_partial_batch():
    from unittest.mock import patch
    from app.manual_bank_matching import closure_bank_status
    user={'id':'accountant','role':'admin','brand_id':'test','branch_ids':['branch']}
    assert closure_bank_status(None,user,{'snapshot':{}})['state']=='undocumented'
    zero={'collections':[],'refunds':[],'summary':{'card_collected_halalas':0,'card_refunded_halalas':0}}
    assert closure_bank_status(None,user,{'snapshot':zero})['state']=='no_card_operations'
    assert closure_bank_status(None,user,{'snapshot':{**zero,'summary':{'card_collected_halalas':None,'card_refunded_halalas':0}}})['state']=='undocumented'
    snapshot={'collections':[{'id':'col','method':'card','amount_halalas':1000}],
        'refunds':[],'summary':{'card_collected_halalas':1000,'card_refunded_halalas':0}}
    class Cursor:
        def fetchone(self):return {'batch_id':'batch'}
        def fetchall(self):return [{'id':'col','method':'card','amount_halalas':1000}]
    class Connection:
        def execute(self,*args):return Cursor()
    with patch('app.manual_bank_matching.view',return_value={'id':'batch'}),patch('app.manual_bank_matching.project',return_value={'financially_matched':False}):
        result=closure_bank_status(Connection(),user,{'snapshot':snapshot})
        assert result=={'state':'pending','batch_ids':['batch'],'pending_source_count':1}
        assert 'bank_received_halalas' not in result,'Never infer a per-driver bank amount from partial batch arrival'
    with patch('app.manual_bank_matching.view',return_value={'id':'batch'}),patch('app.manual_bank_matching.project',return_value={'financially_matched':True}):
        assert closure_bank_status(Connection(),user,{'snapshot':snapshot})['state']=='matched'


def test_refund_only_source_cannot_be_approved_by_refund_actor_or_shift_custodian():
    from app.manual_bank_matching import assert_independent
    class Cursor:
        def __init__(self,rows):self.rows=rows
        def fetchall(self):return self.rows
    class Connection:
        def __init__(self,shift_actor,refund_actor):self.shift_actor,self.refund_actor=shift_actor,refund_actor
        def execute(self,query,args):
            return Cursor([{'user_id':self.shift_actor}] if 'FROM shifts' in query else
                [{'authorized_by':self.refund_actor}] if 'FROM refunds' in query else [])
    source={'proposed_by':'other','source_snapshot':{'collections':[],'refunds':[{'id':'refund','shift_id':'refund-shift'}]}}
    for shift_actor,refund_actor in [('reviewer','other'),('other','reviewer')]:
        with pytest.raises(HTTPException) as error:assert_independent(Connection(shift_actor,refund_actor),{'id':'reviewer'},source)
        assert error.value.status_code==403
    assert_independent(Connection('custodian','refund-authorizer'),{'id':'reviewer'},source)


@pytest.mark.postgres
def test_registered_actual_bank_source_is_shared_by_pos_and_later_recovery_without_double_use(ops):
    from app.manual_bank_matching import bank_line,claim_bank_line
    actor,post,_,_=ops;_,body,_,_=setup_sources(ops)
    base='/api/accounting/bank-reconciliation'
    proposal=post('accountant',base+'/bank-lines',receipt(1000,'standalone-line'),key='standalone-bank-registration')
    assert proposal['bank_line_id'] is None
    post('accountant',base+'/bank-lines/'+proposal['id']+'/decision',decision(),status=403)
    approved=post('bankreviewer',base+'/bank-lines/'+proposal['id']+'/decision',decision())
    identifier=approved['bank_line_id']
    assert actor('accountant').get(base+'/bank-lines').json()['bank_lines'][0]['unallocated_halalas']==1000
    with db.tx() as c:
        owner=c.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone()
        assert bank_line(c,owner,identifier)['unallocated_halalas']==1000
        claim=claim_bank_line(c,owner,identifier,600,'financial_recovery','actual-debtor-payment')
        assert claim['remaining_halalas']==400
    with pytest.raises(HTTPException),db.tx() as c:
        claim_bank_line(c,owner,identifier,600,'financial_recovery','actual-debtor-payment')
    batch=post('accountant',base,body);bid=batch['id']
    post('bankreviewer',base+'/'+bid+'/decision',decision())
    declared=receipt(780,'standalone-line');declared['statement_amount_halalas']=1000
    proposed=post('accountant',base+'/'+bid+'/receipts',declared)
    post('bankreviewer',base+'/'+bid+'/receipts/'+proposed['receipts'][0]['id']+'/decision',decision(),status=409)
    declared=receipt(400,'standalone-line');declared['statement_amount_halalas']=1000
    proposed=post('accountant',base+'/'+bid+'/receipts',declared)
    actual=post('bankreviewer',base+'/'+bid+'/receipts/'+proposed['receipts'][-1]['id']+'/decision',decision())
    assert actual['bank_received_halalas']==400 and actual['timing_pending_halalas']==380
    with db.tx() as c:
        assert c.execute('SELECT sum(amount_halalas) amount FROM bank_reconciliation_bank_claims').fetchone()['amount']==1000
        assert bank_line(c,owner,identifier)['unallocated_halalas']==0


def test_manual_bank_preparation_is_disabled_by_default(monkeypatch):
    from app.manual_bank_matching import enabled,access,closure_bank_status
    monkeypatch.delenv('KPS_MANUAL_BANK_MATCHING_ENABLED',raising=False)
    assert not enabled()
    with pytest.raises(HTTPException) as error:access({'role':'owner','id':'owner'})
    assert error.value.status_code==404
    assert closure_bank_status(None,{},{} )=={'state':'undocumented','batch_ids':[],'pending_source_count':None}
    zero={'snapshot':{'collections':[],'refunds':[],'summary':{'card_collected_halalas':0,'card_refunded_halalas':0}}}
    assert closure_bank_status(None,{},zero)['state']=='no_card_operations'


@pytest.mark.postgres
def test_registration_period_filter_preserves_original_batches_and_bank_sources(ops):
    actor,post,_,_=ops;_,body,_,_=setup_sources(ops)
    base='/api/accounting/bank-reconciliation'
    made=post('accountant',base,body)
    proposal=post('accountant',base+'/bank-lines',receipt(1000,'period-filter-line'))
    post('bankreviewer',base+'/bank-lines/'+proposal['id']+'/decision',decision())
    assert len(actor('accountant').get(base).json()['batches'])==1
    assert len(actor('accountant').get(base+'/bank-lines').json()['bank_lines'])==1
    period='?start=2099-01-01&end=2099-01-02'
    assert actor('accountant').get(base+period).json()=={'batches':[]}
    assert actor('accountant').get(base+'/bank-lines'+period).json()=={'proposals':[],'bank_lines':[]}
    assert actor('accountant').get(base+'?start=invalid').status_code==422
    assert actor('accountant').get(base+'/bank-lines?start=2026-10-26&end=2026-10-25').status_code==422
    assert actor('accountant').get(base).json()['batches'][0]['id']==made['id']
    assert len(actor('accountant').get(base+'/bank-lines').json()['bank_lines'])==1
    assert actor('other').get(base+'/bank-lines').json()=={'proposals':[],'bank_lines':[]}
