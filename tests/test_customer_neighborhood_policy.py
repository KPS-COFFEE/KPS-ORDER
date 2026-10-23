"""Customer checkout policy never relies on a client-selected context header."""
from contextlib import nullcontext
from pathlib import Path
import subprocess
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from app import auth, catalog, db, first_order_gift, orders
from test_khaliya_routing_source import CatalogConnection, Rows, fixtures, quote_body


def body(service='delivery',neighborhood='',address=''):
    payload=quote_body(service=service,items=[{'product_id':'kps-01','quantity':1}]).model_dump()
    payload['customer'].update(neighborhood=neighborhood,address=address)
    return catalog.QuoteBody.model_validate(payload)


def request(context=None):
    headers=[] if context is None else [(b'x-kps-context',context.encode())]
    return Request({'type':'http','method':'POST','path':'/api/orders','headers':headers})


@pytest.fixture
def checkout_source(monkeypatch):
    branches,products=fixtures()
    class Connection(CatalogConnection):
        def execute(self,sql,args=None):
            if 'FROM app_settings' in sql:return Rows({'settings':{}})
            return super().execute(sql,args)
    conn=Connection(branches,products)
    monkeypatch.setattr(db,'tx',lambda **kwargs:nullcontext(conn))
    monkeypatch.setattr(db,'rate_limit',lambda *args:True)
    monkeypatch.setattr(auth,'assert_customer_order_eligible',lambda *args,**kwargs:None)
    monkeypatch.setattr(db,'idempotent',lambda conn,uid,key,operation,intent,perform,**kwargs:perform())
    return {'id':'synthetic-customer','brand_id':'kps','role':'customer','customer_phone_required':False}


@pytest.mark.parametrize('value',['',' ','\t\n'])
def test_delivery_guard_rejects_blank_neighborhood(value):
    with pytest.raises(HTTPException) as error:catalog.require_customer_delivery_neighborhood(body(neighborhood=value))
    assert error.value.status_code==422 and error.value.detail['code']=='DELIVERY_NEIGHBORHOOD_REQUIRED'


@pytest.mark.parametrize('service',['delivery','pickup','car'])
def test_description_optional_and_only_customer_delivery_needs_neighborhood(service):
    parsed=body(service,neighborhood='حي اختبار' if service=='delivery' else '',address='')
    assert catalog.require_customer_delivery_neighborhood(parsed) is None
    assert parsed.customer.address==''


@pytest.mark.parametrize('context',[None,'work','customer'])
def test_create_guard_cannot_be_bypassed_by_omitting_or_spoofing_header(checkout_source,context):
    with pytest.raises(HTTPException) as error:
        orders._create_order(body(),request(context),checkout_source,'synthetic-idempotency')
    assert error.value.status_code==422 and error.value.detail['code']=='DELIVERY_NEIGHBORHOOD_REQUIRED'


def test_customer_quote_header_only_selects_feedback_and_staff_shared_quote_stays_compatible(checkout_source):
    assert catalog.quote(body(),request('work')).status_code==200
    assert catalog.quote(body(),request()).status_code==200
    with pytest.raises(HTTPException) as error:catalog.quote(body(),request('customer'))
    assert error.value.detail['code']=='DELIVERY_NEIGHBORHOOD_REQUIRED'
    assert catalog.quote(body(neighborhood='حي اختبار'),request('customer')).status_code==200


def test_gift_quote_independently_enforces_customer_requirement(checkout_source):
    with pytest.raises(HTTPException) as error:first_order_gift.gift_quote(body(),user=checkout_source)
    assert error.value.detail['code']=='DELIVERY_NEIGHBORHOOD_REQUIRED'


def test_durable_existing_replay_skips_new_creation_policy(checkout_source,monkeypatch):
    saved='{"order":{"id":"old-committed-order"}}'
    monkeypatch.setattr(db,'idempotent',lambda *args,**kwargs:saved)
    monkeypatch.setattr(catalog,'require_customer_delivery_neighborhood',lambda *args:pytest.fail('A committed replay must not rerun creation policy'))
    result=orders._create_order(body(),None,checkout_source,'existing-key')
    assert result==saved


def test_location_and_coverage_checks_still_precede_neighborhood(checkout_source):
    outside=body().model_copy(deep=True);outside.customer.location.lat=31
    with pytest.raises(HTTPException) as error:orders._create_order(outside,None,checkout_source,'synthetic-outside')
    assert error.value.detail['code']=='OUTSIDE_COVERAGE'


def test_actual_customer_quote_guard_and_tab_draft_acceptance():
    subprocess.run(['node','tests/test_customer_neighborhood_policy.cjs'],cwd=Path(__file__).resolve().parents[1],check=True)
    subprocess.run(['node','tests/test_customer_location_link.cjs'],cwd=Path(__file__).resolve().parents[1],check=True)
