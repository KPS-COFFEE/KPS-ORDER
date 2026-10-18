"""HTTP security contract survives the task-free middleware implementation."""
import asyncio
import json

import pytest
from fastapi import Body, Depends, FastAPI, HTTPException

from app import auth, monitoring
from app.http_security import SecurityMiddleware


def scope(path='/api/check', method='GET', headers=()):
    return {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
            'path': path, 'raw_path': path.encode(), 'root_path': '', 'query_string': b'',
            'method': method, 'scheme': 'http', 'server': ('testserver', 80),
            'client': ('127.0.0.1', 1234), 'headers': list(headers)}


def run(app, request_scope, request_messages=None):
    messages = []
    requests = iter(request_messages or [
        {'type': 'http.request', 'body': b'', 'more_body': False}])

    async def receive():
        return next(requests, {'type': 'http.disconnect'})

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


@pytest.mark.parametrize('declared', [None, '1', '8388608'])
@pytest.mark.parametrize('chunked', [False, True])
def test_actual_oversize_rejects_before_fastapi_dependencies_or_endpoint(monkeypatch, declared, chunked):
    records, effects = [], []
    monkeypatch.setattr(monitoring, 'record_response', records.append)
    app = FastAPI()

    async def authorization():
        effects.append('authorization')

    @app.post('/api/check')
    async def write(body: dict = Body(), authorized=Depends(authorization)):
        effects.append(body)
        return {'ok': True}

    # A valid JSON prefix followed only by whitespace would execute the write if
    # middleware truncated the excess and replayed the accepted prefix.
    prefix = b'{"value":1}'
    padding = b' ' * (8 * 1024 * 1024 - len(prefix))
    if chunked:
        requests = [
            {'type': 'http.request', 'body': prefix, 'more_body': True},
            {'type': 'http.request', 'body': padding, 'more_body': True},
            {'type': 'http.request', 'body': b' ', 'more_body': False},
        ]
    else:
        requests = [{'type': 'http.request', 'body': prefix + padding + b' ', 'more_body': False}]
    headers = [(b'origin', b'http://testserver'), (b'content-type', b'application/json')]
    if declared is not None:
        headers.append((b'content-length', declared.encode()))
    elif chunked:
        headers.append((b'transfer-encoding', b'chunked'))
    messages = run(app, scope(method='POST', headers=headers), requests)
    assert messages[0]['status'] == 413
    assert json.loads(messages[1]['body'])['detail']['code'] == 'BODY_TOO_LARGE'
    assert dict(messages[0]['headers'])[b'cache-control'] == b'no-store'
    assert effects == []
    assert records == [413]


@pytest.mark.parametrize('size', [17, 8 * 1024 * 1024])
@pytest.mark.parametrize('chunked', [False, True])
def test_complete_permitted_body_is_preserved_and_disconnect_is_forwarded(size, chunked):
    body = b'a' * size
    requests = ([{'type': 'http.request', 'body': body[:9], 'more_body': True},
                 {'type': 'http.request', 'body': body[9:], 'more_body': False}]
                if chunked else [{'type': 'http.request', 'body': body, 'more_body': False}])
    observed = []

    async def app(request_scope, receive, send):
        observed.append(await receive())
        observed.append(await receive())
        await send({'type': 'http.response.start', 'status': 204, 'headers': []})
        await send({'type': 'http.response.body', 'body': b''})

    messages = run(app, scope(method='POST', headers=[(b'origin', b'http://testserver')]), requests)
    assert messages[0]['status'] == 204
    assert observed == [{'type': 'http.request', 'body': body, 'more_body': False},
                        {'type': 'http.disconnect'}]


def test_body_limit_stops_receiving_after_first_oversized_chunk():
    received = []
    messages = []
    requests = iter([
        {'type': 'http.request', 'body': b'a', 'more_body': True},
        {'type': 'http.request', 'body': b'b' * (8 * 1024 * 1024), 'more_body': True},
        {'type': 'http.request', 'body': b'never consumed', 'more_body': False},
    ])

    async def receive():
        event = next(requests)
        received.append(event['type'])
        return event

    async def send(message):
        messages.append(message)

    async def app(*args):
        pytest.fail('Oversized request reached an application that could write before reading its body')

    asyncio.run(SecurityMiddleware(app)(
        scope(method='POST', headers=[(b'origin', b'http://testserver')]), receive, send))
    assert received == ['http.request', 'http.request']
    assert messages[0]['status'] == 413
    assert json.loads(messages[1]['body'])['detail']['code'] == 'BODY_TOO_LARGE'


def test_disconnect_before_complete_request_does_not_execute_application():
    async def app(*args):
        pytest.fail('Incomplete request reached the application')

    messages = run(app, scope(method='POST', headers=[(b'origin', b'http://testserver')]), [
        {'type': 'http.request', 'body': b'{"value":', 'more_body': True},
        {'type': 'http.disconnect'},
    ])
    assert messages == []


