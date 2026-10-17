"""Read-only prelaunch inventory. This module deliberately cannot delete records."""
from datetime import datetime, timezone
import hashlib
import json
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


def fiscal_retention_tables(dependencies):
    """Conservative table closure, never a row-level DELETE authorization.

    Stop expansion at configuration/identity tables: all of their rows are
    already preserved, so a shared branch or employee cannot imply deleting
    unrelated invoice history. Unknown connected tables remain retained.
    """
    retained = {'fiscal_documents', 'fiscal_policy_decisions'}
    pending = list(retained)
    while pending:
        table = pending.pop()
        for edge in dependencies:
            child, parent = edge['table_name'], edge['referenced_table']
            adjacent = parent if child == table else child if parent == table else None
            if adjacent is None or adjacent in retained:
                continue
            retained.add(adjacent)
            if adjacent not in PRESERVE:
                pending.append(adjacent)
    return sorted(retained)


def _fiscal_fingerprint(conn):
    digest = hashlib.sha256()
    rows = conn.execute('SELECT to_jsonb(f) AS row FROM fiscal_documents f ORDER BY number')
    counts = {'invoice': 0, 'credit_note': 0}
    for result in rows:
        row = result['row']
        counts[row['kind']] += 1
        digest.update(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':')).encode('utf-8') + b'\n')
    return {'counts': counts, 'sha256': digest.hexdigest(),
            'scope': 'all fiscal_documents columns including immutable document JSON',
            'proof': 'snapshot fingerprint only; does not establish backup or restoration'}


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
    # pg_constraint ordinality preserves composite FK column pairing; the
    # information_schema cross join can produce incorrect composite edges.
    dependencies = conn.execute("SELECT child.relname AS table_name,ca.attname AS column_name,"
        "parent.relname AS referenced_table,pa.attname AS referenced_column,co.conname AS constraint_name "
        "FROM pg_constraint co JOIN pg_class child ON child.oid=co.conrelid "
        "JOIN pg_namespace ns ON ns.oid=child.relnamespace "
        "JOIN pg_class parent ON parent.oid=co.confrelid "
        "JOIN LATERAL unnest(co.conkey) WITH ORDINALITY ck(attnum,ord) ON true "
        "JOIN LATERAL unnest(co.confkey) WITH ORDINALITY pk(attnum,ord) ON pk.ord=ck.ord "
        "JOIN pg_attribute ca ON ca.attrelid=child.oid AND ca.attnum=ck.attnum "
        "JOIN pg_attribute pa ON pa.attrelid=parent.oid AND pa.attnum=pk.attnum "
        "WHERE co.contype='f' AND ns.nspname=current_schema() "
        "ORDER BY child.relname,co.conname,ck.ord").fetchall()
    retained = set(fiscal_retention_tables(dependencies))
    for item in counts:
        if item['table'] in retained and item['disposition'] != 'PRESERVE':
            item['disposition'] = 'RETAIN_ALL_ROWS_PENDING_FISCAL_RELATION_REVIEW'
    # Preserve raw financial and invoice evidence; owner test declarations alone
    # are not evidence that an issued invoice or external payment is deletable.
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'mode':'DRY_RUN_ONLY',
        'source':conn.execute('SELECT current_database() AS database,current_schema() AS schema').fetchone(),
        'table_counts':counts,'foreign_keys':dependencies,'reset_authorized':False,
        'fiscal_evidence':_fiscal_fingerprint(conn),'fiscal_retention_tables':sorted(retained),
        'cleanup_executable':False,'cleanup_rehearsed':False,
        'retention_scope':'Conservative whole-table retention; row-level cleanup still requires actual restored-source rehearsal.',
        'financial_totals':_totals(conn,None),
        'sequences':conn.execute('SELECT sequencename,last_value,increment_by FROM pg_sequences WHERE schemaname=current_schema() ORDER BY sequencename').fetchall(),
        'blockers':['current_full_encrypted_backup_required','isolated_restore_and_reconciliation_required',
                    'fiscal_and_external_payment_review_required','restricted_archive_and_retention_required',
                    'customer_order_quiescence_and_atomic_reset_plan_required'],
        'sequence_policy':'Preserve all invoice/order sequences and counters; never reuse issued references.',
        'odometer_policy':'Archive experimental logs; require verified actual first-production reading; never fabricate mileage.'}
