"""Synthetic PostgreSQL witnesses for late-entry scope and ledger preservation."""
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import pytest
from app import db, service_hours
from test_enbishan_dual_brand import dual


@pytest.fixture
def prior_setup(dual):
    client,actor,post,_,order=dual
    actual=datetime.now(timezone.utc)-timedelta(hours=10)
    day=service_hours.delivery_operational_date(actual)
    with db.tx() as c:
        for sid,kind,uid,parent in [('prior-manager','manager','dual-manager',None),('prior-driver','driver','dual-driver','prior-manager')]:
            c.execute('INSERT INTO shifts(id,kind,user_id,brand_id,branch_ids,manager_shift_id,opened_at,delivery_operational_date) VALUES(%s,%s,%s,\'enbishan\',%s,%s,%s,%s)',(sid,kind,uid,db.json(['enbishan-test-1']),parent,actual-timedelta(hours=1),day))
    grant=post('owner','/api/prior-deliveries/grants',{'manager_shift_id':'prior-manager','driver_shift_ids':['prior-driver'],'reason':'Synthetic owner authorization'},brand='enbishan')['grant']
    meta={'driver_shift_id':'prior-driver','actual_delivered_at':actual.isoformat(),'source_reference':'SYNTHETIC-REF-1','cash_halalas':400,'card_halalas':600,'reason':'Synthetic prior delivery attestation','confirmed':True}
    def pending():
        oid=order('enbishan')
        with db.tx() as c:
            c.execute("INSERT INTO driver_tasks(id,order_id,driver_id,shift_id,status,assigned_by) VALUES(%s,%s,'dual-driver','prior-driver','accepted','dual-manager')",('task-'+oid,oid))
            c.execute("UPDATE orders SET preparation_status='accepted',delivery_status='accepted' WHERE id=%s",(oid,))
            version=c.execute('SELECT version FROM orders WHERE id=%s',(oid,)).fetchone()['version']
        return {'prior':deepcopy(meta),'order_id':oid,'expected_version':version}
    return actor,post,grant,meta,pending,actual


def path(g,action='record'):return '/api/prior-deliveries/grants/'+g['id']+'/'+action


@pytest.mark.postgres
def test_existing_closing_order_is_completed_once_with_actual_split(prior_setup):
    actor,post,g,meta,pending,actual=prior_setup
    body=pending()
    with db.tx() as c:c.execute("UPDATE shifts SET status='closing' WHERE id='prior-driver'")
    result=post('manager',path(g),body,brand='enbishan',key='one-prior-entry')
    assert post('manager',path(g),body,brand='enbishan',key='one-prior-entry')==result
    post('manager',path(g),body,brand='enbishan',status=409)
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(body['order_id'],)).fetchone()
        assert o['payment_status']=='paid' and o['delivery_status']=='delivered' and o['completed_at']==actual
        assert o['delivery_operational_date']==service_hours.delivery_operational_date(actual)
        payments=c.execute('SELECT * FROM collections WHERE order_id=%s',(o['id'],)).fetchall()
        assert {(p['method'],p['amount_halalas']) for p in payments}=={('cash',400),('card',600)}
        assert all(p['collector_id']=='dual-driver' and p['created_at']==actual for p in payments)
        assert c.execute("SELECT status FROM shifts WHERE id='prior-driver'").fetchone()['status']=='closing'
        assert c.execute('SELECT sum(amount_halalas) n FROM custody_events WHERE shift_id=\'prior-driver\'').fetchone()['n']==400
        assert c.execute('SELECT count(*) n FROM custody_transfers').fetchone()['n']==0
        t=c.execute('SELECT * FROM driver_tasks WHERE order_id=%s',(o['id'],)).fetchone()
        assert t['picked_up_at'] is None and t['delivered_at']==actual


@pytest.mark.postgres
def test_mismatch_unknown_time_and_wrong_driver_do_not_post(prior_setup):
    actor,post,g,meta,pending,actual=prior_setup
    body=pending()
    for change,status in [({'cash_halalas':0},422),({'confirmed':False},422),({'actual_delivered_at':(actual+timedelta(days=2)).isoformat()},422),({'driver_shift_id':'missing'},403)]:
        bad=deepcopy(body);bad['prior'].update(change)
        post('manager',path(g),bad,brand='enbishan',status=status)
    with db.tx() as c:
        assert c.execute('SELECT count(*) n FROM collections').fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM prior_delivery_records').fetchone()['n']==0
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s',(body['order_id'],)).fetchone()['payment_status']=='unpaid'


