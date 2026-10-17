"""Read-only prelaunch inventory. This module deliberately cannot delete records."""
from datetime import datetime, timezone
from psycopg import sql
from .exports import _totals

PRESERVE = {'brands','branches','products','app_settings','users','fleet_vehicles',
            'audit_events','schema_migrations','order_daily_counters','order_branch_counters',
            'fiscal_documents','fiscal_policy_decisions','identity_channels',
            'branch_print_devices','driver_break_policies'}
REVIEW = {'orders','collections','refunds','shifts','custody_events','custody_transfers',
          'retained_carryovers','manager_handovers','vehicle_operating_logs','fleet_route_readings',
          'vehicle_log_corrections','conversations','chat_messages','chat_attachments',
          'customer_addresses','customer_identity_links','branch_payment_attempts',
          'branch_online_receipts','branch_online_refunds','branch_payment_webhooks'}


def classification(table):
    if table in PRESERVE:
        return 'PRESERVE'
    if table in REVIEW:
        return 'INDIVIDUAL_LEGAL_AND_TEST_REVIEW_REQUIRED'
    return 'UNCLASSIFIED_PRESERVE_UNTIL_REVIEWED'


def inventory(conn):
    """Caller must use a repeatable-read, read-only transaction on the actual source."""
    state = conn.execute('SHOW transaction_read_only').fetchone()
    if next(iter(state.values())) != 'on':
        raise ValueError('Read-only transaction required')
    tables = conn.execute("SELECT table_name FROM information_schema.tables "
                          "WHERE table_schema=current_schema() AND table_type='BASE TABLE' ORDER BY table_name").fetchall()
    counts = []
    for row in tables:
        name = row['table_name']
        count = conn.execute(sql.SQL('SELECT count(*) AS n FROM {}').format(sql.Identifier(name))).fetchone()['n']
        counts.append({'table': name, 'rows': count, 'disposition': classification(name), 'approved_delete_rows': 0})
    dependencies = conn.execute("SELECT tc.table_name,kcu.column_name,ccu.table_name AS referenced_table,"
        "ccu.column_name AS referenced_column FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.constraint_schema=kcu.constraint_schema "
        "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name=tc.constraint_name AND ccu.constraint_schema=tc.constraint_schema "
        "WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema=current_schema() "
        "ORDER BY tc.table_name,kcu.column_name").fetchall()
    # Preserve raw financial and invoice evidence; owner test declarations alone
    # are not evidence that an issued invoice or external payment is deletable.
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'mode':'DRY_RUN_ONLY',
        'source':conn.execute('SELECT current_database() AS database,current_schema() AS schema').fetchone(),
        'table_counts':counts,'foreign_keys':dependencies,'reset_authorized':False,
        'financial_totals':_totals(conn,None),
        'sequences':conn.execute('SELECT sequencename,last_value,increment_by FROM pg_sequences WHERE schemaname=current_schema() ORDER BY sequencename').fetchall(),
        'blockers':['current_full_encrypted_backup_required','isolated_restore_and_reconciliation_required',
                    'fiscal_and_external_payment_review_required','restricted_archive_and_retention_required',
                    'customer_order_quiescence_and_atomic_reset_plan_required'],
        'sequence_policy':'Preserve all invoice/order sequences and counters; never reuse issued references.',
        'odometer_policy':'Archive experimental logs; require verified actual first-production reading; never fabricate mileage.'}
