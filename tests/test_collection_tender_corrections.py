"""Canonical tender correction uses synthetic PostgreSQL only; no gateway,
bank request, customer charge or original financial record mutation occurs."""
from datetime import date, timedelta
from uuid import uuid4
import pytest
import psycopg
from fastapi import HTTPException
from app import db
from app.collection_tender_corrections import target_values, Proposal
from test_operations import ops,delivered,begin_driver_closure
from test_driver_float import accountant


def body(source,method='card'):
    return {'expected_order_version':source['expected_order_version'],'source_correction_id':source['source_correction_id'],'target_method':method,
        'target_tendered_halalas':2000 if method=='cash' else None,'target_reference':'Actual terminal slip 123' if method=='card' else '',
        'evidence_reference':'Original customer receipt and signed terminal slip','note':'Documented wrong tender, no new payment or invoice change',
        'followup_user_id':'ops-accountant','due_on':(date.today()+timedelta(days=1)).isoformat()}


def approval(source):
    return {'expected_order_version':source['expected_order_version'],'source_correction_id':source['source_correction_id'],'confirmed':True,
        'evidence_reference':'Independent review of original collection proof','note':'Approved source reclassification only, no receipt or bank match'}


def setup(ops,monkeypatch):
    monkeypatch.setenv('KPS_ORDER_SOURCE_TREATMENTS_ENABLED','true')
    actor,post,order,shifts=ops;accountant()
    manager,driver=shifts();oid=delivered(post,order,driver)
    with db.tx() as c:original=c.execute('SELECT * FROM collections WHERE order_id=%s',(oid,)).fetchone()
    path='/api/collections/'+original['id']+'/tender-corrections'
    return actor,post,oid,manager,driver,original,path


@pytest.mark.postgres
def test_closed_source_corrects_once_and_preserves_collection_invoice_and_snapshot(ops,monkeypatch):
    from app import reporting
    monkeypatch.setattr(reporting,'generate_files',lambda *_:None)
    actor,post,oid,manager,driver,original,path=setup(ops,monkeypatch)
    begin_driver_closure(post,driver)
    post('driver',f'/api/shifts/{driver}/submit',{'counted_halalas':0,'terminal_total_halalas':1000,'handover_halalas':0,'handover':{'recipient_user_id':'ops-manager','to_shift_id':manager,'reference':'Actual card payment, zero cash'}})
    post('manager',f'/api/shifts/{driver}/driver-close',{'received_halalas':0,'confirmed':True})
    with db.tx() as c:
        report=c.execute('SELECT * FROM closure_reports WHERE shift_id=%s',(driver,)).fetchone()
        fiscal=c.execute('SELECT * FROM fiscal_documents WHERE order_id=%s ORDER BY id',(oid,)).fetchall()
    source=actor('accountant').get(path).json();request_body=body(source)
    post('manager',path,request_body,status=403)
    post('other',path,request_body,status=404)
    key=uuid4().hex;proposed=post('accountant',path,request_body,key=key)
    assert post('accountant',path,request_body,key=key)==proposed
    approve=approval(source)
    post('accountant',path+'/'+proposed['id']+'/approve',approve,status=403)
    key=uuid4().hex;corrected=post('owner',path+'/'+proposed['id']+'/approve',approve,key=key)
    assert post('owner',path+'/'+proposed['id']+'/approve',approve,key=key)==corrected
    with db.tx() as c:
        assert c.execute('SELECT * FROM collections WHERE id=%s',(original['id'],)).fetchone()==original
        effective=c.execute('SELECT * FROM effective_collections WHERE id=%s',(original['id'],)).fetchone()
        assert effective['method']=='card' and effective['amount_halalas']==1000
        assert effective['tendered_halalas'] is None and effective['change_halalas']==0
        assert c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',(report['id'],)).fetchone()['snapshot']==report['snapshot']
        assert c.execute('SELECT * FROM fiscal_documents WHERE order_id=%s ORDER BY id',(oid,)).fetchall()==fiscal
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==1
        events=c.execute('SELECT * FROM custody_events WHERE reference_id=%s',(corrected['id'],)).fetchall()
        assert len(events)==1 and events[0]['kind']=='adjustment' and events[0]['amount_halalas']==-1000
        assert c.execute('SELECT count(*) n FROM report_corrections WHERE report_id=%s',(report['id'],)).fetchone()['n']==1
        from app.collection_tender_corrections import matching_channel_correction
        assert matching_channel_correction(c,report,-1000,1000,[corrected['id']])['ids']==[corrected['id']]
        assert matching_channel_correction(c,report,-1000,1000,['unrelated-document']) is None
    with pytest.raises(psycopg.Error):
        with db.tx() as c:c.execute("UPDATE collection_tender_corrections SET target_method='cash' WHERE id=%s",(corrected['id'],))


