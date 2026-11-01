"""Explain canonical assignment guards without granting assignment eligibility."""
from copy import deepcopy
from fastapi import HTTPException
from . import db

class ReadinessObservation:
    """Reuse point reads only inside one roster's read-only SQL snapshot.

    No result outlives that request/transaction. This is deliberately not used
    by assignment, which retains fresh reads and its reservation/lock protocol.
    Exact SQL and scalar parameters are allowlisted; locks, business writes,
    aggregates and arbitrary SQL always go directly to the real connection.
    """
    _reads = frozenset({
        'SELECT * FROM shifts WHERE id=%s',
        'SELECT * FROM users WHERE id=%s',
        "SELECT 1 FROM driver_breaks WHERE shift_id=%s AND status='active'",
        'SELECT brand_id,branch_ids FROM users WHERE id=%s AND active',
        'SELECT m.branch_ids FROM staff_brand_memberships m '
        'JOIN brands b ON b.id=m.brand_id WHERE m.user_id=%s AND m.brand_id=%s AND m.active AND b.active',
        'SELECT brand_id FROM driver_brand_dedications WHERE driver_id=%s '
        'AND cancelled_at IS NULL AND starts_at<=now() AND ends_at>now()',
        'SELECT w.* FROM central_workdays w JOIN central_workday_legs l '
        'ON l.workday_id=w.id WHERE l.shift_id=%s',
    })

    class _PointResult:
        def __init__(self, row):
            self.row = row
            self.consumed = False

        def fetchone(self):
            if self.consumed:
                return None
            self.consumed = True
            return deepcopy(self.row)

        def fetchall(self):
            row = self.fetchone()
            return [] if row is None else [row]

    def __init__(self, connection):
        self._connection = connection
        self._rows = {}

    def execute(self, statement, params=()):
        if (not isinstance(statement, str) or statement not in self._reads
                or not isinstance(params, (tuple, list))
                or not all(value is None or isinstance(value, str) for value in params)):
            return self._connection.execute(statement, params)
        key = (statement, tuple(params))
        if key not in self._rows:
            row = self._connection.execute(statement, params).fetchone()
            # A large roster stays bounded; uncached results remain correct.
            if len(self._rows) >= 4096:
                return self._PointResult(row)
            self._rows[key] = deepcopy(row)
        return self._PointResult(self._rows[key])


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
