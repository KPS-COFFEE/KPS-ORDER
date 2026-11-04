"""DB failures remain generic publicly and useful without private data in logs."""
import json
import logging

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import PoolTimeout

from app import db
from app.http_security import SecurityMiddleware
from app.main import database_unavailable


def failure_events(caplog):
    return [json.loads(r.getMessage().split(' ', 1)[1]) for r in caplog.records
            if r.getMessage().startswith('database_request_failed ')]


@pytest.mark.parametrize('error,expected_sqlstate,cause', [
    (psycopg.errors.DeadlockDetected('secret SQL customer 0551234567'), '40P01', None),
    (psycopg.errors.QueryCanceled('secret SQL timeout'), '57014', None),
    (psycopg.OperationalError('postgres://secret@private-host'), None, PoolTimeout('secret pool')),
])
@pytest.mark.parametrize('method', ['GET', 'POST'])
def test_routed_failures_log_safe_categories_and_trace_without_replaying(monkeypatch, caplog, error, expected_sqlstate, cause, method):
    app = FastAPI()
    app.add_middleware(SecurityMiddleware)
    app.add_exception_handler(psycopg.OperationalError, database_unavailable)
    attempts = []

    @app.api_route('/api/probe/{customer_id}', methods=['GET', 'POST'])
    async def fail(customer_id: str):
        attempts.append(customer_id)
        raise error from cause

    monkeypatch.setattr(db, 'pool_statistics', lambda: {
        'postgres://pool-secret': {'pool_size': 4, 'requests_waiting': 2,
                                   'requests_wait_ms': 12, 'unknown': 'sensitive',
                                   'connections_num': True},
        'other-private-name': {'pool_size': 4, 'requests_waiting': 1},
    })
    caplog.set_level(logging.ERROR, logger='kps_order')
    response = TestClient(app).request(method, '/api/probe/private-customer?phone=0551234567',
                                      headers={'origin': 'http://testserver',
                                               'x-request-id': 'forged-secret'})
    assert response.status_code == 503
    assert response.json() == {'detail': {'code': 'DATABASE_UNAVAILABLE', 'message': 'قاعدة البيانات غير متاحة مؤقتًا.'}}
    assert attempts == ['private-customer']
    events = failure_events(caplog)
    assert len(events) == 1
    event = events[0]
    assert event['trace'] == response.headers['x-request-id']
    assert event['route'] == '/api/probe/{customer_id}'
    assert event['method'] == method
    assert event['error_type'] == type(error).__name__
    assert event['sqlstate'] == expected_sqlstate
    assert event['cause_type'] == ('PoolTimeout' if cause else None)
    assert event['elapsed_ms'] >= 0
    assert event['pool_counters'] == {'pool_size': 8, 'requests_waiting': 3, 'requests_wait_ms': 12}
    assert not any(value in caplog.text for value in (
        'private-customer', '0551234567', 'secret', 'private-host', 'other-private-name',
        'phone=', 'forged-secret',
    ))


def test_middleware_database_failure_logs_once_without_route_or_pool_leak(monkeypatch, caplog):
    # Represents a failure before routing, e.g. storefront authorization.
    async def failing_app(scope, receive, send):
        raise psycopg.errors.DeadlockDetected('secret query')

    def broken_pool_stats():
        raise RuntimeError('secret pool details')

    monkeypatch.setattr(db, 'pool_statistics', broken_pool_stats)
    caplog.set_level(logging.ERROR, logger='kps_order')
    response = TestClient(SecurityMiddleware(failing_app)).get('/api/private-id?token=secret')
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'SERVICE_UNAVAILABLE'
    event, = failure_events(caplog)
    assert event['trace'] == response.headers['x-request-id']
    assert event['route'] == '<unresolved>'
    assert event['pool_counters'] is None
    assert event['sqlstate'] == '40P01'
    assert 'secret' not in caplog.text
    assert 'private-id' not in caplog.text
