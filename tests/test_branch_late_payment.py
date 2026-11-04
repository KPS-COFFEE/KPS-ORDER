"""Late superseded receipts: real PG, synthetic provider evidence only."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from app import db, branch_payments as payments, payment_gateways, payment_refunds
from test_operations import ops
from test_branch_payments import branch_payment, accept, attempt
from test_backup import independent_restore_database

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize('successor_paid',[False,True])
def test_late_superseded_payment_is_saved_once_and_refunded_independently(branch_payment,monkeypatch,successor_paid,independent_restore_database,tmp_path):
    actor,post,oid=branch_payment
    version=accept(post,oid)
    old=attempt(post,oid,version)
    new=post('customer','/api/branch-payments/attempts/'+old['id']+'/renew')['attempt']
    adapter=payment_gateways.adapter_for('moyasar')
    monkeypatch.setattr(adapter,'verify_sandbox_payment',lambda ctx,pid:
        payment_gateways.VerifiedPayment('moyasar',pid,ctx,'test'))
    if successor_paid:
        assert post('customer','/api/branch-payments/attempts/'+new['id']+'/reconcile')['attempt']['status']=='verified'
    with db.tx() as c:
        original=dict(c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone())
        retired=c.execute('SELECT * FROM branch_payment_attempts WHERE id=%s',(old['id'],)).fetchone()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:payments.verify_attempt(retired),range(4)))
    assert all(r['attempt']['status']=='review_required' for r in results)
    assert post('customer','/api/branch-payments/attempts/'+old['id']+'/reconcile')['attempt']['status']=='review_required'
    with db.tx() as c:
        assert dict(c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone())==original
        assert c.execute('SELECT count(*) n FROM branch_online_receipts WHERE order_id=%s',(oid,)).fetchone()['n']==1+successor_paid
        assert c.execute('SELECT status FROM branch_payment_attempts WHERE id=%s',(old['id'],)).fetchone()['status']=='superseded'
    post('owner','/api/orders/'+oid+'/preparation',{'status':'preparing','version':original['version']},status=409)
    post('customer','/api/branch-payments/attempts/'+new['id']+'/renew',status=409)
    monkeypatch.setenv('KPS_BRANCH_PAYMENT_REFUNDS_ENABLED','true')
    history=actor('owner').get('/api/branch-payments/orders/'+oid+'/refunds').json()
    assert history['enabled']
    assert old['id'] in {p['attempt_id'] for p in history['payments']}
    payload={'version':original['version'],'attempt_id':old['id'],'amount_halalas':1000,'reason':'Late duplicate receipt'}
    post('customer','/api/branch-payments/orders/'+oid+'/refunds',payload,status=403)
    post('owner','/api/branch-payments/orders/'+oid+'/refunds',payload|{'attempt_id':'unrelated-attempt'},status=409)
    refund=post('owner','/api/branch-payments/orders/'+oid+'/refunds',payload)['refund']
    calls=[]; observed=[0]
    def request(ctx,pid,amount=None):
        assert ctx.attempt_id==old['id'] and pid==old['provider_payment_id']
        calls.append(amount)
        if amount is not None:observed[0]+=amount
        return observed[0]
    monkeypatch.setattr(payment_refunds,'request',request)
    for _ in range(2):
        assert post('owner','/api/branch-payments/refunds/'+refund['id']+'/execute')['refund']['status']=='verified'
    assert calls==[None,1000,None]
    with db.tx() as c:
        assert dict(c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone())==original
        assert not payments.review_required(c,oid)
    if not successor_paid:
        assert post('customer','/api/branch-payments/attempts/'+new['id']+'/reconcile')['attempt']['status']=='verified'
    with db.tx() as c:
        order=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
    post('owner','/api/orders/'+oid+'/preparation',{'status':'preparing','version':order['version']})
    report=actor('owner').get('/api/branch-payments/report?branch_id=ops-branch').json()
    assert report['totals']['received_halalas']==2000
    assert report['totals']['refunded_halalas']==1000
    assert report['totals']['net_movement_halalas']==1000
    import psycopg
    from psycopg import sql
    from uuid import uuid4
    from app.exports import build_archive,restore_archive
    with db.tx() as c:
        archive,manifest=build_archive(c,full=True)
    assert manifest['counts']['branch_online_receipts']==2
    schema='kps_restore_'+uuid4().hex
    assert restore_archive(archive,target_url=independent_restore_database,target_schema=schema,
                           asset_directory=tmp_path/'assets')['verified']
    with psycopg.connect(independent_restore_database) as c:
        c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
        assert c.execute('SELECT count(*) FROM branch_online_receipts WHERE order_id=%s',(oid,)).fetchone()[0]==2
        assert c.execute('SELECT count(*) FROM branch_online_refunds WHERE attempt_id=%s AND status=%s',(old['id'],'verified')).fetchone()[0]==1