@pytest.mark.postgres
def test_closed_shift_gets_separate_linked_supplement(prior_setup):
    actor,post,g,meta,pending,actual=prior_setup
    from app import reporting
    from app.accounting import driver_settlement_values
    from app.shift_reconciliation import closed_shift
    with db.tx() as c:
        c.execute("UPDATE shifts SET status='closed',closed_at=%s,submission=%s WHERE id='prior-driver'",(actual+timedelta(minutes=10),db.json({'expected_halalas':0,'counted_halalas':0,'handover_halalas':0,'difference_halalas':0})))
        rid=reporting.create_snapshot(c,'prior-driver','dual-owner')
        before=c.execute('SELECT * FROM closure_reports WHERE id=%s',(rid,)).fetchone()['snapshot']
    # A synthetic preexisting uncollected late task also uses the supplement.
    body=pending()
    result=post('manager',path(g),body,brand='enbishan')
    with db.tx() as c:
        original=c.execute('SELECT * FROM shifts WHERE id=\'prior-driver\'').fetchone()
        assert original['status']=='closed'
        assert c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',(rid,)).fetchone()['snapshot']==before
        supplement=c.execute('SELECT * FROM shifts WHERE supplement_of_shift_id=\'prior-driver\'').fetchone()
        assert supplement['status']=='closed' and supplement['opened_at']==supplement['closed_at']
        assert supplement['opening_halalas']==0 and not supplement['permanent_float'] and not supplement['vehicle_log']
        snap=c.execute('SELECT snapshot FROM closure_reports WHERE id=%s',(result['supplement_report_id'],)).fetchone()['snapshot']
        assert snap['summary']['completed_orders']==1 and snap['summary']['net_collections_halalas']==1000
        assert snap['supplement_of_shift_id']=='prior-driver'
        values=driver_settlement_values(snap)
        assert values['pending_cash_halalas']==400 and values['system_bank_halalas']==600
        assert not values['reconciliation_complete'] and values['cash_received_halalas'] is None
        assert len(closed_shift(c,original)['supplements'])==1


@pytest.mark.postgres
def test_grant_revoke_and_fresh_membership_block_replay(prior_setup):
    actor,post,g,meta,pending,actual=prior_setup
    body=pending()
    post('manager',path(g),body,brand='enbishan',key='record-before-revoke')
    post('owner','/api/prior-deliveries/grants/'+g['id']+'/revoke',{'reason':'Synthetic owner revocation'},brand='enbishan')
    post('manager',path(g),body,brand='enbishan',key='record-before-revoke',status=403)
    assert not actor('manager','enbishan').get('/api/prior-deliveries/options').json()['grants']


@pytest.mark.postgres
def test_manager_cannot_authorize_and_other_brand_is_rejected(prior_setup):
    actor,post,g,meta,pending,actual=prior_setup
    post('manager','/api/prior-deliveries/grants',{'manager_shift_id':'prior-manager','driver_shift_ids':['prior-driver'],'reason':'Synthetic unauthorized grant'},brand='enbishan',status=403)
    post('manager',path(g),pending(),brand='kps',status=403)
    with db.tx() as c:c.execute("UPDATE staff_brand_memberships SET active=false WHERE user_id='dual-driver' AND brand_id='enbishan'")
    post('manager',path(g),pending(),brand='enbishan',status=403)


