"""Owner investigation over synthetic canonical evidence; no real balance mutations."""
import io
import json
import zipfile
from copy import deepcopy
import pytest
from fastapi import HTTPException
from app import db
from app.owner_audit import clean,require_owner,export_data,bounded,MAX_ROWS,documented_review
from test_operations import ops
from test_financial_clarifications import setup_case


def test_private_fields_binary_and_credentials_are_excluded_recursively():
    source={'id':'e1','customer':{'phone':'private'},'details':{'access_token':'secret','evidence':['R001'],'nested':{'password_hash':'private','amount_halalas':1950}},'pdf_data':b'private','phone':'private'}
    original=deepcopy(source)
    assert clean(source)=={'id':'e1','details':{'evidence':['R001'],'nested':{'amount_halalas':1950}}}
    assert source==original


@pytest.mark.parametrize('role',['driver','delivery_manager','admin','customer'])
def test_owner_only_even_accounting_permissions_cannot_grant_audit(role):
    with pytest.raises(HTTPException) as error:require_owner(dict(role=role,permissions=['accounting_review']))
    assert error.value.status_code==403


def test_excess_evidence_is_rejected_instead_of_silently_truncated():
    with pytest.raises(HTTPException) as error:bounded([None]*(MAX_ROWS+1))
    assert error.value.status_code==413


@pytest.mark.parametrize('lang',['ar','en'])
def test_owner_exports_preserve_all_selected_evidence_without_spreadsheet_formulas(lang):
    data=dict(generated_at='2026-10-26T10:00:00Z',financial_completion_claimed=False,limitations=['recorded_evidence_only'],
        orders=[dict(id='order1',total_halalas=2500,payment_status='unpaid')],
        collections=[dict(id='payment1',amount_halalas=1950,actor_name='=HYPERLINK("evil")',reference='<receipt>')])
    assert json.loads(export_data(data,'json',lang).body)==data
    assert export_data(data,'pdf',lang).body.startswith(b'%PDF')
    with zipfile.ZipFile(io.BytesIO(export_data(data,'xlsx',lang).body)) as archive:
        sheet=archive.read('xl/worksheets/sheet1.xml').decode()
        assert '<f>' not in sheet and 'HYPERLINK' in sheet
        assert 'payment1' in sheet and 'order1' in sheet


@pytest.mark.postgres
def test_owner_order_audit_actual_shortfall_scope_privacy_and_export(ops):
    actor,post,_,_=ops
    oid=setup_case(ops)
    with db.tx() as c:
        before=c.execute('SELECT total_halalas,payment_status,version FROM orders WHERE id=%s',(oid,)).fetchone()
        financial_before=c.execute('SELECT count(*) n FROM collections').fetchone()['n']
    url='/api/owner-audit?order_query='+oid
    result=actor('owner').get(url)
    assert result.status_code==200,result.text
    data=result.json()
    assert len(data['orders'])==1 and data['orders'][0]['total_halalas']==2500
    assert data['orders'][0]['financial_case']['pending_amount_halalas']==550
    assert sum(p['amount_halalas'] for p in data['collections'])==1950
    assert data['financial_completion_claimed'] is False
    assert 'customer' not in data['orders'][0]
    assert actor('driver').get(url).status_code==403
    assert actor('manager').get(url).status_code==403
    assert actor('other').get(url).status_code==404
    assert actor('accountant').get(url).status_code==403
    for format in ('json','pdf','xlsx'):
        response=actor('owner').get('/api/owner-audit/export/'+format+'?order_query='+oid+'&lang=en')
        assert response.status_code==200,response.text
        assert 'no-store' in response.headers['cache-control']
    with db.tx() as c:
        assert c.execute('SELECT total_halalas,payment_status,version FROM orders WHERE id=%s',(oid,)).fetchone()==before
        assert c.execute('SELECT count(*) n FROM collections').fetchone()['n']==financial_before


@pytest.mark.postgres
def test_overlapping_driver_manager_closures_are_deduplicated_and_mixed_scope_denied(ops):
    actor,_,order,shifts=ops
    manager,driver=shifts();oid=order()
    with db.tx() as c:
        snapshot={'orders':[{'id':oid}],'shift':{'submission':{'handover_halalas':1000,'actual_handover_halalas':900}},'summary':{'cash_collected_halalas':1000},'driver_reports':[{'shift_id':driver}],'operational_date':'2026-10-26'}
        c.execute("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES('audit-manager',%s,'manager','ops-brand','[\"ops-branch\"]','ops-manager',%s),('audit-driver',%s,'driver','ops-brand','[\"ops-branch\"]','ops-driver',%s)",(manager,db.json(snapshot),driver,db.json(snapshot)))
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids) VALUES('audit-foreign-shift','manager','ops-other','ops-other','[\"ops-branch-other\"]')")
        c.execute("INSERT INTO closure_reports(id,shift_id,kind,brand_id,branch_ids,user_id,snapshot) VALUES('audit-foreign','audit-foreign-shift','manager','ops-other','[\"ops-branch-other\"]','ops-other','{}')")
    response=actor('owner').get('/api/owner-audit?report_ids=audit-manager&report_ids=audit-driver&report_ids=audit-driver')
    assert response.status_code==200,response.text
    data=response.json()
    assert len(data['orders'])==1 and len(data['closures'])==2
    assert len(data['selected_report_ids'])==2
    original=data['closures'][0]['original_submission']
    assert original['handover_halalas']==1000 and original['actual_handover_halalas']==900
    denied=actor('owner').get('/api/owner-audit?report_ids=audit-driver&report_ids=audit-foreign')
    assert denied.status_code==404 and 'audit-driver' not in denied.text
    assert actor('owner').get('/api/owner-audit').status_code==422
    assert actor('owner').get('/api/owner-audit?order_query=unknown').status_code==404



