"""Real PostgreSQL verification: fictitious collection reversal, never cash payout."""
from copy import deepcopy
from uuid import uuid4
from datetime import datetime,timedelta
import pytest
from app import db
from app.operations import balance,TestVoid as OwnerTestVoid
from pydantic import ValidationError
from test_operations import ops
from test_accounting import accountant
from test_backup import independent_restore_database


def test_confirmation_and_identity_fields_are_explicit():
    values=dict(order_reference='TEST-0106',expected_version=1,expected_total_halalas=2050,
                reason='TEST morning activation',confirmed_no_cash_received=True)
    assert OwnerTestVoid(**values).expected_total_halalas==2050
    for patch in ({'expected_total_halalas':True},{'confirmed_no_cash_received':'true'},{'expected_version':0},{'reason':' '}):
        with pytest.raises(ValidationError):OwnerTestVoid(**{**values,**patch})


def seed(ops,amounts=(2050,2400,2500)):
    actor,post,make_order,_=ops
    with db.tx() as c:
        user=c.execute("SELECT brand_id,branch_ids FROM users WHERE id='ops-driver'").fetchone()
        for sid,kind,person,parent in [('test-void-manager','manager','ops-manager',None),('test-void-driver','driver','ops-driver','test-void-manager')]:
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,manager_shift_id,delivery_window_snapshot) VALUES(%s,%s,%s,%s,%s,'open',%s,%s)",(sid,kind,person,user['brand_id'],db.json(user['branch_ids']),parent,db.json({'period':'morning'})))
    result=[]
    for index,amount in enumerate(amounts):
        oid='ops-order-'+uuid4().hex;ref=f'TEST-MORNING-{index}'
        with db.tx() as c:
            snap={'delivery_period':'morning','order_reference':ref,'items':[{'product_id':'ops-product','name':'قهوة تجربة','quantity':1,'unit_price_halalas':amount,'line_total_halalas':amount,'units':[]}]}
            c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer,completed_at) VALUES(%s,%s,%s,'ops-customer','delivery','ready','delivered','paid','cash',%s,0,%s,%s,'{}',now())",(oid,user['brand_id'],user['branch_ids'][0],amount,amount,db.json(snap)))
            c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,delivered_at) VALUES(%s,%s,'ops-driver','test-void-driver','delivered','ops-manager',now())",('task-'+oid,oid))
            c.execute("INSERT INTO collections(id,order_id,shift_id,collector_id,method,amount_halalas) VALUES(%s,%s,'test-void-driver','ops-driver','cash',%s)",('col-'+oid,oid,amount))
            c.execute("INSERT INTO custody_events(id,shift_id,amount_halalas,kind,reference_id,actor_id,reason) VALUES(%s,'test-void-driver',%s,'collection',%s,'ops-driver','TEST fictitious cash')",('event-'+oid,amount,'col-'+oid))
            row=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        result.append((oid,dict(order_reference=ref,expected_version=row['version'],expected_total_halalas=amount,
            reason='TEST morning activation by owner; no actual cash',confirmed_no_cash_received=True)))
    return result


