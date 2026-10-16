"""Real PostgreSQL checkout claims, synthetic intent adapter, no merchant traffic."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from cryptography.fernet import Fernet
import pytest
from app import db, payment_intents, payment_gateways
from test_branch_payments import branch_payment, accept, attempt
from test_operations import ops

pytestmark=pytest.mark.postgres


@pytest.fixture
def checkout_case(branch_payment,monkeypatch):
    actor,post,oid=branch_payment
    monkeypatch.setenv('KPS_BRANCH_PAYMENT_CHECKOUT_ENABLED','true')
    monkeypatch.setenv('KPS_PAYMENT_CHECKOUT_ENCRYPTION_KEY',Fernet.generate_key().decode())
    monkeypatch.setenv(payment_gateways.credential_prefix('moyasar','ops-brand','fixture-seller')+'_TEST_PUBLIC_KEY','pk_test_synthetic_only')
    a=attempt(post,oid,accept(post,oid))
    calls=[]
    def create(context):
        calls.append(context)
        return payment_intents.CheckoutIntent('moyasar',str(uuid4()),context,
            datetime.now(timezone.utc)+timedelta(hours=1),
            'https://fixture.invalid/payment-return?attempt='+context.attempt_id,'pi_secret_synthetic_checkout')
    monkeypatch.setattr(payment_intents,'create_moyasar_sandbox_intent',create)
    return actor,post,oid,a,calls


def test_checkout_reuses_encrypted_session_and_never_marks_paid(checkout_case):
    actor,post,oid,a,calls=checkout_case
    url='/api/branch-payments/attempts/'+a['id']+'/checkout'
    first=actor('customer').post(url)
    assert first.status_code==200,first.text
    assert first.headers['cache-control']=='no-store'
    assert post('customer',url)==first.json()
    assert len(calls)==1
    with db.tx() as c:
        row=c.execute('SELECT * FROM branch_checkout_sessions WHERE attempt_id=%s',(a['id'],)).fetchone()
        assert b'pi_secret_synthetic_checkout' not in bytes(row['encrypted_secret'])
        assert c.execute('SELECT payment_status FROM orders WHERE id=%s',(oid,)).fetchone()['payment_status']=='unpaid'
        assert c.execute('SELECT count(*) n FROM branch_online_receipts').fetchone()['n']==0
    post('owner',url,status=403)
    other=actor('other')
    assert other.post(url).status_code==403


def test_concurrent_checkout_creates_only_one_provider_intent(checkout_case):
    actor,post,oid,a,calls=checkout_case
    client=actor('customer')
    def submit(_):return client.post('/api/branch-payments/attempts/'+a['id']+'/checkout')
    with ThreadPoolExecutor(max_workers=4) as pool:responses=list(pool.map(submit,range(4)))
    assert all(r.status_code in {200,409} for r in responses)
    assert any(r.status_code==200 for r in responses)
    assert len(calls)==1
    ready=post('customer','/api/branch-payments/attempts/'+a['id']+'/checkout')
    assert all(r.json()==ready for r in responses if r.status_code==200)


def test_uncertain_creation_is_not_automatically_repeated(checkout_case,monkeypatch):
    actor,post,oid,a,calls=checkout_case
    def fail(context):
        calls.append(context)
        raise db.error(503,'PAYMENT_INTENT_UNCONFIRMED','Synthetic timeout')
    monkeypatch.setattr(payment_intents,'create_moyasar_sandbox_intent',fail)
    url='/api/branch-payments/attempts/'+a['id']+'/checkout'
    post('customer',url,status=503)
    post('customer',url,status=409)
    assert len(calls)==1
    with db.tx() as c:
        assert c.execute('SELECT state FROM branch_checkout_sessions WHERE attempt_id=%s',(a['id'],)).fetchone()['state']=='uncertain'


def test_cancelled_order_cannot_obtain_prepared_secret(checkout_case):
    actor,post,oid,a,calls=checkout_case
    url='/api/branch-payments/attempts/'+a['id']+'/checkout'
    post('customer',url)
    with db.tx() as c:version=c.execute('SELECT version FROM orders WHERE id=%s',(oid,)).fetchone()['version']
    post('owner','/api/orders/'+oid+'/preparation',{'status':'cancelled','reason':'Test cancellation','version':version})
    post('customer',url,status=409)
    assert len(calls)==1


@pytest.mark.parametrize('key',['KPS_BRANCH_PAYMENT_CHECKOUT_ENABLED','KPS_PAYMENT_CHECKOUT_ENCRYPTION_KEY'])
def test_missing_configuration_does_not_contact_provider(checkout_case,monkeypatch,key):
    actor,post,oid,a,calls=checkout_case
    monkeypatch.delenv(key)
    post('customer','/api/branch-payments/attempts/'+a['id']+'/checkout',status=409 if key.endswith('ENABLED') else 503)
    assert not calls