@pytest.mark.postgres
def test_stale_source_return_and_structured_supersession_keep_raw_history(ops,monkeypatch):
    actor,post,oid,manager,driver,original,path=setup(ops,monkeypatch)
    source=actor('accountant').get(path).json();first=post('accountant',path,body(source))
    with db.tx() as c:c.execute('UPDATE orders SET version=version+1 WHERE id=%s',(oid,))
    post('owner',path+'/'+first['id']+'/approve',approval(source),status=409)
    returned=post('owner',path+'/'+first['id']+'/reject',{'note':'Source changed; review current original evidence'})
    assert returned['responsible_user_id']=='ops-accountant'
    source=actor('accountant').get(path).json();p=post('accountant',path,body(source));a=post('owner',path+'/'+p['id']+'/approve',approval(source))
    source=actor('accountant').get(path).json()
    assert source['source_correction_id']==a['id']
    reverse=body(source,'cash');reverse['source_correction_id']=None
    post('accountant',path,reverse,status=409)
    reverse['source_correction_id']=a['id'];p2=post('accountant',path,reverse);a2=post('owner',path+'/'+p2['id']+'/approve',approval(source))
    with db.tx() as c:
        raw=c.execute('SELECT * FROM collections WHERE id=%s',(original['id'],)).fetchone()
        assert raw==original
        assert c.execute('SELECT method FROM effective_collections WHERE id=%s',(original['id'],)).fetchone()['method']=='cash'
        docs=c.execute('SELECT * FROM collection_tender_corrections WHERE collection_id=%s ORDER BY revision',(original['id'],)).fetchall()
        assert len(docs)==2 and docs[1]['source_correction_id']==a['id'] and docs[1]['id']==a2['id']
        assert c.execute("SELECT sum(amount_halalas) n FROM custody_events WHERE reference_id=ANY(%s)",([a['id'],a2['id']],)).fetchone()['n']==0


@pytest.mark.postgres
def test_existing_bank_claim_blocks_change_and_commits_named_followup(ops,monkeypatch):
    actor,post,oid,manager,driver,original,path=setup(ops,monkeypatch)
    with db.tx() as c:
        c.execute("INSERT INTO bank_reconciliation_batches(id,brand_id,branch_ids,batch_reference,source_snapshot,gross_halalas,refund_halalas,fees_halalas,expected_net_halalas,evidence_reference,note,proposed_by) VALUES('tender-existing-batch','ops-brand','[\"ops-branch\"]','Original source claim','{}',1000,0,0,1000,'Original bank source documents','Synthetic linked bank state','ops-accountant')")
        c.execute("INSERT INTO bank_reconciliation_source_claims(source_kind,source_id,batch_id,amount_halalas) VALUES('collection',%s,'tender-existing-batch',1000)",(original['id'],))
    source=actor('accountant').get(path).json()
    assert source['blocked_reason']
    blocked=post('accountant',path,body(source),status=409)['detail']
    assert blocked['status']=='requires_followup' and blocked['responsible_user_id']=='ops-accountant'
    with db.tx() as c:
        task=c.execute("SELECT details FROM audit_events WHERE action='collection_tender.followup_required' AND entity_id=%s",(oid,)).fetchone()['details']
        assert task['followup_id']==blocked['followup_id'] and task['due_on']
        assert c.execute('SELECT 1 FROM collection_tender_corrections WHERE collection_id=%s',(original['id'],)).fetchone() is None
        assert c.execute('SELECT * FROM collections WHERE id=%s',(original['id'],)).fetchone()==original


@pytest.mark.postgres
def test_production_hold_is_fail_closed_without_financial_change(ops,monkeypatch):
    actor,post,oid,manager,driver,original,path=setup(ops,monkeypatch)
    source=actor('accountant').get(path).json();monkeypatch.setenv('KPS_ORDER_SOURCE_TREATMENTS_ENABLED','false')
    assert actor('owner').get('/api/collections/tender-correction-capabilities').json()['enabled'] is False
    post('accountant',path,body(source),status=404)
    with db.tx() as c:assert c.execute('SELECT 1 FROM collection_tender_correction_proposals').fetchone() is None


def test_tender_target_keeps_net_amount_and_rejects_fictitious_card_cash_details():
    effective={'method':'card','amount_halalas':1000}
    proposal=Proposal(expected_order_version=1,target_method='cash',target_tendered_halalas=1500,evidence_reference='Verified source documents',note='Documented tender correction',followup_user_id='accountant',due_on=date.today())
    assert target_values(proposal,effective)==(1500,500,'')
    with pytest.raises(HTTPException):target_values(proposal.model_copy(update={'target_tendered_halalas':999}),effective)
    with pytest.raises(HTTPException):target_values(proposal.model_copy(update={'target_method':'card'}),effective)


@pytest.mark.parametrize('target',[
    {'id':'owner','role':'owner','active':True,'brand_id':'other','branch_ids':['branch']},
    {'id':'owner','role':'owner','active':True,'brand_id':'seller','home_brand_id':'home','branch_ids':[]},
])
def test_followup_owner_must_be_authorized_for_source_brand_and_projected_branch(target):
    from unittest.mock import Mock,patch
    from app.collection_tender_corrections import eligible_followup
    c=Mock();c.execute.return_value.fetchone.return_value=target
    with patch('app.collection_tender_corrections.multibrand.enabled',return_value=False),patch('app.collection_tender_corrections.auth.has_permission',return_value=True),pytest.raises(HTTPException) as failure:
        eligible_followup(c,{'id':'accountant'},{'brand_id':'seller','branch_id':'branch'},target['id'])
    assert failure.value.status_code==422
