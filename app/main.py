"""Small independent web service. Authoritative data resides exclusively in PostgreSQL."""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import auth, catalog, db, email_delivery, orders

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'public'
LOGGER = logging.getLogger('kps_order')


@asynccontextmanager
async def lifespan(app):
    background_tasks = []
    app.state.database_configured = bool(os.getenv('DATABASE_URL'))
    if app.state.database_configured:
        # A bad configured migration/database blocks startup; no storage substitute is created.
        db.migrate()
        from . import recovery
        if recovery.configuration()['poll_configured']:
            background_tasks.append(asyncio.create_task(recovery.poll_loop(), name='backup-status-monitor'))
        from . import email_delivery, report_notifications
        if email_delivery.configuration()['configured'] and os.getenv('EMAIL_WORKER_ENABLED', 'true').lower() == 'true':
            background_tasks.append(asyncio.create_task(email_delivery.poll_loop(), name='identity-email-outbox'))
        if os.getenv('REPORT_WORKER_ENABLED', 'true').lower() == 'true':
            background_tasks.append(asyncio.create_task(report_notifications.poll_loop(), name='closure-report-worker'))
    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title='KPS Independent Orders', version='5.0.0', lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
hosts = [item.strip() for item in os.getenv('APP_ALLOWED_HOSTS', '').split(',') if item.strip()]
if hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)


@app.middleware('http')
async def security(request: Request, call_next):
    trace = uuid.uuid4().hex
    try:
        if request.url.path.startswith('/api/') and request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            auth.check_origin(request)
        length = request.headers.get('content-length')
        if length and (not length.isdecimal() or int(length) > 8 * 1024 * 1024):
            response = JSONResponse({'detail': {'code': 'BODY_TOO_LARGE', 'message': 'الملف أو الطلب أكبر من الحد المسموح.'}}, status_code=413)
        else:
            response = await call_next(request)
    except Exception as exc:
        from fastapi import HTTPException
        if isinstance(exc, HTTPException):
            response = JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
        else:
            # Never serialize DB error details, submitted fields or provider credentials to a client.
            LOGGER.error('request_failed trace=%s method=%s path=%s error_type=%s',
                         trace, request.method, request.url.path, type(exc).__name__)
            response = JSONResponse({'detail': {'code': 'SERVICE_UNAVAILABLE', 'message': 'تعذر تنفيذ العملية الآن. حاول لاحقًا.'}}, status_code=503)
    response.headers['X-Request-ID'] = trace
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=(self)'
    response.headers['Content-Security-Policy'] = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                                   "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
                                                   "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
    if os.getenv('APP_ENV', 'production') == 'production':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    if request.url.path.startswith('/api/') or request.url.path.startswith('/ops'):
        response.headers['Cache-Control'] = 'no-store'
    return response


@app.exception_handler(RequestValidationError)
async def invalid_input(request: Request, exc: RequestValidationError):
    fields = ['.'.join(map(str, error['loc'][1:])) for error in exc.errors()]
    return JSONResponse({'detail': {'code': 'VALIDATION_ERROR', 'message': 'راجع الحقول المطلوبة وصحتها.', 'fields': fields}}, status_code=422)


@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(request: Request, exc):
    return JSONResponse({'detail': {'code': 'DATABASE_UNAVAILABLE', 'message': 'قاعدة البيانات غير متاحة مؤقتًا.'}}, status_code=503)


app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(orders.router)
# Provider callbacks use their own signed timestamped verification, rather than
# browser Origin or session cookies. They must stay outside the /api namespace.
app.include_router(email_delivery.webhook_router)
# Components are separate files within this one service and share canonical tables.
for name in ('operations', 'chat', 'reporting', 'admin', 'exports', 'recovery', 'performance', 'report_notifications'):
    module = importlib.import_module('.' + name, __package__)
    app.include_router(module.router)


def revision():
    return os.getenv('RUNTIME_REVISION') or os.getenv('RENDER_GIT_COMMIT') or 'local-unversioned'


@app.get('/health')
def health():
    return {'status': 'alive', 'revision': revision(), 'application': 'kps-order-independent-v5'}


@app.get('/ready')
def ready():
    try:
        with db.tx() as conn:
            conn.execute('SELECT 1')
            applied = conn.execute('SELECT count(*) AS n FROM schema_migrations').fetchone()['n']
            products = sum(catalog.source_approved(product) for product in conn.execute('SELECT * FROM products').fetchall()
                           if product['settings'].get('purchasable') is not False)
            branches = conn.execute("SELECT count(*) AS n FROM branches WHERE settings->>'operational_data_verified'='true' OR settings->>'ordering_verified'='true'").fetchone()['n']
            identity = auth.channel_readiness(conn, 'kps')
            preservation = conn.execute("SELECT details FROM audit_events WHERE action='release.operational_data_preserved' AND entity_id=%s ORDER BY created_at DESC LIMIT 1", (revision(),)).fetchone()
        try:
            auth.provider_config()
            otp = True
        except Exception:
            otp = False
        return {'status': 'ready', 'revision': revision(), 'database': 'connected', 'applied_migrations': applied,
                'customer_otp_configured': otp, 'verified_products': products, 'verified_branches': branches,
                'identity_channels': identity,
                'data_preservation': {'verified': bool(preservation and preservation['details'].get('verified')),
                                      'checked_tables': preservation['details'].get('tables_checked', 0) if preservation else 0,
                                      'revision': revision()},
                'ordering_configured': bool(any(channel['available'] for channel in identity.values()) and products and branches)}
    except Exception:
        return JSONResponse({'status': 'not_ready', 'revision': revision(), 'database': 'unavailable'}, status_code=503)


@app.get('/')
def customer_page():
    path = PUBLIC / 'index.html'
    if not path.is_file():
        return JSONResponse({'detail': {'code': 'UI_UNAVAILABLE', 'message': 'واجهة المتجر قيد الإعداد.'}}, status_code=503)
    return FileResponse(path, media_type='text/html', headers={'Cache-Control': 'no-cache'})


@app.get('/ops')
@app.get('/ops.html')
def operations_page():
    path = PUBLIC / 'ops.html'
    if not path.is_file():
        return JSONResponse({'detail': {'code': 'UI_UNAVAILABLE', 'message': 'واجهة التشغيل قيد الإعداد.'}}, status_code=503)
    return FileResponse(path, media_type='text/html', headers={'Cache-Control': 'no-cache'})


@app.get('/docs/metrics')
def metric_definitions(user=Depends(auth.current_user)):
    if user['role'] not in {'owner', 'delivery_manager', 'delivery_staff', 'branch_manager', 'driver'}:
        raise db.error(403, 'STAFF_REQUIRED', 'تعريفات تقارير التشغيل متاحة للموظفين المخولين.')
    return FileResponse(ROOT / 'docs' / 'metrics.md', media_type='text/plain; charset=utf-8',
                        headers={'Cache-Control': 'no-store'})


@app.get('/{filename}')
def frontend_file(filename: str):
    # Never mount the repository, data files, migrations, environment or the legacy executable menu.
    if filename not in {'customer.js', 'style.css', 'customer.css', 'ops.js', 'ops.css', 'report.js'}:
        raise db.error(404, 'FILE_NOT_FOUND', 'الملف غير موجود.')
    path = PUBLIC / filename
    if not path.is_file() or path.is_symlink():
        raise db.error(404, 'FILE_NOT_FOUND', 'الملف غير موجود.')
    return FileResponse(path)


if (PUBLIC / 'assets').exists():
    app.mount('/assets', StaticFiles(directory=PUBLIC / 'assets', follow_symlink=False), name='assets')
