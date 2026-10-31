"""Explain canonical assignment guards without granting assignment eligibility."""
from fastapi import HTTPException
from . import db

def describe(c, row):
    if not row['active']: return 'inactive', 'owner_reactivate_employee', []
    if not row.get('shift_id'): return 'no_active_shift', 'open_driver_shift', []
    if row['shift_status'] != 'open': return 'closing_or_closed', 'complete_existing_closure', []
    s=c.execute('SELECT * FROM shifts WHERE id=%s', (row['shift_id'],)).fetchone()
    if (s.get('vehicle_log') or {}).get('awaiting_refuel'):
        pending=c.execute("SELECT 1 FROM fuel_exceptions WHERE shift_id=%s AND phase='activation' AND status='requested'", (s['id'],)).fetchone()
        return ('fuel_exception_pending', 'manager_review_fuel_exception', []) if pending else ('waiting_for_fuel', 'record_refuelling_or_request_exception', [])
    if c.execute("SELECT 1 FROM driver_breaks WHERE shift_id=%s AND status='active'", (s['id'],)).fetchone():
        return 'on_break', 'explicitly_end_break', []
    if not s.get('available', True): return 'manually_paused', 'manager_resume_availability', []
    if row['active_tasks']: return 'busy', 'complete_assigned_tasks', []
    from .operations import observe_driver_shift
    eligible=[]
    for bid in row['branch_ids']:
        candidate={'brand_id':s['brand_id'],'branch_id':bid}
        if (s.get('vehicle_log') or {}).get('receipt_trial'):
            candidate['snapshot']={'delivery_period':'morning'}
        try: observe_driver_shift(c,s,candidate)
        except HTTPException as exc:
            if exc.status_code not in (403,404,409): raise
        else: eligible.append(bid)
    if not eligible: return 'manager_or_scope_not_ready', 'restore_authorized_open_manager_shift', []
    return 'available', 'none', eligible


def live_status(row):
    """Three mutually exclusive staffing buckets; busy includes paused duty.

    Readiness remains separate: breaks, fuel checks and closing are on-duty
    unavailability, never evidence that a driver has no physical shift.
    """
    if not row.get('shift_id') or row.get('shift_status') == 'closed':
        return 'no_shift'
    return 'available' if row.get('availability') == 'available' else 'busy'


def live_counts(rows):
    counts = dict(total=0, available=0, busy=0, no_shift=0)
    for row in rows:
        counts['total'] += 1
        counts[row['live_status']] += 1
    return counts
