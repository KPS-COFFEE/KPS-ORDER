"""Final host/presentation boundaries, without database or provider I/O."""
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app import activation, multibrand, portals, storefront_scope

ROOT = Path(__file__).resolve().parents[1]


def request(host, path='/', query=b'', extra=()):
    return Request({'type':'http', 'scheme':'https', 'method':'GET', 'path':path,
                    'query_string':query, 'headers':[(b'host',host.encode()),*extra]})


def test_brand_html_never_copies_kps_executable_campaign():
    source = (ROOT/'public/index.html').read_text()
    assert '/assets/kps-gift-creative.js?' in source
    assert storefront_scope.customer_html(source, request('testserver')) == source
    token = storefront_scope.current_brand.set('enbishan')
    try:
        rendered = storefront_scope.customer_html(source, request('order.enbishan.com'))
    finally:
        storefront_scope.current_brand.reset(token)
    assert '/assets/kps-gift-creative.' not in rendered
    assert 'https://order.enbishan.com/assets/enbishan-menu/source-6f7efb0f38241172.jpg' in rendered
    assert 'property="og:url" content="https://order.enbishan.com"' in rendered
    assert 'KPS COFFEE' not in rendered
    assert 'public-contact-support' in rendered


def mapped_runtime(monkeypatch):
    monkeypatch.setattr(multibrand, 'enabled', lambda: True)
    monkeypatch.setattr(multibrand, 'isolation_guard', lambda: None)
    monkeypatch.setenv('PUBLIC_BASE_URL','https://order.kps.sa')
    monkeypatch.delenv('ENBISHAN_ISOLATED_ENABLED',raising=False)
    monkeypatch.setenv('APP_ALLOWED_HOSTS','order.kps.sa,order.enbishan.com')
    monkeypatch.setenv('KPS_STOREFRONT_HOST_BRANDS','{"order.kps.sa":"kps","order.enbishan.com":"enbishan"}')


@pytest.mark.parametrize('host,brand',[('order.kps.sa','kps'),('order.enbishan.com','enbishan')])
def test_exact_host_selects_its_brand_ignoring_forwarded_headers(monkeypatch,host,brand):
    mapped_runtime(monkeypatch)
    assert storefront_scope.bound_brand(request(host,extra=((b'x-forwarded-host',b'foreign.invalid'),))) == brand


def test_unknown_virtual_host_cannot_acquire_default_storefront(monkeypatch):
    mapped_runtime(monkeypatch)
    with pytest.raises(HTTPException) as error:
        storefront_scope.bound_brand(request('foreign.invalid'))
    assert error.value.status_code == 404


@pytest.mark.parametrize('mapping',[
    '{"order.kps.sa":"kps"}',
    '{"order.kps.sa":"enbishan","order.enbishan.com":"evil"}',
    '{"order.kps.sa":"kps","*.enbishan.com":"enbishan"}',
])
def test_bad_runtime_mapping_fails_closed(monkeypatch,mapping):
    mapped_runtime(monkeypatch)
    monkeypatch.setenv('KPS_STOREFRONT_HOST_BRANDS',mapping)
    with pytest.raises(RuntimeError):
        storefront_scope.bound_brand(request('order.kps.sa'))


def test_unapproved_multibrand_activation_cannot_be_enabled_by_flag(monkeypatch):
    monkeypatch.setenv('KPS_MULTIBRAND_ENABLED','true')
    monkeypatch.setenv('RUNTIME_REVISION','a'*40)
    monkeypatch.delenv('KPS_MULTIBRAND_ACCEPTANCE_SHA',raising=False)
    monkeypatch.delenv('KPS_MULTIBRAND_OWNER_APPROVAL_SHA',raising=False)
    with pytest.raises(HTTPException) as error:
        activation.enabled('multibrand')
    assert error.value.detail['code']=='FEATURE_RELEASE_NOT_APPROVED'


@pytest.mark.parametrize('path',['/ops','/ops.html','/ops.js','/api/identity/staff/login','/api/auth/login'])
def test_enbishan_customer_portal_never_exposes_staff_flow(monkeypatch,path):
    monkeypatch.setenv('KPS_PORTAL_HOST_ROUTING_ENABLED','true')
    app=Starlette(routes=[Route('/{path:path}',lambda request:PlainTextResponse('downstream'))])
    app.add_middleware(portals.PortalBoundaryMiddleware)
    response=TestClient(app,base_url='https://order.enbishan.com').get(path)
    assert response.status_code==403
    assert response.json()['detail']['code']=='PORTAL_BOUNDARY_REJECTED'


def test_enbishan_customer_portal_public_page_and_role_boundary(monkeypatch):
    monkeypatch.setenv('KPS_PORTAL_HOST_ROUTING_ENABLED','true')
    app=Starlette(routes=[Route('/',lambda request:PlainTextResponse('customer page'))])
    app.add_middleware(portals.PortalBoundaryMiddleware)
    assert TestClient(app,base_url='https://order.enbishan.com').get('/').text=='customer page'
    with pytest.raises(HTTPException):
        portals.enforce_role(request('order.enbishan.com'),{'role':'owner'})
    portals.enforce_role(request('order.enbishan.com'),{'role':'customer'})


@pytest.mark.parametrize('endpoint',['daily','beneficiaries'])
def test_campaign_data_requires_authenticated_principal(endpoint):
    from app import auth
    from app.main import app
    def denied():
        raise HTTPException(401, {'code':'AUTHENTICATION_REQUIRED'})
    previous=dict(app.dependency_overrides)
    app.dependency_overrides[auth.current_user]=denied
    try:
        response=TestClient(app).get('/api/reports/promotions/'+endpoint,
                                     params={'start':'2026-10-22','end':'2026-10-22'})
        assert response.status_code==401
        assert response.json()['detail']['code']=='AUTHENTICATION_REQUIRED'
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
