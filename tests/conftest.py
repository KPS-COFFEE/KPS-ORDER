"""Isolated PostgreSQL fixtures; never fall back to a runtime database.

Each database test owns a UUID schema inside an explicitly named local test
database. Only that schema can be deleted by test cleanup. Production migrations
and runtime never use this reset logic. Source-only tests need no database.
"""
from __future__ import annotations

import os
import re
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

_HOSTS = {'127.0.0.1', 'localhost', '::1', 'postgres'}
_PROVIDER_SECRETS = (
    'TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_VERIFY_SERVICE_SID',
    'RENDER_API_KEY', 'RENDER_WEBHOOK_SIGNING_SECRET', 'RENDER_POSTGRES_ID', 'RENDER_BACKUP_BRAND_ID',
    'RECOVERY_POLL_ENABLED', 'RECOVERY_POLL_INTERVAL_SECONDS',
    'EMAIL_FROM', 'MAIL_OUTBOX_KEY', 'RESEND_API_KEY', 'RESEND_WEBHOOK_SECRET',
    'SMTP_HOST', 'SMTP_PORT', 'SMTP_USERNAME', 'SMTP_PASSWORD',
)
_TEST_DEFAULTS = {
    'APP_ENV': 'testing',
    'PUBLIC_BASE_URL': 'http://testserver',
    'APP_ALLOWED_HOSTS': 'testserver,localhost,127.0.0.1',
    'SESSION_COOKIE_SECURE': 'false',
    'SESSION_TTL_HOURS': '12',
    'OTP_PROVIDER': 'disabled',
    'EMAIL_PROVIDER': 'disabled',
    'REPORT_WORKER_ENABLED': 'false',
    'EMAIL_WORKER_ENABLED': 'false',
    'INITIAL_CATALOG_IMPORT': 'true',
}


def pytest_configure(config):
    # Set import-time middleware values before test modules import app.main.
    # No inherited runtime DSN or messaging credentials may be used by tests.
    os.environ.pop('DATABASE_URL', None)
    for key in _PROVIDER_SECRETS:
        os.environ.pop(key, None)
    os.environ.update(_TEST_DEFAULTS)
    config.addinivalue_line('markers', 'postgres: requires an explicit isolated TEST_DATABASE_URL')


@pytest.fixture(autouse=True)
def safe_test_environment(monkeypatch):
    for key, value in _TEST_DEFAULTS.items():
        monkeypatch.setenv(key, value)
    for key in _PROVIDER_SECRETS:
        monkeypatch.delenv(key, raising=False)
    # A test that needs the database must request database/client. Otherwise an
    # accidental app call fails closed instead of reaching a runtime resource.
    monkeypatch.delenv('DATABASE_URL', raising=False)


def _isolated_test_url() -> str:
    url = os.environ.get('TEST_DATABASE_URL', '')
    if not url:
        raise pytest.UsageError('TEST_DATABASE_URL is required for database tests; no production fallback is permitted.')
    if not url.startswith(('postgresql://', 'postgres://')):
        raise pytest.UsageError('TEST_DATABASE_URL must use a PostgreSQL URL, not an inherited runtime DSN.')
    try:
        parts = conninfo_to_dict(url)
    except Exception:
        raise pytest.UsageError('TEST_DATABASE_URL must be a valid PostgreSQL connection URL.') from None
    name = parts.get('dbname', '')
    host = parts.get('host', '')
    if host not in _HOSTS or not (name.startswith('test_') or name.endswith('_test')):
        raise pytest.UsageError('Database tests require a local PostgreSQL host and a database named test_* or *_test.')
    if parts.get('options'):
        raise pytest.UsageError('TEST_DATABASE_URL must not override session options; fixtures own the isolated search_path.')
    return url


@pytest.fixture
def database(monkeypatch, safe_test_environment):
    base_url = _isolated_test_url()
    schema = 'kps_test_' + uuid.uuid4().hex
    assert re.fullmatch(r'kps_test_[0-9a-f]{32}', schema)
    with psycopg.connect(base_url, autocommit=True, connect_timeout=10) as control:
        actual_name = control.execute('SELECT current_database()').fetchone()[0]
        if not (actual_name.startswith('test_') or actual_name.endswith('_test')):
            raise pytest.UsageError('The connected database is not an explicitly named test database.')
        control.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    parsed = urlsplit(base_url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(('options', f'-csearch_path={schema}'))
    isolated_url = urlunsplit(parsed._replace(query=urlencode(query)))
    monkeypatch.setenv('DATABASE_URL', isolated_url)
    from app import db
    try:
        db.migrate()
        yield isolated_url
    finally:
        db.close_pools(isolated_url)
        # Cleanup is bounded to the UUID schema created above. Never drop a
        # database, public schema or anything supplied by an external test URL.
        with psycopg.connect(base_url, autocommit=True, connect_timeout=10) as control:
            control.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


@pytest.fixture
def db_schema(database):
    """Alias for tests that explicitly request the isolated schema lifecycle."""
    return database


@pytest.fixture
def client(database):
    from app.main import app
    # Tests exercise real lifespan migrations and origin protection. They may
    # remove this header explicitly when testing missing/rejected origins.
    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    try:
        with TestClient(app, base_url='http://testserver', headers={'Origin': 'http://testserver'}) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
