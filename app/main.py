"""Small independent web service. Authoritative data resides exclusively in PostgreSQL."""
from __future__ import annotations

import asyncio
import importlib
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import psycopg
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import auth, catalog, db, email_delivery, monitoring, orders, image_previews
from .http_security import SecurityMiddleware
from .host_policy import OfficialHostMiddleware

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'public'


@asynccontextmanager
async def lifespan(app):
    background_tasks = []
    try:
        app.state.database_configured = bool(os.getenv('DATABASE_URL'))
        if app.state.database_configured:
            # A bad configured migration/database blocks startup; no storage substitute is created.
            db.migrate()
            from . import recovery
            if recovery.configuration()['poll_configured']:
                background_tasks.append(asyncio.create_task(recovery.poll_loop(), name='backup-status-monitor'))
            if monitoring.configuration()['runtime_configured']:
                background_tasks.append(asyncio.create_task(monitoring.poll_loop(), name='operational-monitor'))
            from . import email_delivery, report_notifications, order_support
            if os.getenv('APP_ENV')!='testing':
                background_tasks.append(asyncio.create_task(order_support.poll_loop(), name='delivery-photo-cleanup'))
            if email_delivery.configuration()['configured'] and os.getenv('EMAIL_WORKER_ENABLED', 'true').lower() == 'true':
                background_tasks.append(asyncio.create_task(email_delivery.poll_loop(), name='identity-email-outbox'))
            if os.getenv('REPORT_WORKER_ENABLED', 'true').lower() == 'true':
                background_tasks.append(asyncio.create_task(report_notifications.poll_loop(), name='closure-report-worker'))
        yield
    finally:
        try:
            for task in background_tasks:
                task.cancel()
            for task in background_tasks:
                with suppress(asyncio.CancelledError):
                    await task
        finally:
            db.close_pools()


app = FastAPI(title='KPS Independent Orders', version='5.0.0', lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
hosts = [item.strip() for item in os.getenv('APP_ALLOWED_HOSTS', '').split(',') if item.strip()]
if hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)


from . import portals
app.add_middleware(portals.PortalBoundaryMiddleware)
app.add_middleware(SecurityMiddleware)
app.add_middleware(OfficialHostMiddleware)


@app.exception_handler(RequestValidationError)
async def invalid_input(request: Request, exc: RequestValidationError):
    fields = ['.'.join(map(str, error['loc'][1:])) for error in exc.errors()]
    return JSONResponse({'detail': {'code': 'VALIDATION_ERROR', 'message': 'راجع الحقول المطلوبة وصحتها.', 'fields': fields}}, status_code=422)


@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(request: Request, exc):
    return JSONResponse({'detail': {'code': 'DATABASE_UNAVAILABLE', 'message': 'قاعدة البيانات غير متاحة مؤقتًا.'}}, status_code=503)


# These routers have disjoint namespaces. Resolve the frequently used order
# routes first without changing a route's dependencies or relative precedence
# within its router. No permission/session result is cached.
app.include_router(orders.router)
from . import experience_policy
app.include_router(experience_policy.router)
from . import first_order_gift
app.include_router(first_order_gift.router)
from . import morning_delivery
app.include_router(morning_delivery.router)
from . import branch_payments
app.include_router(branch_payments.router)
app.include_router(catalog.router)
app.include_router(auth.router)
from . import owner_phone
app.include_router(owner_phone.router)
from . import employee_sms, sms_rollout, sms_pilot_ui
app.include_router(employee_sms.router)
app.include_router(sms_rollout.router)
app.include_router(sms_pilot_ui.router)
# Provider callbacks use their own signed timestamped verification, rather than
# browser Origin or session cookies. They must stay outside the /api namespace.
app.include_router(email_delivery.webhook_router)
# Components are separate files within this one service and share canonical tables.
for name in ('operations', 'smart_dispatch', 'driver_breaks', 'fuel_exceptions', 'driver_evidence', 'driver_float', 'chat', 'reporting', 'admin', 'exports', 'recovery', 'monitoring', 'performance', 'report_notifications', 'maintenance', 'branch_printing', 'invoice_profiles', 'fiscal', 'fiscal_issuance', 'order_support', 'accounting', 'fleet', 'driver_incentives', 'customer_profile', 'customer_legal', 'online_payments', 'order_amendments', 'order_requests', 'channel_settlement', 'internal_documents', 'driver_closure'):
    module = importlib.import_module('.' + name, __package__)
    app.include_router(module.router)

from . import driver_checks
app.include_router(driver_checks.router)
from . import delegated_operations, driver_work_hours, driver_hours_reports
app.include_router(delegated_operations.router)
app.include_router(driver_work_hours.router)
app.include_router(driver_hours_reports.router)


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
            phone_required=auth.customer_phone_required(conn,'kps')
            preservation = conn.execute("SELECT details FROM audit_events WHERE action='release.operational_data_preserved' AND entity_id=%s ORDER BY created_at DESC LIMIT 1", (revision(),)).fetchone()
        otp = any(identity[c]['configured'] for c in ('sms','whatsapp'))
        return {'status': 'ready', 'revision': revision(), 'database': 'connected', 'applied_migrations': applied,
                'customer_otp_configured': otp, 'verified_products': products, 'verified_branches': branches,
                'identity_channels': identity,
                'data_preservation': {'verified': bool(preservation and preservation['details'].get('verified')),
                                      'checked_tables': preservation['details'].get('tables_checked', 0) if preservation else 0,
                                      'revision': revision()},
                'ordering_configured': bool((identity['sms']['available'] if phone_required else any(channel['available'] for channel in identity.values())) and products and branches)}
    except Exception:
        return JSONResponse({'status': 'not_ready', 'revision': revision(), 'database': 'unavailable'}, status_code=503)


@app.get('/')
def customer_page(request: Request):
    path = PUBLIC / 'index.html'
    if not path.is_file():
        return JSONResponse({'detail': {'code': 'UI_UNAVAILABLE', 'message': 'واجهة المتجر قيد الإعداد.'}}, status_code=503)
    from .storefront_scope import customer_html
    return HTMLResponse(customer_html(path.read_text(), request), headers={'Cache-Control': 'no-store'})


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
    app.include_router(image_previews.router)
    app.mount('/assets', StaticFiles(directory=PUBLIC / 'assets', follow_symlink=False), name='assets')

