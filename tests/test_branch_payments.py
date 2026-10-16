"""Real PostgreSQL ledger with synthetic provider HTTP, never a merchant charge."""
from uuid import uuid4
import httpx
import pytest
import psycopg
from app import db, payment_gateways, order_amendments
from test_operations import ops

pytestmark = pytest.mark.postgres


@pytest.fixture
def branch_payment(ops, monkeypatch):
    actor, post, create, shifts = ops
    monkeypatch.setenv('KPS_BRANCH_PAYMENT_SANDBOX_ENABLED', 'true')
    monkeypatch.setenv('KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED', 'true')
    monkeypatch.setenv(payment_gateways.credential_prefix('moyasar','ops-brand','fixture-seller')+'_TEST_SECRET_KEY', 'sk_test_synthetic_never_sent')
    oid = 'branch-payment-'+uuid4().hex
    with db.tx() as c:
        c.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) VALUES(%s,'ops-brand','ops-branch','ops-customer','car','new','not_applicable','card',1000,0,1000,%s,'{}')",
                  (oid,db.json({'items':[],'branch_payment_mode':'test','branch_payment_seller':'fixture-seller'})))
    return actor,post,oid


def accept(post, oid):
    return post('owner','/api/orders/'+oid+'/preparation',{'status':'accepted','version':1})['order']['version']


def attempt(post, oid, version):
    return post('customer','/api/branch-payments/orders/'+oid+'/attempt',{'version':version})['attempt']


def provider(monkeypatch, aid, change=None):
    with db.tx() as c:
        row = c.execute('SELECT * FROM branch_payment_attempts WHERE id=%s',(aid,)).fetchone()
    context = payment_gateways.PaymentContext(row['brand_id'],row['seller_id'],row['order_id'],aid,row['amount_halalas'])
    payload = {'id':str(row['provider_payment_id']),'amount':row['amount_halalas'],'currency':'SAR',
               'status':'paid','refunded':0,'live':False,'metadata':context.metadata}
    payload.update(change or {})
    calls=[]
    original = httpx.Client
    def handle(request):
        calls.append(request)
        assert request.method == 'GET'
        assert str(request.url) == 'https://api.moyasar.com/v1/payments/'+str(row['provider_payment_id'])
        return httpx.Response(200,json=payload)
    monkeypatch.setattr(payment_gateways.httpx,'Client',lambda **kw: original(transport=httpx.MockTransport(handle),**kw))
    return calls


def test_accept_payment_prepare_arrive_handover_and_no_double_collection(branch_payment, monkeypatch):
    actor,post,oid=branch_payment
    post('customer','/api/branch-payments/orders/'+oid+'/attempt',{'version':1},status=409)
    version=accept(post,oid)
    first=attempt(post,oid,version)
    assert attempt(post,oid,version)==first
    post('owner','/api/orders/'+oid+'/preparation',{'status':'preparing','version':version},status=409)
    post('owner','/api/orders/'+oid+'/collect',{'method':'card','amount_halalas':1000,'reference':'fake'},status=409)
    calls=provider(monkeypatch,first['id'])
    url='/api/branch-payments/attempts/'+first['id']+'/reconcile'
    assert post('customer',url)['attempt']['status']=='verified'
    assert post('owner',url)['attempt']['status']=='verified'
    assert len(calls)==1
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert o['payment_status']=='paid'
        assert order_amendments.balance(c,o)==1000
        assert c.execute('SELECT count(*) n FROM branch_online_receipts WHERE order_id=%s',(oid,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) n FROM collections WHERE order_id=%s',(oid,)).fetchone()['n']==0
    arrival=post('customer','/api/orders/'+oid+'/arrival',{'version':o['version']})['order']
    for status in ['preparing','ready','completed']:
        arrival=post('owner','/api/orders/'+oid+'/preparation',{'status':status,'version':arrival['version']})['order']
    assert arrival['arrival_at'] and arrival['completed_at']
    post('owner','/api/orders/'+oid+'/preparation',{'status':'completed','version':arrival['version']},status=409)
    post('owner','/api/orders/'+oid+'/refund',{'method':'card','amount_halalas':1000,'reason':'Must use provider'},status=409)


@pytest.mark.parametrize('change',[{'amount':999},{'status':'failed'},{'status':'initiated'},{'live':True},{'metadata':{}},{'currency':'USD'}])
def test_unverified_response_never_pays_or_prepares(branch_payment, monkeypatch, change):
    actor,post,oid=branch_payment
    version=accept(post,oid); a=attempt(post,oid,version)
    provider(monkeypatch,a['id'],change)
    post('customer','/api/branch-payments/attempts/'+a['id']+'/reconcile',status=409)
    with db.tx() as c:
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s',(oid,)).fetchone()['payment_status']=='unpaid'
        assert c.execute('SELECT count(*) n FROM branch_online_receipts').fetchone()['n']==0
    post('owner','/api/orders/'+oid+'/preparation',{'status':'preparing','version':version},status=409)


def test_late_success_after_cancel_is_saved_without_resuming_order(branch_payment,monkeypatch):
    actor,post,oid=branch_payment
    version=accept(post,oid); a=attempt(post,oid,version)
    post('owner','/api/orders/'+oid+'/preparation',{'status':'cancelled','reason':'Synthetic cancellation','version':version})
    provider(monkeypatch,a['id'])
    assert post('customer','/api/branch-payments/attempts/'+a['id']+'/reconcile')['attempt']['status']=='review_required'
    with db.tx() as c:
        o=c.execute('SELECT * FROM orders WHERE id=%s',(oid,)).fetchone()
        assert o['preparation_status']=='cancelled' and o['payment_status']=='unpaid'
        assert order_amendments.balance(c,o)==1000


def test_disabled_scope_and_immutable_records(branch_payment,monkeypatch):
    actor,post,oid=branch_payment
    version=accept(post,oid)
    monkeypatch.delenv('KPS_BRANCH_PAYMENT_SANDBOX_ENABLED')
    post('customer','/api/branch-payments/orders/'+oid+'/attempt',{'version':version},status=409)
    monkeypatch.setenv('KPS_BRANCH_PAYMENT_SANDBOX_ENABLED','true')
    a=attempt(post,oid,version)
    post('other','/api/branch-payments/attempts/'+a['id']+'/reconcile',status=404)
    post('driver','/api/branch-payments/attempts/'+a['id']+'/reconcile',status=403)
    for statement in ['UPDATE branch_payment_attempts SET amount_halalas=2000 WHERE id=%s',
                      'DELETE FROM branch_payment_attempts WHERE id=%s']:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.tx() as c:c.execute(statement,(a['id'],))
    provider(monkeypatch,a['id'])
    post('customer','/api/branch-payments/attempts/'+a['id']+'/reconcile')
    with pytest.raises(psycopg.errors.RaiseException):
        with db.tx() as c:c.execute('UPDATE branch_online_receipts SET amount_halalas=1')
