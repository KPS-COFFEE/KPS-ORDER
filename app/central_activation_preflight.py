"""Optional startup diagnostics using a private read-only connection.

No startup hooks, migration imports, network reconfiguration or feature activation
occur on import. Call run(enabled=True) explicitly after normal migrations.
"""
from __future__ import annotations

import os


def summarize(report):
    """A log-safe projection: no actor, shift, vehicle IDs or private row data."""
    return {'event': 'central_activation_preflight', 'status': 'completed',
            'mode': 'READ_ONLY_METADATA',
            'active_shift_count': report['active_shift_count'],
            'active_employee_count': report['active_employee_count'],
            'existing_grouped_workday_count': len(report['existing_grouped_workdays']),
            'legacy_adoption_candidate_count': len(report['legacy_adoption_candidates']),
            'legacy_manager_adoption_first_count': sum(bool(r['manager_adoption_first']) for r in report['legacy_adoption_candidates']),
            'conflict_count': len(report['conflicts']),
            'physical_vehicle_conflict_count': len(report['physical_vehicle_conflicts']),
            'metadata_preflight_clear': report['metadata_preflight_clear'],
            'activation_approved': False}


def run(*, enabled=False):
    """Return log-safe results when requested; failures never reveal exceptions.

    This helper deliberately cannot authorize or enable the central feature.
    It is intended for a dormant production deployment before activation.
    """
    if not enabled:
        return None
    if os.getenv('KPS_CENTRAL_OPERATIONS_ENABLED') == 'true':
        return {'event': 'central_activation_preflight', 'status': 'skipped_feature_active',
                'activation_approved': False}
    try:
        # Standalone tool imports only standard library and independent safety.
        from tools import central_activation_preflight as evidence
        url = evidence._safety.validate_target(os.environ.get('DATABASE_URL', ''))
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(url, autocommit=True, row_factory=dict_row, connect_timeout=5,
                             options='-c default_transaction_read_only=on -c search_path=public',
                             application_name='kps_central_startup_readonly_preflight') as conn:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                conn.execute("SET LOCAL statement_timeout='2s'")
                conn.execute("SET LOCAL lock_timeout='250ms'")
                conn.execute("SET LOCAL idle_in_transaction_session_timeout='5s'")
                conn.execute("SET LOCAL transaction_timeout='15s'")
                return summarize(evidence.collect(conn))
    except Exception:
        return {'event': 'central_activation_preflight', 'status': 'failed',
                'metadata_preflight_clear': False, 'activation_approved': False}
