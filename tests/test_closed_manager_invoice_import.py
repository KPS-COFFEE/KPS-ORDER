"""Source invoices supplement closed ledgers without inventing identity/time."""
from copy import deepcopy
from datetime import timedelta

import psycopg
import pytest

from app import db, reporting, service_hours
from test_enbishan_dual_brand import dual
from test_prior_deliveries import prior_setup


@pytest.fixture
def closed_invoices(prior_setup):
    actor, post, old_grant, meta, pending, actual = prior_setup
    actual -= timedelta(days=1)
    with db.tx() as c:
        for sid in ('prior-driver','prior-manager'):
            c.execute("UPDATE shifts SET status='closed',opened_at=%s,delivery_operational_date=%s,closed_at=%s,submission=%s WHERE id=%s",
                      (actual-timedelta(hours=1),service_hours.delivery_operational_date(actual),actual+timedelta(minutes=10),db.json({'expected_halalas':13900,'counted_halalas':5000,
                      'handover_halalas':5000,'retained_halalas':0,'difference_halalas':-8900}),sid))
            reporting.create_snapshot(c,sid,'dual-owner')
        originals=c.execute("SELECT * FROM shifts WHERE id IN ('prior-driver','prior-manager') ORDER BY id").fetchall()
        reports=c.execute('SELECT id,snapshot FROM closure_reports ORDER BY id').fetchall()
    grant=post('owner','/api/prior-deliveries/grants',{
        'manager_shift_id':'prior-manager','driver_shift_ids':['prior-driver'],
        'closed_manager_supplement':True,'reason':'Synthetic owner-authorized source invoice supplement'},brand='enbishan')['grant']
    body={'driver_shift_id':'prior-driver','branch_id':'enbishan-test-1',
          'invoice_reference':'SYNTHETIC-SABAD-001','invoice_issued_at':actual.isoformat(),
          'customer_label':'CUSTOMER0001','items':[{'name':'Synthetic source invoice item','quantity':1,'unit_price_halalas':3200}],
          'total_halalas':3200,'tax_halalas':417,'cash_halalas':0,'card_halalas':3200,
          'reason':'Owner supplied original Sabad invoice; actual delivery time unknown','confirmed':True}
    return actor,post,grant,body,originals,reports,old_grant


def endpoint(grant):
    return '/api/prior-deliveries/grants/'+grant['id']+'/legacy-invoice'


@pytest.mark.postgres
def test_two_source_invoices_add_only_card_supplements_and_keep_original_cash(closed_invoices):
    actor,post,g,body,originals,reports,_=closed_invoices
    with db.tx() as c:
        before_custody=c.execute('SELECT * FROM custody_events ORDER BY id').fetchall()
        before_transfers=c.execute('SELECT * FROM custody_transfers ORDER BY id').fetchall()
    first=post('owner',endpoint(g),body,brand='enbishan',key='source-invoice-1')
    assert post('owner',endpoint(g),body,brand='enbishan',key='source-invoice-1')==first
    second_body=deepcopy(body)
    second_body.update(invoice_reference='SYNTHETIC-SABAD-002',total_halalas=3450,card_halalas=3450,tax_halalas=450)
    second_body['items'][0]['unit_price_halalas']=3450
    second=post('owner',endpoint(g),second_body,brand='enbishan')
    with db.tx() as c:
        assert c.execute("SELECT * FROM shifts WHERE id IN ('prior-driver','prior-manager') ORDER BY id").fetchall()==originals
        assert c.execute('SELECT id,snapshot FROM closure_reports WHERE id=ANY(%s) ORDER BY id',([r['id'] for r in reports],)).fetchall()==reports
        assert c.execute("SELECT submission->>'counted_halalas' n FROM shifts WHERE id='prior-driver'").fetchone()['n']=='5000'
        assert c.execute('SELECT * FROM custody_events ORDER BY id').fetchall()==before_custody
        assert c.execute('SELECT * FROM custody_transfers ORDER BY id').fetchall()==before_transfers
        sums=c.execute("SELECT method,sum(amount_halalas) n FROM collections GROUP BY method").fetchall()
        assert sums==[{'method':'card','n':6650}]
        for result in (first,second):
            order=c.execute('SELECT * FROM orders WHERE id=%s',(result['order']['id'],)).fetchone()
            task=c.execute('SELECT * FROM driver_tasks WHERE order_id=%s',(order['id'],)).fetchone()
            customer=c.execute('SELECT * FROM users WHERE id=%s',(order['customer_id'],)).fetchone()
            assert order['payment_status']=='paid' and order['delivery_status']=='delivered'
            assert order['completed_at'] is None and task['delivered_at'] is None and task['picked_up_at'] is None
            assert not order['customer'].get('phone') and customer['phone'] is None
            assert customer['identity_kind']=='guest' and not customer['active']
            assert customer['id'] not in {'dual-enbishan-customer','dual-kps-customer'}
            supplement=c.execute('SELECT * FROM shifts WHERE id=%s',(task['shift_id'],)).fetchone()
            assert supplement['supplement_of_shift_id']=='prior-driver'
            assert supplement['status']=='closed' and supplement['opened_at']==supplement['closed_at']
            assert supplement['opening_halalas']==0 and not supplement['permanent_float'] and not supplement['vehicle_log']
            assert not c.execute('SELECT 1 FROM branch_print_jobs WHERE order_id=%s',(order['id'],)).fetchone()
            assert not c.execute('SELECT 1 FROM fiscal_documents WHERE order_id=%s',(order['id'],)).fetchone()
            snap=c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',(result['supplement_report_id'],)).fetchone()['snapshot']
            assert snap['summary']['completed_orders']==1
            assert snap['summary']['card_collected_halalas']==order['total_halalas']
            assert snap['summary']['cash_collected_halalas']==0
        users=c.execute('SELECT customer_id FROM orders WHERE id=ANY(%s)',([first['order']['id'],second['order']['id']],)).fetchall()
        assert len({u['customer_id'] for u in users})==2
        from app.shift_reconciliation import closed_shift
        manager=c.execute("SELECT * FROM shifts WHERE id='prior-manager'").fetchone()
        assert len(closed_shift(c,manager)['supplements'])==2
        from app.fiscal_issuance import issue_sale
        from fastapi import HTTPException
        assert issue_sale(c,first['order']['id'],'dual-owner') is None
        with pytest.raises(HTTPException) as refused:
            issue_sale(c,first['order']['id'],'dual-owner',required=True)
        assert refused.value.status_code==409
    # Imported collection recording time is today, but source reporting belongs
    # only to the original operational day and must not double-count today.
    day=g['operational_date']
    for group in ('daily','items'):
        result=actor('owner','enbishan').get('/api/accounting/reports',params={'start':day,'end':day,'group':group})
        assert result.status_code==200,result.text
        rows=result.json()['rows']
        assert sum(r['gross'] for r in rows)==6650
        if group=='daily':
            assert sum(r['orders'] for r in rows)==2 and sum(r['card'] for r in rows)==6650
            assert sum(r['cash'] for r in rows)==0
        else:assert sum(r['quantity'] for r in rows)==2
    today=service_hours.delivery_operational_date().isoformat()
    result=actor('owner','enbishan').get('/api/accounting/reports',params={'start':today,'end':today,'group':'daily'})
    assert result.status_code==200,result.text
    assert sum(r['orders']+r['card']+r['gross'] for r in result.json()['rows'])==0
    post('owner',endpoint(g),body,brand='enbishan',status=409)


