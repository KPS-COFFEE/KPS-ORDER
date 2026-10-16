"""Provider HTTP simulation only; no actual merchant requests or card data."""
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4
import httpx
import pytest
from fastapi import HTTPException
from app import payment_gateways as gateways, payment_intents as intents


@pytest.fixture
def intent_provider(monkeypatch):
    context = gateways.PaymentContext('brand','seller','order','attempt',1250)
    monkeypatch.setenv('KPS_BRANCH_PAYMENT_SANDBOX_ENABLED','true')
    monkeypatch.setenv('KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED','true')
    monkeypatch.setenv('PUBLIC_BASE_URL','https://checkout.fixture.invalid')
    prefix = gateways.credential_prefix('moyasar','brand','seller')
    monkeypatch.setenv(prefix+'_TEST_SECRET_KEY','sk_test_synthetic_only')
    calls=[]
    change={}
    original=httpx.Client
    def handle(request):
        calls.append(request)
        assert request.method=='POST'
        assert str(request.url)=='https://api.moyasar.com/v1/payment_intents'
        payload=json.loads(request.content)
        assert set(payload)=={'amount','currency','description','callback_url'}
        assert payload['amount']==1250 and payload['currency']=='SAR'
        data={**payload,'id':str(uuid4()),'status':'created','client_secret':'pi_secret_synthetic_only',
              'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}
        data.update(change)
        return httpx.Response(201,json=data)
    monkeypatch.setattr(intents.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    return context,calls,change


def test_server_amount_and_seller_scoped_sandbox_intent(intent_provider):
    context,calls,_=intent_provider
    result=intents.create_moyasar_sandbox_intent(context)
    assert result.context==context and result.mode=='test'
    assert result.callback_url=='https://checkout.fixture.invalid/payment-return?attempt=attempt'
    assert result.client_secret not in repr(result)
    assert len(calls)==1


@pytest.mark.parametrize('change',[
    {'amount':1251},{'amount':True},{'currency':'USD'},{'status':'fulfilled'},
    {'callback_url':'https://attacker.invalid'},{'description':'other order'},
    {'id':'not-a-uuid'},{'client_secret':'sk_live_rejected'},
    {'expires_at':'2000-01-01T00:00:00+00:00'},{'expires_at':'2030-01-01T00:00:00'},
])
def test_mismatched_intent_is_never_exposed(intent_provider,change):
    context,calls,changes=intent_provider
    changes.update(change)
    with pytest.raises(HTTPException) as caught:intents.create_moyasar_sandbox_intent(context)
    assert caught.value.detail['code']=='PAYMENT_INTENT_UNCONFIRMED'
    assert len(calls)==1
    assert 'synthetic_only' not in str(caught.value)


@pytest.mark.parametrize('url',['','http://checkout.fixture.invalid','https://user:pass@checkout.fixture.invalid',
                               'https://checkout.fixture.invalid/other','https://checkout.fixture.invalid?x=1',
                               'https://checkout.fixture.invalid#fragment','https://checkout.fixture.invalid:bad'])
def test_invalid_return_configuration_stops_before_provider(intent_provider,monkeypatch,url):
    context,calls,_=intent_provider
    monkeypatch.setenv('PUBLIC_BASE_URL',url)
    with pytest.raises(HTTPException):intents.create_moyasar_sandbox_intent(context)
    assert not calls


@pytest.mark.parametrize('setting',['KPS_BRANCH_PAYMENT_SANDBOX_ENABLED','KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED'])
def test_disabled_gate_never_contacts_provider(intent_provider,monkeypatch,setting):
    context,calls,_=intent_provider
    monkeypatch.delenv(setting)
    with pytest.raises(HTTPException):intents.create_moyasar_sandbox_intent(context)
    assert not calls


def test_other_seller_cannot_reuse_credentials(intent_provider):
    context,calls,_=intent_provider
    with pytest.raises(HTTPException):
        intents.create_moyasar_sandbox_intent(gateways.PaymentContext('brand','other-seller','order','attempt',1250))
    assert not calls


def test_post_timeout_is_not_retried_and_does_not_leak_provider_data(intent_provider,monkeypatch):
    context,_,_=intent_provider
    calls=[]
    original=httpx.Client
    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout('sensitive-provider-body',request=request)
    # Fixture patched the module-global Client; recover the real constructor.
    from httpx._client import Client
    monkeypatch.setattr(intents.httpx,'Client',lambda **kw:Client(transport=httpx.MockTransport(handle),**kw))
    with pytest.raises(HTTPException) as caught:intents.create_moyasar_sandbox_intent(context)
    assert len(calls)==1 and 'sensitive-provider-body' not in str(caught.value)
