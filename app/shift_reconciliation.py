"""Read-only shared closure view. Accounting remains the canonical settlement engine.

Call only after authorizing the shift. Never rewrite a historical snapshot using
current order state, and never treat operational closure as financial approval.
"""
from .accounting import driver_settlement_values, variance_confirmation


def closed_shift(c, shift):
    if shift['status'] != 'closed':
        return None
    report = c.execute(
        'SELECT id,snapshot FROM closure_reports WHERE shift_id=%s AND brand_id=%s '
        'ORDER BY version DESC,created_at DESC LIMIT 1',
        (shift['id'], shift['brand_id']),
    ).fetchone()
    if not report:
        return {'state': 'missing_snapshot', 'shift_id': shift['id'], 'brand_id': shift['brand_id']}
    latest = c.execute(
        'SELECT * FROM driver_accounting_settlements WHERE report_id=%s AND brand_id=%s '
        'ORDER BY revision DESC LIMIT 1', (report['id'], shift['brand_id']),
    ).fetchone()
    if shift['kind'] == 'manager' and not latest:
        legacy = c.execute(
            "SELECT * FROM manager_financial_matches WHERE report_id=%s AND status='matched' ORDER BY revision DESC LIMIT 1",
            (report['id'],),
        ).fetchone()
        if legacy:
            latest = dict(revision=0, bank_halalas=legacy['bank_halalas'],
                          cash_due_halalas=legacy['expected_cash_halalas'], cash_received_halalas=legacy['cash_halalas'])
    snap = report['snapshot']
    values = driver_settlement_values(snap, latest)
    confirmation = variance_confirmation(c, report['id'], values['revision'])
    return dict(values, state='closed_snapshot', report_id=report['id'],
                shift_id=shift['id'], brand_id=shift['brand_id'],
                operational_date=shift.get('reporting_operational_date') or snap.get('operational_date'),
                original_operational_date=snap.get('operational_date'),
                summary=snap.get('summary', {}),
                external_enbishan_totals=snap.get('external_enbishan_totals', {}),
                variance_pending=bool(values['reconciliation_complete'] and values['net_difference_halalas'] and not confirmation))


def meaningful_reason(value):
    """Reject numeric/placeholding reasons without guessing responsibility."""
    reason = (value or '').strip()
    return len(reason) >= 5 and sum(ch.isalpha() for ch in reason) >= 3