@pytest.mark.postgres
def test_three_voids_reverse_6950_once_preserve_originals_and_exclude_reports(ops):
    actor,post,_,_=ops;orders=seed(ops);accountant()
    with db.tx() as c:
        before=c.execute('SELECT * FROM collections ORDER BY id').fetchall()
        originals=c.execute('SELECT id,snapshot,customer,total_halalas FROM orders ORDER BY id').fetchall()
        original_tasks=c.execute('SELECT * FROM driver_tasks ORDER BY id').fetchall()
        shift=c.execute("SELECT * FROM shifts WHERE id='test-void-driver'").fetchone()
        assert balance(c,shift)==6950
    for oid,body in orders:
        url=f'/api/orders/{oid}/test-void'
        result=post('owner',url,body,key='test-void-'+oid)
        assert result['no_cash_disbursed'] and result['amount_halalas']==body['expected_total_halalas']
        assert post('owner',url,body,key='test-void-'+oid)==result
        post('owner',url,body,status=409)
    shift_view=actor('owner').get('/api/shifts/test-void-driver')
    assert shift_view.status_code==200
    assert all(t['test_void'] for t in shift_view.json()['tasks'])
    with db.tx() as c:
        assert balance(c,shift)==0
        assert c.execute('SELECT * FROM collections ORDER BY id').fetchall()==before
        assert c.execute('SELECT id,snapshot,customer,total_halalas FROM orders ORDER BY id').fetchall()==originals
        assert c.execute("SELECT count(*) n FROM custody_events WHERE kind='refund'").fetchone()['n']==0
        reversals=c.execute("SELECT * FROM refunds WHERE record_kind='test_void'").fetchall()
        assert len(reversals)==3 and sum(r['amount_halalas'] for r in reversals)==6950
        assert all(not r['line_quantities'] for r in reversals)
        assert c.execute("SELECT count(*) n FROM audit_events WHERE action='order.test_void'").fetchone()['n']==3
        assert c.execute('SELECT * FROM driver_tasks ORDER BY id').fetchall()==original_tasks
        from app.reporting import create_snapshot
        c.execute("UPDATE shifts SET status='closed',closed_at=now(),submission=%s WHERE id=%s",(db.json(dict(expected_halalas=0,counted_halalas=0,handover_halalas=0,retained_halalas=0,difference_halalas=0)),shift['id']))
        report_id=create_snapshot(c,shift['id'],'ops-owner')
        report=c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',(report_id,)).fetchone()['snapshot']
        assert report['summary']['completed_orders']==0
        assert report['summary']['test_void_halalas']==6950
        assert report['summary']['cash_collected_halalas']==0
        assert report['summary']['cash_refunded_halalas']==0
        assert report['summary']['net_collections_halalas']==0
        from app.driver_incentives import source_rows
        user=c.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone()
        assert source_rows(c,user,shift_id=shift['id'])==[]
        from app.channel_settlement import view
        for oid,body in orders:
            internal=view(c,oid,{'settlement_amount_halalas':body['expected_total_halalas']})
            assert internal['branch_settlement_halalas']==0 and internal['unallocated_refund_halalas']==0
            assert internal['test_void_no_cash_disbursed'] and not internal['refunds']
        from app.accounting import build
        day=datetime.now().date();start=(day-timedelta(days=1)).isoformat();end=day.isoformat()
        daily=build(c,user,start,end)
        assert sum(r['cash'] for r in daily['rows'])==0
        actions=build(c,user,start,end,group='actions')
        assert len(actions['rows'])==3 and all('Test correction' in r['action'] for r in actions['rows'])
    with pytest.raises(Exception):
        with db.tx() as c:c.execute('DELETE FROM refunds WHERE id=%s',(reversals[0]['id'],))
    with pytest.raises(Exception):
        with db.tx() as c:c.execute('UPDATE orders SET test_void=NULL WHERE id=%s',(orders[0][0],))


@pytest.mark.postgres
def test_permissions_confirmation_staleness_and_closed_shift_block_without_side_effects(ops):
    actor,post,_,_=ops;[(oid,body)]=seed(ops,(2050,));url=f'/api/orders/{oid}/test-void'
    for who in ('manager','driver','branch','customer'):post(who,url,body,status=403)
    post('other',url,body,status=404)
    post('owner',url,{**body,'confirmed_no_cash_received':False},status=422)
    post('owner',url,{**body,'expected_total_halalas':1},status=409)
    with db.tx() as c:c.execute("UPDATE shifts SET status='submitted' WHERE id='test-void-driver'")
    post('owner',url,body,status=409)
    with db.tx() as c:
        assert c.execute("SELECT count(*) n FROM refunds").fetchone()['n']==0
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s',(oid,)).fetchone()['payment_status']=='paid'


