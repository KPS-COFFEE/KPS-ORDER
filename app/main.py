"""Small independent web service. Authoritative data resides exclusively in PostgreSQL."""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import psycopg
from anyio.to_thread import current_default_thread_limiter
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
# Two request slots per database connection keep the measured small-worker
# queue bounded while allowing non-database work alongside checked-out reads.
REQUEST_THREAD_TOKENS = 8


@asynccontextmanager
async def lifespan(app):
    background_tasks = []
    request_limiter = current_default_thread_limiter()
    previous_thread_tokens = request_limiter.total_tokens
    request_limiter.total_tokens = REQUEST_THREAD_TOKENS
    try:
        from . import staff_geo
        staff_geo.validate_configuration()
        app.state.database_configured = bool(os.getenv('DATABASE_URL'))
        if app.state.database_configured:
            # A bad configured migration/database blocks startup; no storage substitute is created.
            db.migrate()
            from .central_activation_preflight import run as central_preflight
            evidence = await asyncio.to_thread(central_preflight, enabled=os.getenv('KPS_CENTRAL_ACTIVATION_PREFLIGHT') == 'true')
            if evidence is not None:
                print(json.dumps(evidence, sort_keys=True), flush=True)
            from . import recovery
            if recovery.configuration()['poll_configured']:
                background_tasks.append(asyncio.create_task(recovery.poll_loop(), name='backup-status-monitor'))
            if monitoring.configuration()['runtime_configured']:
                background_tasks.append(asyncio.create_task(monitoring.poll_loop(), name='operational-monitor'))
            from . import email_delivery, report_notifications, order_support
            if os.getenv('APP_ENV')!='testing':
                background_tasks.append(asyncio.create_task(order_support.poll_loop(), name='delivery-photo-cleanup'))
                from . import morning_dispatch
                background_tasks.append(asyncio.create_task(morning_dispatch.poll_loop(), name='morning-branch2-dispatch'))
            if email_delivery.configuration()['configured'] and os.getenv('EMAIL_WORKER_ENABLED', 'true').lower() == 'true':
                background_tasks.append(asyncio.create_task(email_delivery.poll_loop(), name='identity-email-outbox'))
            if os.getenv('MANAGER_NOTIFICATION_WORKER_ENABLED','true').lower()=='true' and os.getenv('APP_ENV')!='testing':
                from . import manager_notifications
                background_tasks.append(asyncio.create_task(manager_notifications.poll_loop(),name='manager-notifications'))
            if os.getenv('REPORT_WORKER_ENABLED', 'true').lower() == 'true':
                background_tasks.append(asyncio.create_task(report_notifications.poll_loop(), name='closure-report-worker'))
            from . import branch_payment_webhooks
            if branch_payment_webhooks.worker_enabled() and os.getenv('APP_ENV') != 'testing':
                background_tasks.append(asyncio.create_task(branch_payment_webhooks.poll_loop(), name='branch-payment-inbox'))
            from . import branch_refund_reconciliation
            if branch_refund_reconciliation.enabled() and os.getenv('APP_ENV') != 'testing':
                background_tasks.append(asyncio.create_task(branch_refund_reconciliation.poll_loop(), name='branch-refund-reconciliation'))
        yield
    finally:
        try:
            for task in background_tasks:
                task.cancel()
            for task in background_tasks:
                with suppress(asyncio.CancelledError):
                    await task
        finally:
            try:
                db.close_pools()
            finally:
                request_limiter.total_tokens = previous_thread_tokens


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
    from .database_diagnostics import record_failure
    record_failure(request, exc)
    return JSONResponse({'detail': {'code': 'DATABASE_UNAVAILABLE', 'message': 'قاعدة البيانات غير متاحة مؤقتًا.'}}, status_code=503)


