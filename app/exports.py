"""Versioned, scope-safe business export and separate-schema PostgreSQL restore."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import time
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Response
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from . import auth, db
from .runtime_revision import revision as source_revision

router = APIRouter(prefix='/api/admin')
FORMAT = 'kps-portable-v2'
TABLES = ('brands', 'app_settings', 'branches', 'delivery_hours_decisions', 'branch_opening_hours_decisions', 'owner_branch_location_decisions', 'products', 'catalog_owner_approvals', 'owner_display_decisions', 'users', 'driver_duty_profiles', 'customer_addresses', 'customer_identity_links', 'assets', 'banners',
          'driver_float_requests', 'driver_float_decisions', 'order_daily_counters', 'order_branch_counters', 'shifts', 'operational_day_reclassifications', 'central_workdays', 'central_workday_legs', 'driver_latest_locations', 'driver_location_health', 'orders', 'complimentary_authorizations', 'complimentary_authorization_revocations', 'delivery_tracking_links', 'branch_payment_attempts', 'branch_checkout_sessions', 'branch_payment_webhooks', 'branch_online_receipts', 'branch_online_refunds', 'branch_refund_reconciliation', 'branch_payment_renewals', 'order_amendments', 'order_revisions', 'order_revision_receipts', 'driver_tasks', 'order_driver_attribution_corrections', 'driver_work_trips', 'driver_work_trip_orders', 'driver_work_trip_events', 'fuel_exceptions', 'driver_break_policies', 'driver_breaks', 'driver_break_events', 'dispatch_offers', 'dispatch_offer_events', 'driver_incentive_decisions', 'driver_incentive_decision_events', 'collections', 'refunds', 'fiscal_previews', 'fiscal_policy_decisions', 'fiscal_documents', 'channel_settlement_allocations', 'custody_transfers',
          'first_order_gift_claims', 'custody_events', 'retained_carryovers', 'manager_handovers', 'driver_support_requests', 'central_manager_handovers', 'central_manager_handover_legs', 'closure_reports', 'manager_financial_matches', 'driver_accounting_settlements', 'accounting_daily_closures',
          'report_corrections', 'conversations', 'chat_attachments',
          'chat_messages', 'conversation_reads', 'conversation_transfers', 'order_events',
          'audit_events', 'recovery_events', 'recovery_state', 'recovery_alerts',
          'idempotency_keys', 'sessions', 'staff_geo_access_grants', 'owner_phone_challenges', 'otp_challenges', 'rate_limits',
          'identity_channels', 'identity_tokens', 'email_outbox', 'email_webhook_events',
          'performance_provinces', 'performance_province_branches', 'province_performance_policies',
          'closure_drafts', 'report_renderings', 'report_notifications',
          'recovery_monitor_state', 'recovery_alert_notifications', 'recovery_worker_observations', 'driver_evidence', 'vehicle_reports', 'vehicle_report_events',
          'branch_print_devices', 'branch_print_jobs', 'support_tickets', 'support_ticket_events', 'delivery_photos',
          'accounting_reviews', 'accounting_cases', 'accounting_case_events', 'accounting_case_files', 'accounting_months', 'fleet_vehicles', 'vehicle_operating_logs', 'vehicle_log_corrections', 'fleet_route_readings', 'driver_check_requests',
          'map_resolution_state', 'map_resolution_clients', 'map_resolution_leases', 'map_resolution_cache',
          'staff_brand_memberships', 'driver_brand_dedications',
          'customer_followups','customer_marketing_notices','customer_marketing_consents','benefit_programs','benefit_rules',
          'customer_wallets','benefit_order_states','customer_wallet_entries', 'promotion_report_closures',
          'prior_delivery_grants','prior_delivery_revocations','prior_delivery_records','legacy_delivery_attestations',
          'shared_driver_closure_declarations','shared_driver_closure_returns','shared_driver_closure_receipts','shared_driver_closure_allocations',
          'shared_manager_closure_declarations','shared_manager_closure_returns','shared_manager_closure_receipts','shared_manager_closure_allocations',
          'financial_case_clarifications','financial_case_clarification_events','legacy_invoice_imports',
          'bank_reconciliation_batches','bank_reconciliation_receipts','bank_reconciliation_decisions','bank_reconciliation_source_claims','bank_reconciliation_bank_lines','bank_reconciliation_bank_claims')
TABLES += ('bank_reconciliation_line_proposals', 'bank_reconciliation_line_decisions')
TABLES += ('collection_tender_correction_proposals','collection_tender_corrections','collection_tender_correction_rejections')
# Physical GPS/session history and mixed-brand itineraries belong to the private
# full archive. A brand export must not expose another brand's route or session.
TRACKING_PRIVATE_TABLES = {'driver_tracking_points', 'driver_trip_stops',
                          'driver_route_eta_state', 'driver_route_eta_expectations'}
TABLES += ('driver_tracking_points', 'driver_trip_stops', 'driver_route_eta_state', 'driver_route_eta_expectations')
TABLES += ('driver_cash_bags','driver_cash_bag_counts','manager_vault_closures')
SHARED_PHYSICAL_TABLES = {'driver_cash_bags','driver_cash_bag_counts','manager_vault_closures','shared_driver_closure_declarations','shared_driver_closure_returns','shared_driver_closure_receipts','shared_driver_closure_allocations',
                          'shared_manager_closure_declarations','shared_manager_closure_returns','shared_manager_closure_receipts','shared_manager_closure_allocations'}
# Combined physical actuals cannot be split into invented brand amounts in a brand export.
# Full private archives retain the whole immutable declaration and its provenance.
# Global bank statement originals can span sellers; private full archives retain them.
SHARED_BANK_TABLES = {'bank_reconciliation_bank_lines'}
AUTH_TABLES = TRACKING_PRIVATE_TABLES | SHARED_PHYSICAL_TABLES | SHARED_BANK_TABLES | {'central_manager_handovers', 'central_manager_handover_legs', 'central_workdays', 'central_workday_legs', 'delivery_tracking_links', 'staff_brand_memberships', 'driver_brand_dedications', 'branch_checkout_sessions', 'branch_payment_webhooks', 'branch_refund_reconciliation', 'sessions', 'owner_phone_challenges', 'otp_challenges', 'rate_limits', 'idempotency_keys',
               'identity_tokens', 'email_outbox', 'email_webhook_events', 'branch_print_devices', 'staff_geo_access_grants',
               'map_resolution_state', 'map_resolution_clients', 'map_resolution_leases', 'map_resolution_cache'}
INFRASTRUCTURE_TABLES = {'recovery_events', 'recovery_state', 'recovery_alerts',
                         'recovery_monitor_state', 'recovery_alert_notifications', 'recovery_worker_observations'}
# Private full archives preserve exact evidence; restore retires these anonymous
# capabilities/budgets and location cache only after validating the snapshot.
MAP_RUNTIME_TABLES = {'driver_route_eta_state', 'map_resolution_state', 'map_resolution_clients',
                      'map_resolution_leases', 'map_resolution_cache'}
PUBLIC = db.ROOT / 'public' / 'assets'
MAX_WEB_EXPORT = 64 * 1024 * 1024
INDEPENDENT_RESTORE_DATABASE_ID = 'dpg-db50dcdckfvc738gsrhg-a'


def _plain(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return {'$decimal': str(value)}
    raise TypeError(f'Unsupported export type: {type(value).__name__}')


def _encode(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=_plain).encode()


def _scoped_sql(table, brand, *, bank_table_available=True):
    if brand and table in {'driver_route_eta_state', 'driver_route_eta_expectations'}:
        return 'FALSE', ()
    if brand and table in {'driver_tracking_points', 'driver_trip_stops'}:
        return 'brand_id=%s', (brand,)
    if brand and table in {'collection_tender_correction_proposals','collection_tender_corrections'}:
        return 'brand_id=%s', (brand,)
    if brand and table == 'collection_tender_correction_rejections':
        return 'proposal_id IN (SELECT id FROM collection_tender_correction_proposals WHERE brand_id=%s)', (brand,)
    if brand and table == 'bank_reconciliation_line_proposals':
        return 'brand_id=%s', (brand,)
    if brand and table == 'bank_reconciliation_line_decisions':
        return 'proposal_id IN (SELECT id FROM bank_reconciliation_line_proposals WHERE brand_id=%s)', (brand,)
    if brand and table == 'bank_reconciliation_batches':
        return 'brand_id=%s', (brand,)
    if brand and table in {'bank_reconciliation_receipts','bank_reconciliation_decisions','bank_reconciliation_source_claims'}:
        return 'batch_id IN (SELECT id FROM bank_reconciliation_batches WHERE brand_id=%s)', (brand,)
    if brand and table == 'bank_reconciliation_bank_claims':
        return 'receipt_id IN (SELECT r.id FROM bank_reconciliation_receipts r JOIN bank_reconciliation_batches b ON b.id=r.batch_id WHERE b.brand_id=%s)', (brand,)
    if brand and table == 'driver_duty_profiles':
        return 'driver_id IN (SELECT id FROM users WHERE brand_id=%s)', (brand,)
    if brand and table == 'financial_case_clarifications':
        return 'order_id IN (SELECT id FROM orders WHERE brand_id=%s)', (brand,)
    if brand and table == 'financial_case_clarification_events':
        return 'clarification_id IN (SELECT id FROM financial_case_clarifications WHERE order_id IN (SELECT id FROM orders WHERE brand_id=%s))', (brand,)
    if brand and table in {'staff_geo_access_grants', 'legacy_delivery_attestations','legacy_invoice_imports'}:
        return 'brand_id=%s', (brand,)
    if brand and table in {'prior_delivery_grants','prior_delivery_records'}:
        return 'brand_id=%s', (brand,)
    if brand and table == 'prior_delivery_revocations':
        return 'grant_id IN (SELECT id FROM prior_delivery_grants WHERE brand_id=%s)', (brand,)
    if brand and table == 'driver_work_trips':
        return 'brand_id=%s', (brand,)
    if brand and table in {'driver_work_trip_orders', 'driver_work_trip_events'}:
        return 'trip_id IN (SELECT id FROM driver_work_trips WHERE brand_id=%s)', (brand,)
    if brand is None:
        return 'TRUE', ()
    direct = {'operational_day_reclassifications', 'complimentary_authorizations', 'complimentary_authorization_revocations', 'driver_support_requests', 'promotion_report_closures', 'delivery_tracking_links', 'driver_location_health', 'first_order_gift_claims', 'driver_latest_locations', 'branch_payment_attempts', 'branch_checkout_sessions', 'branch_payment_webhooks', 'branch_online_receipts', 'branch_online_refunds', 'branch_refund_reconciliation', 'branch_payment_renewals', 'fiscal_policy_decisions', 'fuel_exceptions', 'driver_break_policies', 'driver_breaks', 'driver_break_events', 'dispatch_offers', 'dispatch_offer_events', 'driver_float_requests','driver_float_decisions','manager_financial_matches', 'driver_accounting_settlements', 'accounting_daily_closures','customer_addresses','customer_identity_links','branches', 'products', 'catalog_owner_approvals', 'owner_display_decisions', 'owner_branch_location_decisions', 'users', 'assets', 'banners', 'shifts', 'orders', 'closure_reports',
              'conversations', 'otp_challenges', 'identity_channels', 'identity_tokens', 'email_outbox', 'email_webhook_events',
              'performance_provinces', 'performance_province_branches', 'province_performance_policies',
              'report_notifications', 'driver_evidence', 'vehicle_reports', 'branch_print_devices', 'branch_print_jobs',
              'accounting_reviews', 'accounting_cases', 'accounting_months', 'fleet_vehicles', 'driver_check_requests'} | INFRASTRUCTURE_TABLES
    if table == 'brands':
        return 'id=%s', (brand,)
    if table in {'customer_followups','customer_marketing_notices','customer_marketing_consents','benefit_programs','benefit_rules',
                 'customer_wallets','benefit_order_states','customer_wallet_entries'}:
        return 'brand_id=%s', (brand,)
    if table == 'app_settings' or table in direct:
        return 'brand_id=%s', (brand,)
    if table in {'delivery_hours_decisions','branch_opening_hours_decisions','order_daily_counters','order_branch_counters'}:
        return 'branch_id IN (SELECT id FROM branches WHERE brand_id=%s)', (brand,)
    if table in {'order_amendments','order_revisions','order_revision_receipts','driver_tasks','order_driver_attribution_corrections', 'driver_incentive_decisions', 'driver_incentive_decision_events', 'collections', 'refunds', 'fiscal_previews', 'fiscal_documents', 'channel_settlement_allocations', 'order_events', 'support_tickets', 'delivery_photos'}:
        return 'order_id IN (SELECT id FROM orders WHERE brand_id=%s)', (brand,)
    if table == 'support_ticket_events':
        return 'ticket_id IN (SELECT id FROM support_tickets WHERE order_id IN (SELECT id FROM orders WHERE brand_id=%s))', (brand,)
    if table in {'accounting_case_events','accounting_case_files'}:
        return 'case_id IN (SELECT id FROM accounting_cases WHERE brand_id=%s)', (brand,)
    if table in {'custody_transfers', 'retained_carryovers', 'manager_handovers'}:
        return 'from_shift_id IN (SELECT id FROM shifts WHERE brand_id=%s)', (brand,)
    if table == 'custody_events':
        return 'shift_id IN (SELECT id FROM shifts WHERE brand_id=%s)', (brand,)
    if table in {'vehicle_operating_logs','vehicle_log_corrections','fleet_route_readings'}:
        return 'shift_id IN (SELECT id FROM shifts WHERE brand_id=%s)', (brand,)
    if table == 'vehicle_report_events':
        return 'report_id IN (SELECT id FROM vehicle_reports WHERE brand_id=%s)', (brand,)
    if table in {'report_corrections', 'report_renderings'}:
        return 'report_id IN (SELECT id FROM closure_reports WHERE brand_id=%s)', (brand,)
    if table == 'closure_drafts':
        return 'shift_id IN (SELECT id FROM shifts WHERE brand_id=%s)', (brand,)
    if table in {'chat_attachments', 'chat_messages', 'conversation_reads', 'conversation_transfers'}:
        return 'conversation_id IN (SELECT id FROM conversations WHERE brand_id=%s)', (brand,)
    if table == 'audit_events':
        # Shared staff can act for another seller. Financial evidence follows
        # its canonical source, never the employee's home-brand identity.
        bank_scope = ("(entity_type='bank_batch' AND entity_id IN (SELECT id FROM bank_reconciliation_batches WHERE brand_id=%s) "
                      "OR entity_type='bank_line_proposal' AND entity_id IN (SELECT id FROM bank_reconciliation_line_proposals WHERE brand_id=%s))") if bank_table_available else "FALSE AND %s::text IS NOT NULL AND %s::text IS NOT NULL"
        return ("CASE WHEN action LIKE 'collection_tender.%%' THEN "
                "entity_type='order' AND entity_id IN (SELECT id FROM orders WHERE brand_id=%s) "
                "WHEN action LIKE 'accounting.financial_treatment.%%' THEN "
                "entity_type='report' AND entity_id IN (SELECT id FROM closure_reports WHERE brand_id=%s) "
                "WHEN action LIKE 'accounting.refund_item_allocation.%%' OR action LIKE 'accounting.order_financial_treatment.%%' THEN "
                "entity_type='order' AND entity_id IN (SELECT id FROM orders WHERE brand_id=%s) "
                "WHEN action LIKE 'accounting.bank_%%' THEN "
                +bank_scope+" "
                "WHEN action LIKE 'employee.ticket.%%' THEN details->>'brand_id'=%s "
                "ELSE actor_id IN (SELECT id FROM users WHERE brand_id=%s) END", (brand,brand,brand,brand,brand,brand,brand))
    if table in {'sessions', 'idempotency_keys'}:
        return 'user_id IN (SELECT id FROM users WHERE brand_id=%s)', (brand,)
    raise ValueError('An unscoped table cannot enter a brand business export.')


def _totals(conn, brand_id):
    clause, values = _scoped_sql('orders', brand_id)
    order = conn.execute('SELECT count(*) orders,coalesce(sum(total_halalas),0)::bigint total_halalas FROM orders WHERE ' + clause, values).fetchone()
    col_clause, vals = _scoped_sql('collections', brand_id)
    ref_clause, ref_vals = _scoped_sql('refunds', brand_id)
    order['collected_halalas'] = conn.execute('SELECT coalesce(sum(amount_halalas),0)::bigint AS n FROM collections WHERE ' + col_clause, vals).fetchone()['n']
    order['refunded_halalas'] = conn.execute('SELECT coalesce(sum(amount_halalas),0)::bigint AS n FROM refunds WHERE ' + ref_clause, ref_vals).fetchone()['n']
    return order


def build_archive(conn, *, brand_id=None, full=False, max_bytes=None) -> tuple[bytes, dict]:
    """Caller supplies a read-only repeatable-read transaction for a consistent snapshot."""
    if not full and not brand_id:
        raise ValueError('Business export requires an explicit brand.')
    output = io.BytesIO()
    files, counts, size = {}, {}, 0
    available = {row['table_name'] for row in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE'").fetchall()}
    selected = [table for table in TABLES if table in available and (full or table not in AUTH_TABLES)]
    if available - set(TABLES) - {'schema_migrations'}:
        raise db.error(409, 'EXPORT_SCHEMA_UNSUPPORTED', 'أضيفت سجلات تشغيلية جديدة. يلزم تحديث صيغة التصدير قبل إخراج نسخة كاملة.')
    if not full:
        # A scoped export cannot carry staffing completion while omitting the
        # cross-brand immutable receipt which justifies it. Preserve the exact
        # source and direct the owner to a whole private archive instead.
        for shared in ('shared_driver_closure_declarations','shared_manager_closure_declarations'):
            if shared in available and conn.execute(sql.SQL(
                'SELECT 1 FROM {} d JOIN central_workday_legs l ON l.workday_id=d.workday_id '
                'JOIN shifts s ON s.id=l.shift_id WHERE s.brand_id=%s LIMIT 1'
            ).format(sql.Identifier(shared)), (brand_id,)).fetchone():
                raise db.error(409, 'EXPORT_SHARED_PHYSICAL_SCOPE',
                    'توجد تقفيلات فعلية مشتركة بين البراندين. استخدم الأرشيف الخاص الكامل لحفظ الأصل والاستلام وإسناد المحاسبة معًا؛ لا يمكن فصلها في تصدير براند واحد.')
    migrations = conn.execute('SELECT version,sha256 FROM schema_migrations ORDER BY version').fetchall()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        def add(name, content):
            nonlocal size
            size += len(content)
            if max_bytes and size > max_bytes:
                raise db.error(413, 'EXPORT_SIZE_LIMIT', 'حجم التصدير يتجاوز حد التنزيل المباشر. استخدم النسخة الاحتياطية الخاصة.')
            archive.writestr(name, content)
            files[name] = {'sha256': hashlib.sha256(content).hexdigest(), 'size_bytes': len(content)}

        for table in selected:
            clause, params = _scoped_sql(table, None if full else brand_id,
                                         bank_table_available='bank_reconciliation_batches' in available)
            ordering = ' ORDER BY kind DESC,opened_at,id' if table == 'shifts' else ''
            rows = conn.execute(sql.SQL('SELECT * FROM {} WHERE ' + clause + ordering).format(sql.Identifier(table)), params).fetchall()
            counts[table] = len(rows)
            for index, row in enumerate(rows):
                if table == 'users' and not full:
                    row.pop('password_hash', None)
                    row.pop('username', None)
                if table in {'report_notifications', 'recovery_alert_notifications'} and not full:
                    # A business export never conveys an authentication-mail capability.
                    row['email_outbox_id'] = None
                for column, value in list(row.items()):
                    if isinstance(value, (bytes, bytearray, memoryview)):
                        identity = str(row.get('id', index))
                        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identity):
                            identity = hashlib.sha256(identity.encode()).hexdigest()
                        name = f'blobs/{table}/{identity}/{column}.bin'
                        contents = bytes(value)
                        add(name, contents)
                        row[column] = {'$binary': name, 'sha256': files[name]['sha256'], 'size_bytes': len(contents)}
            add(f'data/{table}.json', _encode(rows))

        for path in sorted(PUBLIC.glob('*')) if PUBLIC.exists() else []:
            if path.is_file() and not path.is_symlink() and re.fullmatch(r'[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|webp|svg)', path.name):
                if full or brand_id == 'kps':
                    add('static/assets/' + path.name, path.read_bytes())
        manifest = {'format': FORMAT, 'mode': 'database' if full else 'business', 'created_at': datetime.now(timezone.utc).isoformat(),
                    'source_revision': source_revision('local-unversioned'),
                    'scope': {'brand_id': None if full else brand_id}, 'migrations': migrations,
                    'table_order': selected, 'counts': counts, 'totals': _totals(conn, None if full else brand_id),
                    'files': files, 'timezone': 'Asia/Riyadh', 'currency': 'SAR',
                    'source_identifier_fields': {'branches': ['id', 'branch_no', 'source_id'],
                                                 'products': ['id', 'settings.source_id'], 'orders': ['id', 'snapshot']},
                    'deduplication': 'Preserve primary/source IDs; verify counts/totals before import. Never replay sales or collections.',
                    'credentials_included': bool(full),
                    'map_runtime_import_policy': 'Private database archives only; verify then discard signing key, counters, leases and location cache on isolated restore.',
                    'excluded_tables': [] if full else sorted(AUTH_TABLES),
                    'shared_physical_exclusion_reason': None if full else 'Combined actual declarations require a full private archive; no invented brand allocation is exported.',
                    'infrastructure_tables': [table for table in selected if table in INFRASTRUCTURE_TABLES],
                    'infrastructure_import_policy': 'Preserve observations and failure queues as historical source records; do not activate source hosting configuration or replay alerts.'}
        archive.writestr('manifest.json', _encode(manifest))
    return output.getvalue(), manifest


def verify_archive(contents: bytes, *, require_full=False):
    try:
        archive = zipfile.ZipFile(io.BytesIO(contents))
        names = archive.namelist()
        if len(set(names)) != len(names) or 'manifest.json' not in names:
            raise ValueError('Duplicate or missing archive records.')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('format') != FORMAT or manifest.get('mode') not in {'business', 'database'}:
            raise ValueError('Unsupported portable format.')
        if require_full and manifest['mode'] != 'database':
            raise ValueError('Business exports cannot restore authentication or the complete database.')
        if set(names) != set(manifest.get('files', {})) | {'manifest.json'}:
            raise ValueError('Unexpected or missing archive file.')
        for name, descriptor in manifest['files'].items():
            if name.startswith('/') or '..' in name.split('/') or '\\' in name:
                raise ValueError('Unsafe archive path.')
            member = archive.getinfo(name)
            if member.file_size != descriptor['size_bytes'] or member.file_size > 100 * 1024 * 1024:
                raise ValueError('Archive size mismatch.')
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != descriptor['sha256']:
                raise ValueError('Archive checksum mismatch.')
        if manifest['table_order'] != [table for table in TABLES if table in manifest['table_order']]:
            raise ValueError('Unknown or incorrectly ordered export tables.')
        for table in manifest['table_order']:
            if len(json.loads(archive.read(f'data/{table}.json'))) != manifest['counts'][table]:
                raise ValueError('Export row count mismatch.')
        return archive, manifest
    except (zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError):
        raise ValueError('Invalid portable archive.') from None


def _restore_target(target_url, target_database_id=None):
    try:
        connection = conninfo_to_dict(target_url)
    except psycopg.Error:
        raise ValueError('Invalid private recovery connection configuration.') from None
    name, host = connection.get('dbname', ''), connection.get('host', '')
    local = host in {'localhost', '127.0.0.1', '::1', 'postgres'} and (name.startswith('test_') or name.endswith('_test'))
    # Explicit opt-in to the existing recovery resource's INTERNAL hostname.
    # An arbitrary remote host, public Render URL or runtime DB is rejected.
    internal = target_database_id == INDEPENDENT_RESTORE_DATABASE_ID and host == INDEPENDENT_RESTORE_DATABASE_ID
    if not local and not internal:
        raise ValueError('Restore requires a local test database or the explicitly bound INTERNAL recovery database.')
    if connection.get('options'):
        raise ValueError('Restore URL options are prohibited; tool owns the new schema.')
    return connection


def _restored_evidence(conn, archive, manifest):
    """Read the inserted target records, then compare IDs and every stored blob."""
    identity_digests, binary_count = {}, 0
    for table in manifest['table_order']:
        source = json.loads(archive.read(f'data/{table}.json'))
        target = conn.execute(sql.SQL('SELECT * FROM {}').format(sql.Identifier(table))).fetchall()
        keys = [row['column_name'] for row in conn.execute(
            "SELECT a.attname column_name FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
            "WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY a.attname", (table,)).fetchall()]
        if not keys:
            raise ValueError('Target table lacks a primary key.')
        identity = lambda row: _encode({key: row[key] for key in keys})
        source_ids, target_ids = sorted(identity(row) for row in source), sorted(identity(row) for row in target)
        if source_ids != target_ids:
            raise ValueError('Restored primary identifiers differ from snapshot.')
        identity_digests[table] = hashlib.sha256(b'\n'.join(target_ids)).hexdigest()
        indexed = {identity(row): row for row in target}
        for row in source:
            actual = indexed[identity(row)]
            for column, value in row.items():
                if isinstance(value, dict) and '$binary' in value:
                    restored = bytes(actual[column])
                    if len(restored) != value['size_bytes'] or hashlib.sha256(restored).hexdigest() != value['sha256']:
                        raise ValueError('Restored binary checksum mismatch.')
                    binary_count += 1
    return identity_digests, binary_count


def migration_manifest():
    paths = list((db.ROOT / 'migrations').glob('*.sql'))
    from . import multibrand
    if multibrand.enabled():
        multibrand.isolation_guard()
        paths.extend((db.ROOT / 'app' / 'multibrand_migrations').glob('*.sql'))
    return [{'version': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(paths, key=lambda path: path.name)]


def _verify_staffing_markers(conn, available):
    """Validate imported physical completion against immutable full-archive evidence."""
    proofs = ["""(s.kind='driver' AND EXISTS (
        SELECT 1 FROM central_workday_legs l
        JOIN central_workdays w ON w.id=l.workday_id
        JOIN shared_driver_closure_declarations d ON d.workday_id=w.id
        JOIN shared_driver_closure_receipts r ON r.declaration_id=d.id
        LEFT JOIN shared_driver_closure_allocations a ON a.declaration_id=d.id
        WHERE l.shift_id=s.id AND w.user_id=s.user_id AND r.created_at=s.staffing_ended_at
          AND coalesce(s.submission->>'manager_approval_required','false')<>'true'
          AND NOT EXISTS (SELECT 1 FROM shared_driver_closure_returns returned WHERE returned.declaration_id=d.id)
          AND (a.id IS NOT NULL OR d.retained_halalas >=
             (SELECT coalesce(sum((j->>'permanent_float_halalas')::bigint),0)
              FROM jsonb_array_elements(d.expected_snapshot) j)
             OR EXISTS (SELECT 1 FROM audit_events e JOIN users u ON u.id=e.details->>'responsible_user_id'
                WHERE e.action='shared_driver.float_followup' AND e.entity_type='central_workday' AND e.entity_id=d.workday_id
                  AND e.details->>'declaration_id'=d.id AND e.details->>'receipt_id'=r.id
                  AND e.details->>'due_at' IS NOT NULL AND e.details->>'no_employee_liability_assumed'='true'
                  AND (e.details->>'declared_retained_halalas')::bigint=d.retained_halalas))))"""]
    if 'shared_manager_closure_receipts' in available:
        proofs.append("""(s.kind='manager' AND EXISTS (
            SELECT 1 FROM central_workday_legs l
            JOIN central_workdays w ON w.id=l.workday_id
            JOIN shared_manager_closure_declarations d ON d.workday_id=w.id
            JOIN shared_manager_closure_receipts r ON r.declaration_id=d.id
            WHERE l.shift_id=s.id AND w.user_id=s.user_id AND r.created_at=s.staffing_ended_at
              AND r.received_by=d.recipient_user_id
              AND NOT EXISTS (SELECT 1 FROM shared_manager_closure_returns returned
                              WHERE returned.declaration_id=d.id)))""")
    if 'driver_cash_bags' in available:
        proofs.append("""(s.kind='driver' AND EXISTS (
            SELECT 1 FROM driver_cash_bags b JOIN central_workday_legs l ON l.workday_id=b.workday_id
            WHERE l.shift_id=s.id AND b.driver_id=s.user_id AND b.destination='branch'
              AND b.deposited_at=s.staffing_ended_at
              AND coalesce(s.submission->>'manager_approval_required','false')<>'true'))""")
    if 'driver_cash_bags' in available:
        proofs.append("""(s.kind='driver' AND EXISTS (
    SELECT 1 FROM central_workday_legs l
    JOIN central_workdays w ON w.id=l.workday_id
    JOIN shared_driver_closure_declarations d ON d.workday_id=w.id
    JOIN audit_events e ON e.entity_type='central_workday' AND e.entity_id=w.id
      AND e.action='shared_driver.manager_approved'
    WHERE l.shift_id=s.id AND w.user_id=s.user_id
      AND d.id=s.submission->>'shared_declaration_id'
      AND d.id=s.submission->'manager_approval'->>'declaration_id'
      AND d.id=(SELECT newest.id FROM shared_driver_closure_declarations newest WHERE newest.workday_id=w.id ORDER BY newest.revision DESC LIMIT 1)
      AND e.actor_id=s.submission->'manager_approval'->>'approved_by'
      AND e.actor_id<>s.user_id
      AND e.details @> (s.submission->'manager_approval')
      AND e.details->>'submitted_by'=d.recorded_by
      AND e.details->>'submission_reason'=d.reason
      AND e.details->>'delegated_submission'=CASE WHEN d.recorded_by<>s.user_id THEN 'true' ELSE 'false' END
      AND (d.recorded_by=s.user_id OR (d.recorded_by=e.actor_id AND length(btrim(d.reason))>0))
      AND e.details->>'cash_receipt_recorded'='false'
      AND (e.details->>'approved_at')::timestamptz=s.staffing_ended_at
      AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(d.expected_snapshot) j WHERE j->>'manager_user_id'<>e.actor_id)
      AND NOT EXISTS (SELECT 1 FROM shared_driver_closure_returns returned WHERE returned.declaration_id=d.id)
      AND (EXISTS (SELECT 1 FROM driver_cash_bags b WHERE b.declaration_id=d.id AND b.driver_id=s.user_id AND b.destination='branch')
        OR EXISTS (SELECT 1 FROM shared_driver_closure_receipts r WHERE r.declaration_id=d.id))
  ))""")
    if 'manager_vault_closures' in available:
        proofs.append("""(s.kind='manager' AND EXISTS (
            SELECT 1 FROM manager_vault_closures v JOIN central_workday_legs l ON l.workday_id=v.workday_id
            WHERE l.shift_id=s.id AND v.manager_id=s.user_id AND v.created_at=s.staffing_ended_at))""")
    invalid = conn.execute("SELECT s.id FROM shifts s WHERE s.staffing_ended_at IS NOT NULL "
        "AND (s.status NOT IN ('submitted','closed') OR NOT ("+' OR '.join(proofs)+")) LIMIT 1").fetchone()
    if invalid:
        raise ValueError('Restored staffing completion lacks its immutable physical receipt.')


def restore_archive(contents: bytes, *, target_url: str, target_schema: str,
                    target_database_id: str | None = None, asset_directory: str | Path | None = None) -> dict:
    """Restore to a NEW isolated test schema; no existing schema is overwritten.

    Remote operation is limited to the pre-existing recovery resource over its
    private internal hostname. Static files require an explicitly new directory.
    """
    started = time.monotonic()
    archive, manifest = verify_archive(contents, require_full=True)
    if not re.fullmatch(r'kps_restore_[a-f0-9]{32}', target_schema):
        raise ValueError('Restore schema must be kps_restore_ plus a new UUID hex value.')
    _restore_target(target_url, target_database_id)
    expected = migration_manifest()
    if manifest['migrations'] != expected:
        raise ValueError('Archive migrations do not match this code. Restore with matching code first.')
    destination, files_restored = Path(asset_directory) if asset_directory is not None else None, 0
    discarded_worker_pulses = 0
    discarded_monitor_states = 0
    discarded_map_runtime = {}
    revoked_geo_grants = 0
    if destination and (destination.exists() or destination.is_symlink()):
        raise ValueError('Static asset restoration refuses existing destinations.')
    with psycopg.connect(target_url, row_factory=dict_row, connect_timeout=10) as conn:
        with conn.transaction():
            if conn.execute('SELECT 1 FROM information_schema.schemata WHERE schema_name=%s', (target_schema,)).fetchone():
                raise ValueError('Restore refuses existing schemas. Choose a new UUID.')
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(target_schema)))
            conn.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(target_schema)))
            db.apply_migrations(conn, seed=False, owner_decisions=False)
            actual = {row['table_name'] for row in conn.execute(
                'SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type=%s',
                (target_schema, 'BASE TABLE')).fetchall()} - {'schema_migrations'}
            if set(manifest['table_order']) != actual:
                raise ValueError('Backup is missing application tables; restoration aborted.')
            # Import stored ledger rows rather than re-materializing them from
            # shifts. These two derived-data triggers alone are disabled within
            # the new isolated restore schema and this transaction.
            conn.execute('ALTER TABLE shifts DISABLE TRIGGER sync_vehicle_log')
            conn.execute('ALTER TABLE users DISABLE TRIGGER register_fleet_vehicle')
            conn.execute('ALTER TABLE customer_wallet_entries DISABLE TRIGGER wallet_entry_balance')
            # Physical completion is imported with its exact historical receipt;
            # its insert guard normally forbids creating already-ended staffing.
            # Disable only in this newly created schema, then verify all markers
            # against immutable receipts before restoring the live write guard.
            restore_staffing_guard = 'shared_driver_closure_receipts' in actual
            if restore_staffing_guard:
                conn.execute('ALTER TABLE shifts DISABLE TRIGGER guarded_staffing_end')
            for table in manifest['table_order']:
                column_info = conn.execute(
                    'SELECT column_name,udt_name,is_nullable,is_generated FROM information_schema.columns WHERE table_schema=%s AND table_name=%s',
                    (target_schema, table)).fetchall()
                columns = {row['column_name']: row['udt_name'] for row in column_info}
                nullable = {row['column_name'] for row in column_info if row['is_nullable']=='YES'}
                generated = {row['column_name'] for row in column_info if row['is_generated']=='ALWAYS'}
                for row in json.loads(archive.read(f'data/{table}.json')):
                    if set(row) != set(columns):
                        raise ValueError('Backup columns do not match target.')
                    values = []
                    writable = {column: value for column,value in row.items() if column not in generated}
                    for column, value in writable.items():
                        if columns[column] == 'bytea' and value is not None:
                            if not isinstance(value, dict) or set(value) != {'$binary', 'sha256', 'size_bytes'}:
                                raise ValueError('Invalid binary reference.')
                            payload = archive.read(value['$binary'])
                            if len(payload) != value['size_bytes'] or hashlib.sha256(payload).hexdigest() != value['sha256']:
                                raise ValueError('Attachment checksum mismatch.')
                            values.append(payload)
                        elif columns[column] in {'json', 'jsonb'}:
                            values.append(None if value is None and column in nullable else db.json(value))
                        elif isinstance(value, dict) and '$decimal' in value:
                            values.append(Decimal(value['$decimal']))
                        else:
                            values.append(value)
                    statement = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
                        sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, writable)),
                        sql.SQL(',').join(sql.Placeholder() for _ in writable))
                    conn.execute(statement, values)
            # Validate deferred references/projections before changing trigger
            # state: PostgreSQL refuses ALTER TABLE with pending FK events on
            # a populated wallet ledger. A consistent snapshot has no new
            # benefit delta to materialize here.
            conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
            conn.execute('ALTER TABLE shifts ENABLE TRIGGER sync_vehicle_log')
            conn.execute('ALTER TABLE users ENABLE TRIGGER register_fleet_vehicle')
            conn.execute('ALTER TABLE customer_wallet_entries ENABLE TRIGGER wallet_entry_balance')
            if restore_staffing_guard:
                _verify_staffing_markers(conn, actual)
                conn.execute('ALTER TABLE shifts ENABLE TRIGGER guarded_staffing_end')
            consent_sequence=conn.execute("SELECT pg_get_serial_sequence('customer_marketing_consents','seq') AS name").fetchone()['name']
            conn.execute("SELECT setval(%s::regclass,greatest(coalesce((SELECT max(seq) FROM customer_marketing_consents),0),1),EXISTS(SELECT 1 FROM customer_marketing_consents))",(consent_sequence,))
            # Restores preserve history, never replay physical printing or
            # reactivate device credentials copied from an older snapshot.
            if 'branch_print_devices' in actual:
                conn.execute("UPDATE branch_print_devices SET revoked_at=coalesce(revoked_at,now()),token_hash=NULL,pair_hash=NULL,pair_expires_at=NULL")
                conn.execute("UPDATE branch_print_jobs SET status='cancelled',finished_at=now() WHERE status='queued'")
                conn.execute("UPDATE branch_print_jobs SET status='uncertain',finished_at=now() WHERE status='claimed'")
            if 'chat_messages' in actual:
                sequence = conn.execute("SELECT pg_get_serial_sequence('chat_messages','seq') AS name").fetchone()['name']
                if sequence:
                    conn.execute("SELECT setval(%s::regclass,greatest(coalesce((SELECT max(seq) FROM chat_messages),0),1),EXISTS(SELECT 1 FROM chat_messages))", (sequence,))
            if 'orders' in actual:
                conn.execute("SELECT setval('order_human_number_seq',greatest(coalesce((SELECT max(order_no) FROM orders),0),1),EXISTS(SELECT 1 FROM orders))")
            if 'fiscal_documents' in manifest['table_order']:
                conn.execute("SELECT setval('fiscal_document_number_seq',greatest(coalesce((SELECT max(substring(number from '[0-9]+$')::bigint) FROM fiscal_documents),0),1),EXISTS(SELECT 1 FROM fiscal_documents))")
            if 'fiscal_previews' in manifest['table_order']:
                conn.execute("SELECT setval('fiscal_preview_number_seq',greatest(coalesce((SELECT max(substring(number from '[0-9]+$')::bigint) FROM fiscal_previews),0),1),EXISTS(SELECT 1 FROM fiscal_previews))")
            if 'shifts' in actual:
                sequence = conn.execute("SELECT pg_get_serial_sequence('shifts','shift_no') AS name").fetchone()['name']
                if sequence:
                    conn.execute("SELECT setval(%s::regclass,greatest(coalesce((SELECT max(shift_no) FROM shifts),0),1),EXISTS(SELECT 1 FROM shifts))", (sequence,))
            counts = {table: conn.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(table))).fetchone()['n']
                      for table in manifest['table_order']}
            if counts != manifest['counts'] or _totals(conn, None) != manifest['totals']:
                raise ValueError('Restored counts or monetary totals differ from snapshot.')
            identities, binary_count = _restored_evidence(conn, archive, manifest)
            # Snapshot verification precedes retirement. Restored entry links
            # and browser exceptions must never become live capabilities again.
            # This is a system restore action, not an invented owner approval.
            if 'staff_geo_access_grants' in actual:
                revoked_geo_grants = conn.execute(
                    "UPDATE staff_geo_access_grants SET revoked_at=now(),revoked_by=NULL,"
                    "revocation_source='restore',revocation_reason='Retired after isolated restore' "
                    'WHERE revoked_at IS NULL').rowcount
                if revoked_geo_grants:
                    db.audit(conn, None, 'restore.staff_geo_grants_revoked', 'runtime', target_schema,
                             {'grants': revoked_geo_grants})
            # Verify archived counts/identifiers first, then retire only this
            # newly restored schema's ephemeral runtime pulses. Historical
            # provider backup observations and owner alerts stay preserved.
            for table in sorted(MAP_RUNTIME_TABLES & actual):
                discarded_map_runtime[table] = counts.get(table, 0)
                conn.execute(sql.SQL('DELETE FROM {}').format(sql.Identifier(table)))
            if any(discarded_map_runtime.values()):
                db.audit(conn, None, 'restore.map_runtime_discarded', 'runtime', target_schema,
                         {'tables': discarded_map_runtime})
            if 'recovery_worker_observations' in actual:
                discarded_worker_pulses = counts['recovery_worker_observations']
                discarded_monitor_states = counts.get('recovery_monitor_state', 0)
                conn.execute('DELETE FROM recovery_worker_observations')
                conn.execute('DELETE FROM recovery_monitor_state')
                if discarded_worker_pulses or discarded_monitor_states:
                    db.audit(conn, None, 'restore.ephemeral_worker_observations_discarded', 'runtime', target_schema,
                             {'tables': {'recovery_worker_observations': discarded_worker_pulses,
                                         'recovery_monitor_state': discarded_monitor_states}})
            if destination:
                # Creation is exclusive. Failure removes only the directory
                # created by this invocation and rolls back the target schema.
                destination.mkdir(mode=0o700)
                try:
                    for name, descriptor in manifest['files'].items():
                        if not name.startswith('static/assets/'):
                            continue
                        basename = name.removeprefix('static/assets/')
                        if not re.fullmatch(r'[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|webp|svg)', basename):
                            raise ValueError('Unexpected static asset path.')
                        path = destination / basename
                        descriptor_fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                        with os.fdopen(descriptor_fd, 'wb') as output:
                            output.write(archive.read(name))
                        if hashlib.sha256(path.read_bytes()).hexdigest() != descriptor['sha256']:
                            raise ValueError('Restored static asset checksum mismatch.')
                        files_restored += 1
                except Exception:
                    shutil.rmtree(destination)
                    raise
    return {'target_schema': target_schema, 'counts': counts, 'totals': manifest['totals'], 'verified': True,
            'snapshot_created_at': manifest['created_at'], 'source_revision': manifest['source_revision'],
            'identifier_sha256': identities, 'binary_files_verified': binary_count,
            'static_files_archived': sum(name.startswith('static/assets/') for name in manifest['files']),
            'static_files_restored': files_restored, 'static_restore_verified': destination is not None,
            'ephemeral_worker_observations_discarded': discarded_worker_pulses,
            'ephemeral_monitor_state_discarded': discarded_monitor_states,
            'ephemeral_map_runtime_discarded': discarded_map_runtime,
            'staff_geo_grants_revoked': revoked_geo_grants,
            'archive_sha256': hashlib.sha256(contents).hexdigest(), 'elapsed_seconds': round(time.monotonic() - started, 3)}


@router.get('/export')
def business_export(user=Depends(auth.require_roles('owner'))):
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        contents, manifest = build_archive(conn, brand_id=user['brand_id'], max_bytes=MAX_WEB_EXPORT)
    with db.tx() as conn:
        db.audit(conn, user['id'], 'business.export.generated', 'brand', user['brand_id'],
                 {'format': FORMAT, 'counts': manifest['counts'], 'sha256': hashlib.sha256(contents).hexdigest()})
    filename = 'kps-business-export-v2-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.zip'
    return Response(contents, media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="{filename}"', 'Cache-Control': 'no-store'})
