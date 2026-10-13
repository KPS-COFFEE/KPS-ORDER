"""HTTP security contract survives the task-free middleware implementation."""
import asyncio
import json

import pytest
from fastapi import HTTPException

from app import auth, monitoring
from app.http_security import SecurityMiddleware


def scope(path='/api/check', method='GET', headers=()):
    return {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
            'path': path, 'raw_path': path.encode(), 'root_path': '', 'query_string': b'',
            'method': method, 'scheme': 'http', 'server': ('testserver', 80),
            'client': ('127.0.0.1', 1234), 'headers': list(headers)}


def run(app, request_scope):
    messages = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        messages.append(message)

    asyncio.run(SecurityMiddleware(app)(request_scope, receive, send))
    return messages


@pytest.mark.parametrize('path,method,status,cache', [
    ('/api/check', 'GET', 200, 'no-store'),
    ('/api/check', 'POST', 409, 'no-store'),
    ('/ops', 'GET', 200, 'no-store'),
    ('/api/product-assets/a', 'GET', 200, 'public, max-age=60'),
    ('/api/banner-assets/a', 'HEAD', 200, 'public, max-age=60'),
    ('/api/product-assets/a', 'GET', 404, 'no-store'),
    ('/api/product-assets/a', 'POST', 200, 'no-store'),
    ('/assets/a', 'GET', 304, 'public, max-age=86400'),
    ('/assets/a', 'GET', 404, 'public, max-age=60'),
])
def test_headers_cookies_streaming_and_monitoring(monkeypatch, path, method, status, cache):
    records = []
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setattr(monitoring, 'record_response', records.append)
    chunks = [{'type': 'http.response.body', 'body': b'one', 'more_body': True},
              {'type': 'http.response.body', 'body': b'two', 'more_body': False}]

    async def app(request_scope, receive, send):
        await send({'type': 'http.response.start', 'status': status,
                    'headers': [(b'cache-control', b'public, max-age=60'),
                                (b'set-cookie', b'one=1'), (b'set-cookie', b'two=2')]})
        for chunk in chunks:
            await send(chunk)

    messages = run(app, scope(path, method, [(b'origin', b'http://testserver')]))
    header_pairs = messages[0]['headers']
    headers = dict(header_pairs)
    assert len(headers[b'x-request-id']) == 32
    assert headers[b'x-content-type-options'] == b'nosniff'
    assert headers[b'x-frame-options'] == b'DENY'
    assert headers[b'referrer-policy'] == b'same-origin'
    assert headers[b'permissions-policy'] == b'camera=(), microphone=(), geolocation=(self)'
    assert b"form-action 'self'" in headers[b'content-security-policy']
    assert headers[b'strict-transport-security'] == b'max-age=31536000; includeSubDomains'
    assert headers[b'cache-control'].decode() == cache
    assert [v for k, v in header_pairs if k == b'set-cookie'] == [b'one=1', b'two=2']
    assert messages[1:] == chunks and all(a is b for a, b in zip(messages[1:], chunks))
    assert records == [status]


@pytest.mark.parametrize('headers,status,code', [
    ([], 403, 'ORIGIN_REJECTED'),
    ([(b'origin', b'https://untrusted.invalid')], 403, 'ORIGIN_REJECTED'),
    ([(b'origin', b'http://testserver'), (b'content-length', b'-1')], 413, 'BODY_TOO_LARGE'),
    ([(b'origin', b'http://testserver'), (b'content-length', b'8388609')], 413, 'BODY_TOO_LARGE'),
])
def test_rejections_do_not_execute_application_or_read_body(monkeypatch, headers, status, code):
    records = []
    monkeypatch.setattr(monitoring, 'record_response', records.append)

    async def app(*args):
        pytest.fail('Rejected request reached the application')

    async def no_body():
        pytest.fail('Rejected request consumed the body')

    messages = []
    async def send(message):
        messages.append(message)
    asyncio.run(SecurityMiddleware(app)(scope(method='POST', headers=headers), no_body, send))
    assert messages[0]['status'] == status
    assert dict(messages[0]['headers'])[b'cache-control'] == b'no-store'
    assert json.loads(messages[1]['body'])['detail']['code'] == code
    assert records == [status]


@pytest.mark.parametrize('error,status,code', [
    (RuntimeError('secret-password-value'), 503, 'SERVICE_UNAVAILABLE'),
    (HTTPException(403, {'code': 'ROLE_FORBIDDEN'}), 403, 'ROLE_FORBIDDEN'),
])
def test_error_contract_is_sanitized(monkeypatch, caplog, error, status, code):
    records = []
    monkeypatch.setattr(monitoring, 'record_response', records.append)
    async def app(*args):
        raise error
    messages = run(app, scope())
    assert messages[0]['status'] == status
    assert json.loads(messages[1]['body'])['detail']['code'] == code
    assert 'secret-password-value' not in repr(messages) + caplog.text
    assert records == [status]


def test_started_response_failure_never_sends_second_headers(monkeypatch):
    messages, records = [], []
    monkeypatch.setattr(monitoring, 'record_response', records.append)
    async def app(request_scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        raise RuntimeError('stream interrupted')
    async def send(message):
        messages.append(message)
    with pytest.raises(RuntimeError, match='stream interrupted'):
        asyncio.run(SecurityMiddleware(app)(scope(), None, send))
    assert len(messages) == 1 and records == [200]


def test_non_http_is_passed_through_unchanged(monkeypatch):
    observed = []
    request_scope, receive, send = {'type': 'lifespan'}, object(), object()
    async def app(*args):
        observed.append(args)
    asyncio.run(SecurityMiddleware(app)(request_scope, receive, send))
    assert observed == [(request_scope, receive, send)]


def test_checkout_router_precedence_is_disjoint_and_preserves_dependency_objects():
    from app import catalog, orders
    from app.main import app
    routers = (orders.router, catalog.router, auth.router)
    namespaces = [{r.path.split('/')[2] for r in router.routes} for router in routers]
    assert all(not a & b for i, a in enumerate(namespaces) for b in namespaces[i + 1:])
    included = [r.original_router for r in app.routes if hasattr(r, 'original_router')]
    assert included[:3] == list(routers)