@pytest.mark.postgres
def test_new_missing_order_uses_price_and_actual_date_without_public_opening(prior_setup):
    actor,post,g,meta,pending,actual=prior_setup
    from app import catalog
    with db.tx() as c:
        branch=c.execute("SELECT * FROM branches WHERE id='enbishan-test-1'").fetchone()
        hours=[{'days':[service_hours.now_riyadh().weekday()],'start':'00:01','end':'00:02'}]
        branch['settings'].update(opening_hours=hours,delivery_hours=hours)
        c.execute('UPDATE branches SET settings=%s WHERE id=%s',(db.json(branch['settings']),branch['id']))
        assert not catalog.branch_service(branch,'delivery')[0]
    new={'brand_id':'enbishan','branch_id':'enbishan-test-1','service':'delivery','payment_method':'split',
         'customer':{'name':'SYNTHETIC','phone':'+966500000111','neighborhood':'TEST','address':'TEST','location':{'lat':28.4,'lng':45.9}},
         'items':[{'product_id':'dual-enbishan','quantity':1}]}
    body={'prior':{**meta,'cash_halalas':1000,'card_halalas':1500},'order':new}
    priced=post('manager',path(g,'quote'),body,brand='enbishan')['quote']
    assert priced['total_halalas']==2500
    body['order']['expected_total_halalas']=2500
    result=post('manager',path(g),body,brand='enbishan')
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(result['order']['id'],)).fetchone()
        assert o['created_at']>actual and o['completed_at']==actual
        assert o['delivery_operational_date']==service_hours.delivery_operational_date(actual)
        assert o['snapshot']['prior_delivery']['cash_receipt_confirmed'] is False
        assert c.execute('SELECT count(*) n FROM branch_print_jobs WHERE order_id=%s',(o['id'],)).fetchone()['n']==0
        assert c.execute('SELECT count(*) n FROM fiscal_documents WHERE order_id=%s',(o['id'],)).fetchone()['n']==0
    post('manager',path(g),body,brand='enbishan',status=409)


@pytest.mark.postgres
def test_prior_records_and_supplements_survive_restore_and_brand_export(prior_setup):
    import os
    from uuid import uuid4
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from app.exports import build_archive, restore_archive
    actor,post,g,meta,pending,actual=prior_setup
    body=pending()
    with db.tx() as c:
        c.execute("UPDATE shifts SET status='closed',closed_at=%s WHERE id='prior-driver'",(actual+timedelta(minutes=10),))
    post('manager',path(g),body,brand='enbishan')
    post('owner','/api/prior-deliveries/grants/'+g['id']+'/revoke',{'reason':'Synthetic completed entry'},brand='enbishan')
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        archive,manifest=build_archive(c,full=True)
        _,enbishan=build_archive(c,brand_id='enbishan')
        _,kps=build_archive(c,brand_id='kps')
        originals={table:c.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table))).fetchall() for table in ['prior_delivery_grants','prior_delivery_revocations','prior_delivery_records']}
        for table in originals:
            assert enbishan['counts'][table]==1 and kps['counts'][table]==0
    target=os.environ['TEST_DATABASE_URL'];schema='kps_restore_'+uuid4().hex
    try:
        restore_archive(archive,target_url=target,target_schema=schema)
        with psycopg.connect(target,row_factory=dict_row) as c:
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            for table,rows in originals.items():
                assert c.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table))).fetchall()==rows
            assert c.execute("SELECT count(*) n FROM shifts WHERE supplement_of_shift_id='prior-driver'").fetchone()['n']==1
    finally:
        with psycopg.connect(target,autocommit=True) as c:
            c.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))


@pytest.mark.postgres
def test_early_manager_day_keeps_original_ledger_and_grants_attached_driver_day(prior_setup):
    actor,post,g,meta,pending,actual=prior_setup
    day=service_hours.delivery_operational_date(actual)
    early=datetime.combine(day,datetime.min.time(),tzinfo=timezone(timedelta(hours=3)))+timedelta(hours=15,minutes=45)
    with db.tx() as c:
        c.execute("UPDATE shifts SET opened_at=%s,delivery_operational_date=%s WHERE id='prior-manager'",(early,day-timedelta(days=1)))
    body={'manager_shift_id':'prior-manager','driver_shift_ids':['prior-driver'],'reason':'Synthetic early opening boundary'}
    new=post('owner','/api/prior-deliveries/grants',body,brand='enbishan')['grant']
    assert new['operational_date']==day.isoformat()
    with db.tx() as c:
        assert c.execute("SELECT delivery_operational_date FROM shifts WHERE id='prior-manager'").fetchone()['delivery_operational_date']==day-timedelta(days=1)
        c.execute("UPDATE shifts SET delivery_operational_date=%s WHERE id='prior-driver'",(day+timedelta(days=1),))
    post('owner','/api/prior-deliveries/grants',body,brand='enbishan',status=422)