@pytest.mark.postgres
@pytest.mark.parametrize('declared', [None, '1'])
def test_oversized_guest_request_has_no_database_side_effects(client, declared):
    from app import db

    def counts():
        with db.tx() as conn:
            return {table: conn.execute('SELECT count(*) AS n FROM ' + table).fetchone()['n']
                    for table in ('users', 'sessions', 'audit_events', 'rate_limits')}

    before = counts()
    prefix = b'{"brand_id":"kps","display_name":"SECURITY TEST"}'
    headers = {'Content-Type': 'application/json'}
    if declared is not None:
        headers['Content-Length'] = declared
    response = client.post('/api/identity/guest', headers=headers,
        content=iter([prefix, b' ' * (8 * 1024 * 1024 + 1 - len(prefix))]))
    assert response.status_code == 413
    assert response.json()['detail']['code'] == 'BODY_TOO_LARGE'
    assert counts() == before


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
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    with pytest.raises(RuntimeError, match='stream interrupted'):
        asyncio.run(SecurityMiddleware(app)(scope(), receive, send))
    assert len(messages) == 1 and records == [200]


def test_non_http_is_passed_through_unchanged(monkeypatch):
    observed = []
    request_scope, receive, send = {'type': 'lifespan'}, object(), object()
    async def app(*args):
        observed.append(args)
    asyncio.run(SecurityMiddleware(app)(request_scope, receive, send))
    assert observed == [(request_scope, receive, send)]


def test_checkout_router_precedence_is_disjoint_and_preserves_dependency_objects():
    from app import branch_payments, catalog, orders
    from app.main import app
    from fastapi.routing import APIRoute

    # The real composition inserts branch payments between orders and catalog.
    # Compare router identities, not a stale count that misses inserted routers.
    routers = (orders.router, branch_payments.router, catalog.router, auth.router)
    namespaces = [{r.path.split('/')[2] for r in router.routes} for router in routers]
    assert all(not a & b for i, a in enumerate(namespaces) for b in namespaces[i + 1:])
    included = [r.original_router for r in app.routes if hasattr(r, 'original_router')]
    assert included[:4] == list(routers)
    assert all(included.count(router) == 1 for router in routers)

    def calls(dependant):
        return {dependant.call} | {call for dependency in dependant.dependencies
                                  for call in calls(dependency)}

    # Inclusion must retain actual endpoint and dependency objects. This guards
    # against wrappers that accidentally lose authentication or role checks.
    protected = (
        (orders.router, '/api/orders', 'GET', auth.current_user),
        (orders.router, '/api/orders', 'POST', auth.current_user),
        (branch_payments.router, '/api/branch-payments/orders/{oid}/attempt', 'POST', auth.current_user),
        (branch_payments.router, '/api/branch-payments/attempts/{aid}/reconcile', 'POST', auth.current_user),
        (auth.router, '/api/auth/logout', 'POST', auth.current_user),
        (auth.router, '/api/identity/staff/invitations', 'POST', auth.current_user),
    )
    for router, path, method, required in protected:
        source = next(route for route in router.routes
                      if isinstance(route, APIRoute) and route.path == path and method in route.methods)
        composed = next(item.original_router for item in app.routes
                        if getattr(item, 'original_router', None) is router)
        target = next(route for route in composed.routes
                      if isinstance(route, APIRoute) and route.path == path and method in route.methods)
        assert target is source
        assert target.endpoint is source.endpoint
        assert target.dependant is source.dependant
        assert required in calls(target.dependant)
        if path.endswith('/invitations') or (router in (orders.router, branch_payments.router) and method == 'POST'):
            assert target.dependant.dependencies[0].call is not auth.current_user
            assert auth.current_user in calls(target.dependant.dependencies[0])

    role_contracts = (
        (orders.router, '/api/orders', {'customer'}),
        (branch_payments.router, '/api/branch-payments/orders/{oid}/attempt', {'customer'}),
        (branch_payments.router, '/api/branch-payments/attempts/{aid}/reconcile', {'customer','owner','branch_manager'}),
        (auth.router, '/api/identity/staff/invitations', {'owner'}),
    )
    for router, path, permitted in role_contracts:
        route = next(route for route in router.routes if route.path == path and 'POST' in route.methods)
        guard = route.dependant.dependencies[0].call
        for role in ('customer','owner','driver','delivery_manager','branch_manager'):
            principal = {'role':role}
            if role in permitted:
                assert asyncio.run(guard(user=principal)) is principal
            else:
                with pytest.raises(HTTPException) as denied:
                    asyncio.run(guard(user=principal))
                assert denied.value.status_code == 403
                assert denied.value.detail['code'] == 'ROLE_FORBIDDEN'