# These routers have disjoint namespaces. Resolve the frequently used order
# routes first without changing a route's dependencies or relative precedence
# within its router. No permission/session result is cached.
app.include_router(orders.router)
from . import branch_payments, branch_payment_renewal
app.include_router(branch_payments.router)
app.include_router(catalog.router)
app.include_router(auth.router)
from . import customer_message_routing
app.include_router(customer_message_routing.router)
from . import experience_policy
app.include_router(experience_policy.router)
from . import first_order_gift
app.include_router(first_order_gift.router)
from . import campaign_reporting
app.include_router(campaign_reporting.router)
from . import storefront_advertising
app.include_router(storefront_advertising.router)
from . import morning_delivery, morning_trial
app.include_router(morning_delivery.router)
app.include_router(morning_trial.router)
from . import customer_map
app.include_router(customer_map.router)
from . import multibrand
app.include_router(multibrand.router)
from . import central_operations
app.include_router(central_operations.router)
from . import driver_order_review
app.include_router(driver_order_review.router)
from . import operating_reports
app.include_router(operating_reports.router)
from . import owner_phone
app.include_router(owner_phone.router)
from . import employee_sms, sms_rollout, sms_pilot_ui
app.include_router(employee_sms.router)
app.include_router(sms_rollout.router)
app.include_router(sms_pilot_ui.router)
# Provider callbacks use their own signed timestamped verification, rather than
# browser Origin or session cookies. They must stay outside the /api namespace.
app.include_router(email_delivery.webhook_router)
from . import branch_payment_webhooks
app.include_router(branch_payment_webhooks.router)
from . import branch_refunds
app.include_router(branch_refunds.router)
from . import branch_payment_reports
app.include_router(branch_payment_reports.router)
from . import branch_payment_policy
app.include_router(branch_payment_policy.router)
from . import owner_dashboard
app.include_router(owner_dashboard.router)
from . import branch_proximity
app.include_router(branch_proximity.router)
from . import manager_notifications
app.include_router(manager_notifications.router)
# Components are separate files within this one service and share canonical tables.
for name in ('dispatch_impact', 'operations', 'driver_support', 'smart_dispatch', 'driver_breaks', 'fuel_exceptions', 'driver_evidence', 'driver_float', 'chat', 'customer_reports', 'reporting', 'admin', 'exports', 'recovery', 'monitoring', 'performance', 'report_notifications', 'maintenance', 'branch_printing', 'invoice_profiles', 'fiscal', 'fiscal_issuance', 'order_support', 'accounting', 'fleet', 'driver_incentives', 'customer_profile', 'customer_legal', 'online_payments', 'order_amendments', 'order_requests', 'order_financial_cases', 'delivery_partial_returns', 'employee_tickets', 'channel_settlement', 'internal_documents', 'driver_closure', 'driver_duty', 'aggregate_driver_closure', 'aggregate_manager_closure', 'financial_clarifications', 'customer_crm', 'owner_audit', 'employee_ticket_workflow'):
    module = importlib.import_module('.' + name, __package__)
    app.include_router(module.router)

from . import dispatch_map
from . import prior_deliveries
app.include_router(prior_deliveries.router)
from . import legacy_invoice_imports
app.include_router(legacy_invoice_imports.router)
from . import legacy_closures
app.include_router(legacy_closures.router)
from . import driver_attribution
app.include_router(driver_attribution.router)
from . import staff_geo_access
app.include_router(staff_geo_access.router)
app.include_router(dispatch_map.router)
from . import map_services
app.include_router(map_services.router)
from . import tracking_ingest, route_eta
app.include_router(tracking_ingest.router)
app.include_router(route_eta.router)
from . import delivery_tracking
app.include_router(delivery_tracking.router)

from . import driver_checks
app.include_router(driver_checks.router)
from . import customer_benefits, customer_engagement
app.include_router(customer_benefits.router)
app.include_router(customer_engagement.router)
from . import delegated_operations, driver_work_hours, driver_hours_reports
app.include_router(delegated_operations.router)
app.include_router(driver_work_hours.router)
app.include_router(driver_hours_reports.router)
from . import task_recovery
app.include_router(task_recovery.router)
from . import enbishan_catalog_import
app.include_router(enbishan_catalog_import.router)


def revision():
    from .runtime_revision import revision as source_revision
    return source_revision('local-unversioned')


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