@pytest.mark.postgres
def test_backup_restores_test_correction_and_ordinary_nullable_marker(ops,independent_restore_database):
    import psycopg
    from psycopg.rows import dict_row
    from app.exports import build_archive,restore_archive
    _,post,make_order,_=ops
    [(oid,body)]=seed(ops,(2050,));ordinary=make_order()
    post('owner',f'/api/orders/{oid}/test-void',body)
    with db.tx() as c:
        source=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        tasks=c.execute('SELECT * FROM driver_tasks WHERE order_id=%s',(oid,)).fetchall()
        contents,_=build_archive(c,full=True)
    schema='kps_restore_'+uuid4().hex
    assert restore_archive(contents,target_url=independent_restore_database,target_schema=schema)['verified']
    with psycopg.connect(independent_restore_database,row_factory=dict_row) as c:
        from psycopg import sql
        c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        assert c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()==source
        assert c.execute('SELECT * FROM driver_tasks WHERE order_id=%s',(oid,)).fetchall()==tasks
        assert c.execute('SELECT test_void IS NULL missing FROM orders WHERE id=%s',(ordinary,)).fetchone()['missing']
        assert c.execute("SELECT record_kind FROM refunds WHERE order_id=%s",(oid,)).fetchone()['record_kind']=='test_void'


@pytest.mark.postgres
@pytest.mark.parametrize('ops',['fiscal-kps'],indirect=True)
@pytest.mark.parametrize('credit_disabled',[False,True])
def test_issued_invoice_is_preserved_and_credit_must_succeed_atomically(ops,credit_disabled):
    actor,post,_,_=ops;[(oid,body)]=seed(ops,(2050,))
    from app import fiscal_issuance
    with db.tx() as c:
        owner=c.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone()
        c.execute("INSERT INTO fiscal_policy_decisions(id,brand_id,actor_id,decision) VALUES('test-void-fiscal','kps','ops-owner',%s)",(db.json({'enabled':True,'phase':'unconfirmed','issuance_mode':'owner_requested'}),))
        original=deepcopy(fiscal_issuance.issue_sale(c,oid,owner,required=True))
    if credit_disabled:
        with db.tx() as c:c.execute("INSERT INTO fiscal_policy_decisions(id,brand_id,actor_id,decision) VALUES('test-void-disabled','kps','ops-owner','{\"enabled\":false}')")
    post('owner',f'/api/orders/{oid}/test-void',body,status=409 if credit_disabled else 200)
    with db.tx() as c:
        assert fiscal_issuance.original(c,oid)==original
        credits=c.execute("SELECT * FROM fiscal_documents WHERE order_id=%s AND kind='credit_note'",(oid,)).fetchall()
        assert len(credits)==(0 if credit_disabled else 1)
        if credits:
            assert credits[0]['document']['total_halalas']==2050
            assert c.execute('SELECT record_kind FROM refunds WHERE id=%s',(credits[0]['refund_id'],)).fetchone()['record_kind']=='test_void'
        else:
            assert not c.execute('SELECT 1 FROM refunds WHERE order_id=%s',(oid,)).fetchone()
            assert balance(c,{'id':'test-void-driver','opening_halalas':0})==2050


def seed_unpaid_cancelled(ops):
    _,_,_,_=ops
    oid='unpaid-test-'+uuid4().hex
    with db.tx() as c:
        user=c.execute("SELECT brand_id,branch_ids FROM users WHERE id='ops-driver'").fetchone()
        for sid,kind,person,parent,status in [('unpaid-manager','manager','ops-manager',None,'closing'),('unpaid-driver','driver','ops-driver','unpaid-manager','submitted')]:
            c.execute("INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,status,manager_shift_id,delivery_window_snapshot) VALUES(%s,%s,%s,%s,%s,%s,%s,'{\"period\":\"morning\"}')",(sid,kind,person,user['brand_id'],db.json(user['branch_ids']),status,parent))
        snap={'delivery_period':'morning','order_reference':'UNPAID-TEST','items':[]}
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,%s,%s,'ops-customer','delivery','cancelled','cancelled','unpaid','cash',2050,0,2050,%s,'{}')",(oid,user['brand_id'],user['branch_ids'][0],db.json(snap)))
        c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by,reason) VALUES(%s,%s,'ops-driver','unpaid-driver','cancelled','ops-manager','TEST cancelled by owner')",('task-'+oid,oid))
        order=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
    return oid,dict(order_reference='UNPAID-TEST',expected_version=order['version'],expected_total_halalas=2050,reason='TEST unpaid cancellation by owner; no actual cash',confirmed_no_cash_received=True)