@pytest.mark.postgres
@pytest.mark.parametrize('change',['line_total','payment_total','tax','future_invoice','unconfirmed','foreign_branch','unknown_driver'])
def test_source_invoice_validation_is_atomic(closed_invoices,change):
    _,post,g,body,_,_,_=closed_invoices
    if change=='line_total': body['items'][0]['unit_price_halalas']+=1
    if change=='payment_total': body['card_halalas']-=1
    if change=='tax': body['tax_halalas']=body['total_halalas']+1
    if change=='future_invoice': body['invoice_issued_at']='2099-01-01T10:00:00+03:00'
    if change=='unconfirmed': body['confirmed']=False
    if change=='foreign_branch': body['branch_id']='kps-branch-1'
    if change=='unknown_driver': body['driver_shift_id']='unknown-driver'
    actor=closed_invoices[0]
    with db.tx() as c: users_before=c.execute('SELECT count(*) n FROM users').fetchone()['n']
    result=actor('owner','enbishan').post(endpoint(g),json=body,headers={'Idempotency-Key':'invalid-'+change})
    assert result.status_code in (403,409,422),result.text
    with db.tx() as c:
        assert not c.execute('SELECT 1 FROM legacy_invoice_imports').fetchone()
        assert not c.execute('SELECT 1 FROM collections').fetchone()
        assert not c.execute("SELECT 1 FROM shifts WHERE supplement_of_shift_id IS NOT NULL").fetchone()
        assert c.execute('SELECT count(*) n FROM users').fetchone()['n']==users_before


@pytest.mark.postgres
def test_source_invoice_requires_explicit_owner_closed_manager_grant(closed_invoices):
    _,post,g,body,_,_,old_grant=closed_invoices
    post('manager',endpoint(g),body,brand='enbishan',status=403)
    post('owner',endpoint(old_grant),body,brand='enbishan',status=409)
    post('owner','/api/prior-deliveries/grants/'+g['id']+'/revoke',{'reason':'Synthetic revoke source invoice permission'},brand='enbishan')
    post('owner',endpoint(g),body,brand='enbishan',status=403)


@pytest.mark.postgres
def test_import_reference_deduplicates_across_grants_and_evidence_is_immutable(closed_invoices):
    _,post,g,body,_,_,_=closed_invoices
    post('owner',endpoint(g),body,brand='enbishan')
    new=post('owner','/api/prior-deliveries/grants',{'manager_shift_id':'prior-manager','driver_shift_ids':['prior-driver'],
             'closed_manager_supplement':True,'reason':'Another explicit grant must not duplicate invoice'},brand='enbishan')['grant']
    post('owner',endpoint(new),body,brand='enbishan',status=409)
    with db.tx() as c:
        c.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('source-other-driver','Synthetic other driver','driver','enbishan','[\"enbishan-test-1\"]')")
        c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,status,opened_at,closed_at,delivery_operational_date) SELECT 'source-other-shift','driver','source-other-driver',brand_id,branch_ids,manager_shift_id,status,opened_at,closed_at,delivery_operational_date FROM shifts WHERE id='prior-driver'")
    other=post('owner','/api/prior-deliveries/grants',{'manager_shift_id':'prior-manager','driver_shift_ids':['source-other-shift'],
             'closed_manager_supplement':True,'reason':'Duplicate invoice cannot move to another driver'},brand='enbishan')['grant']
    other_body={**body,'driver_shift_id':'source-other-shift'}
    post('owner',endpoint(other),other_body,brand='enbishan',status=409)
    for sql in ('UPDATE legacy_invoice_imports SET details=details', 'DELETE FROM legacy_invoice_imports'):
        with pytest.raises(psycopg.Error):
            with db.tx() as c:c.execute(sql)