def test_missing_financial_evidence_never_turns_into_a_zero_balance():
    original={'integrity':{'verified':False,'issues':[{'code':'EVIDENCE_MISSING'}]},'system_cash_halalas':0,'bank_halalas':0,'cash_received_halalas':None,'financially_complete':True}
    result=documented_review(original)
    assert result['system_cash_halalas'] is None and result['bank_halalas'] is None
    assert result['financial_state']=='undocumented' and not result['financially_complete']
    documented_zero=documented_review(original,{'bank_halalas':0,'cash_due_halalas':0,'cash_received_halalas':0})
    assert documented_zero['bank_halalas']==0 and documented_zero['cash_received_halalas']==0
    assert documented_zero['system_cash_halalas'] is None
    assert original['system_cash_halalas']==0


@pytest.mark.postgres
def test_owner_audit_keeps_bank_fees_timing_and_receipts_separate_from_customer_collections(ops):
    from test_delivery_workflow_controls import picked_up
    from datetime import date
    actor,post,_,_=ops
    from test_accounting import accountant
    accountant()
    oid,_,_,_=picked_up(ops,total=2500)
    post('driver',f'/api/orders/{oid}/collect',{'method':'card','amount_halalas':2500,'reference':'POS-AUDIT-001'})
    with db.tx() as c:
        collection=c.execute('SELECT * FROM collections WHERE order_id=%s',(oid,)).fetchone()
        snapshot={'collections':[dict(id=collection['id'],order_id=oid,shift_id=collection['shift_id'],amount_halalas=2500,method='card',brand_id='ops-brand',branch_id='ops-branch')],'refunds':[]}
        c.execute("INSERT INTO bank_reconciliation_batches(id,brand_id,branch_ids,batch_reference,source_snapshot,gross_halalas,refund_halalas,fees_halalas,expected_net_halalas,evidence_reference,note,proposed_by) VALUES('audit-bank-batch','ops-brand',%s,'POS-AUDIT-BATCH',%s,2500,0,50,2450,'POS evidence','Actual POS batch evidence','ops-owner')",(db.json(['ops-branch']),db.json(snapshot)))
        c.execute("INSERT INTO bank_reconciliation_bank_lines(id,received_on,bank_account_reference,statement_reference,line_reference,amount_halalas,evidence_reference,recorded_by) VALUES('audit-bank-line',%s,'audit-account','audit-statement','audit-line',1000,'Actual bank statement','ops-accountant')",(date.today(),))
        c.execute("INSERT INTO bank_reconciliation_receipts(id,batch_id,bank_account_reference,statement_reference,line_reference,amount_halalas,statement_amount_halalas,received_on,evidence_reference,note,proposed_by) VALUES('audit-bank-receipt','audit-bank-batch','audit-account','audit-statement','audit-line',1000,1000,%s,'Bank evidence','Only part of the POS batch arrived','ops-owner')",(date.today(),))
        c.execute("INSERT INTO bank_reconciliation_decisions(id,batch_id,receipt_id,decision,evidence_reference,note,decided_by) VALUES('audit-batch-approval','audit-bank-batch',NULL,'approved','Batch independently checked','Independent batch approval','ops-accountant'),('audit-receipt-approval','audit-bank-batch','audit-bank-receipt','approved','Statement independently checked','Independent bank receipt approval','ops-accountant')")
        c.execute("INSERT INTO bank_reconciliation_bank_claims(id,consumer_kind,consumer_id,receipt_id,bank_account_reference,statement_reference,line_reference,amount_halalas) VALUES('audit-bank-claim','pos_batch','audit-bank-receipt','audit-bank-receipt','audit-account','audit-statement','audit-line',1000)")
    response=actor('owner').get('/api/owner-audit?order_query='+oid)
    assert response.status_code==200,response.text
    data=response.json();batch=data['bank_reconciliations'][0]
    assert batch['gross_halalas']==2500 and batch['fees_halalas']==50 and batch['expected_net_halalas']==2450
    assert batch['bank_received_halalas']==1000 and batch['timing_pending_halalas']==1450
    assert batch['state']=='partially_received' and not batch['financially_matched']
    assert sum(r['amount_halalas'] for r in data['collections'])==2500
    assert len(data['bank_statement_lines'])==1 and len(data['bank_statement_allocations'])==1
    assert actor('other').get('/api/owner-audit?order_query='+oid).status_code==404