@app.get('/branch-payment')
def branch_payment_page(user=Depends(auth.require_roles('customer'))):
    if os.getenv('KPS_BRANCH_PAYMENT_CHECKOUT_ENABLED') != 'true':
        raise db.error(404,'PAYMENT_PAGE_DISABLED','الدفع الإلكتروني غير مفعل.')
    return FileResponse(PUBLIC / 'branch-payment.html',media_type='text/html',headers={'Cache-Control':'no-store'})


@app.get('/payment-return')
def branch_payment_return_page():
    # Cross-site provider navigation omits Strict cookies. Serve only a neutral
    # shell; its same-origin API calls must authenticate and verify payment.
    if os.getenv('KPS_BRANCH_PAYMENT_CHECKOUT_ENABLED') != 'true':
        raise db.error(404,'PAYMENT_PAGE_DISABLED','الدفع الإلكتروني غير مفعل.')
    return FileResponse(PUBLIC / 'branch-payment.html',media_type='text/html',headers={'Cache-Control':'no-store'})


@app.get('/ops')
@app.get('/ops.html')
def operations_page():
    path = PUBLIC / 'ops.html'
    if not path.is_file():
        return JSONResponse({'detail': {'code': 'UI_UNAVAILABLE', 'message': 'واجهة التشغيل قيد الإعداد.'}}, status_code=503)
    return FileResponse(path, media_type='text/html', headers={'Cache-Control': 'no-cache'})


@app.get('/campaign-report')
def campaign_report_page():
    # Static shell only. Every report and beneficiary read authenticates the
    # current work session and enforces brand, branch and permission scope.
    return FileResponse(PUBLIC / 'campaign-report.html', media_type='text/html',
                        headers={'Cache-Control': 'private, no-store'})


@app.get('/work-access')
def work_access_page():
    # Neutral entry exchange only; no account data or administrative shell.
    # The fragment token never reaches HTTP logs and never authenticates a user.
    return FileResponse(PUBLIC / 'work-access.html', media_type='text/html',
                        headers={'Cache-Control': 'private, no-store', 'Referrer-Policy': 'no-referrer'})


@app.get('/docs/metrics')
def metric_definitions(user=Depends(auth.current_user)):
    if user['role'] not in {'owner', 'delivery_manager', 'delivery_staff', 'branch_manager', 'driver'}:
        raise db.error(403, 'STAFF_REQUIRED', 'تعريفات تقارير التشغيل متاحة للموظفين المخولين.')
    return FileResponse(ROOT / 'docs' / 'metrics.md', media_type='text/plain; charset=utf-8',
                        headers={'Cache-Control': 'no-store'})


@app.get('/{filename}')
async def frontend_file(filename: str, request: Request):
    # Never mount the repository, data files, migrations, environment or the legacy executable menu.
    if filename not in {'customer.js', 'style.css', 'customer.css', 'ops.js', 'ops.css', 'report.js', 'work-access.js'}:
        raise db.error(404, 'FILE_NOT_FOUND', 'الملف غير موجود.')
    path = PUBLIC / filename
    if not path.is_file() or path.is_symlink():
        raise db.error(404, 'FILE_NOT_FOUND', 'الملف غير موجود.')
    # Honor browser validators for unchanged JS/CSS, just as /assets already
    # does. FileResponse alone always retransmits the complete frontend bundle.
    return await StaticFiles(directory=PUBLIC, follow_symlink=False).get_response(filename, request.scope)


if (PUBLIC / 'assets').exists():
    app.include_router(image_previews.router)
    app.mount('/assets', StaticFiles(directory=PUBLIC / 'assets', follow_symlink=False), name='assets')



from . import financial_treatments
app.include_router(financial_treatments.router)

from . import refund_item_allocations
app.include_router(refund_item_allocations.router)

from . import shared_financial_treatments, manual_bank_matching
app.include_router(shared_financial_treatments.router)
app.include_router(manual_bank_matching.router)
from . import order_financial_treatments
app.include_router(order_financial_treatments.router)

from . import collection_tender_corrections
app.include_router(collection_tender_corrections.router)

from . import cash_bags
app.include_router(cash_bags.router)

from . import order_interventions
app.include_router(order_interventions.router)
from . import preparation_routing
app.include_router(preparation_routing.router)