@pytest.mark.postgres
def test_unpaid_cancelled_test_removes_entitlement_block_without_financial_mutations(ops):
    actor,post,_,_=ops;oid,body=seed_unpaid_cancelled(ops);url=f'/api/orders/{oid}/test-void'
    with db.tx() as c:
        original=c.execute('SELECT * FROM driver_tasks WHERE order_id=%s',(oid,)).fetchall()
        ledgers={table:c.execute(f'SELECT * FROM {table} ORDER BY id').fetchall() for table in ('collections','refunds','custody_events','custody_transfers')}
        owner=c.execute("SELECT * FROM users WHERE id='ops-owner'").fetchone()
        from app.driver_incentives import source_rows,classify
        assert classify(source_rows(c,owner,order_id=oid)[0])['state']=='pending_review'
    result=post('owner',url,body,key='unpaid-test-once')
    assert result['amount_halalas']==0 and result['no_cash_received'] and result['no_cash_disbursed']
    assert result['credit_note'] is None
    assert post('owner',url,body,key='unpaid-test-once')==result
    post('owner',url,body,status=409)
    with db.tx() as c:
        row=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert row['payment_status']=='unpaid' and row['delivery_status']=='cancelled'
        assert row['test_void']['kind']=='unpaid_cancelled_test'
        assert row['test_void']['actor_id']=='ops-owner'
        assert row['total_halalas']==2050 and row['version']==body['expected_version']+1
        assert source_rows(c,owner,order_id=oid)==[]
        assert c.execute('SELECT * FROM driver_tasks WHERE order_id=%s',(oid,)).fetchall()==original
        for table,rows in ledgers.items():assert c.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()==rows
        assert c.execute("SELECT count(*) n FROM audit_events WHERE entity_id=%s AND action='order.test_void'",(oid,)).fetchone()['n']==1
        assert c.execute("SELECT status FROM shifts WHERE id='unpaid-driver'").fetchone()['status']=='submitted'
    for who in ('manager','driver','branch','customer'):post(who,url,body,status=403)
    post('other',url,body,status=404)


@pytest.mark.postgres
@pytest.mark.parametrize('case',['driver_closed','manager_closed','driver_evening','manager_evening','live_task','collection','reference','confirmation'])
def test_unpaid_test_rejects_unsafe_or_stale_classification(ops,case):
    _,post,_,_=ops;oid,body=seed_unpaid_cancelled(ops)
    with db.tx() as c:
        if case in ('driver_closed','manager_closed'):
            c.execute("UPDATE shifts SET status='closed',closed_at=now() WHERE id=%s",('unpaid-driver' if case=='driver_closed' else 'unpaid-manager',))
        if case in ('driver_evening','manager_evening'):
            c.execute("UPDATE shifts SET delivery_window_snapshot='{\"period\":\"evening\"}' WHERE id=%s",('unpaid-driver' if case=='driver_evening' else 'unpaid-manager',))
        if case=='live_task':c.execute("UPDATE driver_tasks SET status='accepted' WHERE order_id=%s",(oid,))
        if case=='collection':c.execute("INSERT INTO collections(id,order_id,shift_id,collector_id,method,amount_halalas) VALUES('unsafe-col',%s,'unpaid-driver','ops-driver','cash',2050)",(oid,))
    if case=='reference':body['order_reference']='ANOTHER-ORDER'
    if case=='confirmation':body['confirmed_no_cash_received']=False
    post('owner',f'/api/orders/{oid}/test-void',body,status=422 if case=='confirmation' else 409)
    with db.tx() as c:
        assert c.execute('SELECT test_void FROM orders WHERE id=%s',(oid,)).fetchone()['test_void'] is None
        assert c.execute('SELECT count(*) n FROM refunds WHERE order_id=%s',(oid,)).fetchone()['n']==0
