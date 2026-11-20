"""A new manager day never closes unfinished operational or financial work."""
from datetime import date, datetime

from . import db, service_hours


def stored_day(shift):
    value = shift.get('delivery_operational_date')
    if value:
        return date.fromisoformat(value) if isinstance(value, str) else value
    opened = shift['opened_at']
    if isinstance(opened, str):
        opened = datetime.fromisoformat(opened)
    return service_hours.delivery_operational_date(opened)


def require_current(manager, *, at=None):
    if (manager.get('manager_admission_ended_at')
            or manager.get('staffing_ended_at')
            or stored_day(manager) != service_hours.delivery_operational_date(at)):
        raise db.error(409, 'MANAGER_NEW_DAY_REQUIRED',
                       'افتح شفت مدير جديد لهذا اليوم؛ تظل الطلبات والتقفيلات القديمة متاحة للاستكمال.')


def accepts_staff(manager):
    return (manager['status'] == 'open' and not manager.get('manager_admission_ended_at')
            and not manager.get('staffing_ended_at')
            and stored_day(manager) == service_hours.delivery_operational_date())


def retire_previous_days(c, user, target, *, at=None):
    """Caller holds the staff advisory lock; failed openings roll this back too.

    Do not change status, amounts, child attribution or physical receipt markers.
    A genuine same-day duplicate remains blocked by both the API and its index.
    """
    now = at or service_hours.now_riyadh()
    today = service_hours.delivery_operational_date(now)
    rows = c.execute("SELECT * FROM shifts WHERE user_id=%s AND brand_id=%s AND kind='manager' "
                     "AND status<>'closed' AND manager_admission_ended_at IS NULL "
                     "AND to_jsonb(shifts)->>'staffing_ended_at' IS NULL "
                     "ORDER BY id FOR UPDATE", (target['id'], target['brand_id'])).fetchall()
    for old in rows:
        if stored_day(old) >= today:
            continue
        c.execute('UPDATE shifts SET manager_admission_ended_at=%s WHERE id=%s', (now, old['id']))
        db.audit(c, user['id'], 'manager.staffing_admission_ended', 'shift', old['id'],
                 {'previous_day': stored_day(old).isoformat(), 'new_day': today.isoformat(),
                  'financial_changes': False, 'unfinished_work_preserved': True})


def prior_day_filter(alias, kind):
    """Keep yesterday's manager work out of the active admission lock only."""
    if kind != 'manager':
        return '', ()
    if alias not in {'s', 'shifts'}:
        raise ValueError('Unknown shift alias')
    return (f" AND ({alias}.kind<>'manager' OR COALESCE({alias}.delivery_operational_date, "
            f"(({alias}.opened_at AT TIME ZONE 'Asia/Riyadh')-interval '4 hours')::date)>=%s)",
            (service_hours.delivery_operational_date(),))
