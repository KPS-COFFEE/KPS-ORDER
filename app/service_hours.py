"""Riyadh delivery windows shared by availability, staffing and reporting.

Only new delivery admission and fresh shifts use the clock. Existing tasks,
collections, custody transfers and closures remain independent of the window.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

RIYADH = ZoneInfo('Asia/Riyadh')
DELIVERY_DAY_START_MINUTES = 16 * 60
DEFAULT_DELIVERY_HOURS = [{'days': list(range(7)), 'start': '16:00', 'end': '04:00'}]
# KPS branch open every day, except Friday 05:30–12:30 Asia/Riyadh.
# Monday=0, Friday=4; delivery service hours and enablement remain independent.
DEFAULT_KPS_BRANCH_OPENING_HOURS = [
    {'days': [0, 1, 2, 3, 5, 6], 'start': '00:00', 'end': '00:00'},
    {'days': [4], 'start': '00:00', 'end': '05:30'},
    {'days': [4], 'start': '12:30', 'end': '00:00'},
]
DELIVERY_HOURS_DECISION = 'owner-delivery-hours-2026-10-10'


def now_riyadh():
    return datetime.now(RIYADH)


def local_time(value=None):
    value = value or now_riyadh()
    return value.replace(tzinfo=RIYADH) if value.tzinfo is None else value.astimezone(RIYADH)


def delivery_operational_date(value=None):
    """After midnight still belongs to the delivery day beginning at 16:00."""
    return (local_time(value) - timedelta(minutes=DELIVERY_DAY_START_MINUTES)).date()


def _minute(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise ValueError('Invalid configured time')
    hours, minutes = map(int, value.split(':'))
    return hours * 60 + minutes


def _rows(hours):
    for row in hours if isinstance(hours, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get('days'), list):
            continue
        if 'enabled' in row and row['enabled'] is not True:
            continue
        try:
            start, end = _minute(row['start']), _minute(row['end'])
        except (KeyError, TypeError, ValueError):
            continue
        days = [day for day in row['days'] if isinstance(day, int) and not isinstance(day, bool) and 0 <= day <= 6]
        if days:
            yield days, start, end


def hours_status(hours, now=None):
    """Return exact half-open windows and the next scheduled opening, not an ETA."""
    now = local_time(now)
    rows = list(_rows(hours))
    midnight = datetime.combine(now.date(), datetime.min.time(), RIYADH)
    current, next_open = [], []
    # Yesterday handles overnight carry-in; eight future days cover weekly rows.
    for offset in range(-1, 9):
        day = midnight + timedelta(days=offset)
        for days, start, end in rows:
            if day.weekday() not in days:
                continue
            # Equal endpoints preserve the catalog's configured calendar day.
            window_start = day if start == end else day + timedelta(minutes=start)
            window_end = day + timedelta(days=1) if start == end else day + timedelta(minutes=end) + (timedelta(days=1) if start > end else timedelta())
            if window_start <= now < window_end:
                current.append((window_start, window_end))
            if window_start > now:
                next_open.append(window_start)
    active = min(current) if current else None
    reopening = min(next_open) if next_open else None
    return {'configured': bool(rows), 'open': bool(active), 'timezone': 'Asia/Riyadh',
            'next_open_at': reopening.isoformat() if reopening else None,
            'next_open_local': reopening.strftime('%Y-%m-%d %H:%M') if reopening else None,
            'current_window_start': active[0].isoformat() if active else None,
            'current_window_end': active[1].isoformat() if active else None,
            'operational_date': delivery_operational_date(now).isoformat()}


def hours_open(hours, now=None):
    return hours_status(hours, now)['open']


def manager_shift_status(hours, now=None):
    """Allow manager preparation at 15:45 for the upcoming 16:00 window.

    Customer admission and driver shifts keep hours_status unchanged. The
    snapshot uses the upcoming service window and its operational date.
    Custom opening times and explicitly closed days are not widened.
    """
    now = local_time(now)
    status = hours_status(hours, now)
    if status['open'] or not status['next_open_at']:
        return status
    opening = datetime.fromisoformat(status['next_open_at'])
    if (opening.hour == 16 and opening.minute == 0
            and opening - timedelta(minutes=15) <= now < opening):
        status = hours_status(hours, opening)
        status['preparation'] = True
        status['preparation_start'] = (opening - timedelta(minutes=15)).isoformat()
    return status


def closed_delivery_message(hours, now=None):
    now = local_time(now)
    state = hours_status(hours, now)
    if not state['configured']:
        return 'ساعات التوصيل لهذا الفرع غير مهيأة.'
    if not state['next_open_at']:
        return 'التوصيل مغلق الآن؛ راجع أوقات الخدمة لهذا الفرع.'
    reopening = datetime.fromisoformat(state['next_open_at'])
    if reopening.date() == now.date() and reopening.hour == 16 and reopening.minute == 0:
        return 'التوصيل متوقف الآن. يبدأ استقبال الطلبات الساعة 4 مساءً بتوقيت السعودية.'
    day = 'اليوم' if reopening.date() == now.date() else 'غدًا' if reopening.date() == now.date() + timedelta(days=1) else 'بتاريخ ' + reopening.strftime('%Y-%m-%d')
    clock = f'{reopening.hour % 12 or 12}:{reopening.minute:02d}'
    period = 'مساءً' if reopening.hour >= 12 else 'صباحًا'
    return f'التوصيل مغلق الآن، ويعود {day} الساعة {clock} {period} بتوقيت السعودية.'



def apply_default_branch_hours(conn):
    """Apply approved Friday closing once, without overwriting canonical seed or owner edits."""
    from . import db
    rows=conn.execute("SELECT * FROM branches WHERE brand_id='kps' AND branch_no BETWEEN 1 AND 20 ORDER BY branch_no FOR UPDATE").fetchall()
    for branch in rows:
        number=branch['branch_no']
        if (branch['id']!=f'kps-branch-{number}' or
                branch['source_id']!=f'apex-canonical-branch-no:{number}'):
            continue
        decision=conn.execute(
            "INSERT INTO branch_opening_hours_decisions(branch_id,decision_id,result) "
            "VALUES(%s,%s,'evaluating') ON CONFLICT(branch_id) DO NOTHING RETURNING branch_id",
            (branch['id'],'kps-owner-friday-closed-2026-10-10')
        ).fetchone()
        if not decision:
            continue
        result='preserved_existing_owner_hours'
        if branch['settings'].get('opening_hours') is None:
            conn.execute("UPDATE branches SET settings=jsonb_set(settings,'{opening_hours}',%s::jsonb),updated_at=now() WHERE id=%s",
                         (db.json(DEFAULT_KPS_BRANCH_OPENING_HOURS),branch['id']))
            db.audit(conn,None,'branch.owner_opening_hours','branch',branch['id'],
                     {'owner_approved':True,'branch_no':number,'timezone':'Asia/Riyadh',
                      'friday_closed':'05:30-12:30','other_days':'24h',
                      'service_enablement_unchanged':True})
            result='applied_24h_except_friday'
        conn.execute('UPDATE branch_opening_hours_decisions SET result=%s,evaluated_at=now() WHERE branch_id=%s',
                     (result,branch['id']))


def apply_default_delivery_hours(conn):
    """Evaluate the owner's approved default once per existing KPS branch.

    Call after the initial catalog import. A recorded evaluation prevents later
    restarts from undoing custom schedules or an owner's explicit empty hours.
    Flags, locations, fees, coverage and other service hours are never changed.
    """
    from . import catalog, db
    rows = conn.execute("SELECT * FROM branches WHERE brand_id='kps' ORDER BY id FOR UPDATE").fetchall()
    for branch in rows:
        if catalog.normalize_city(branch['city']) not in catalog.DELIVERY_CITIES:
            continue
        decision = conn.execute('INSERT INTO delivery_hours_decisions(decision_id,branch_id,result) '
                                "VALUES(%s,%s,'evaluating') ON CONFLICT(branch_id) DO NOTHING RETURNING branch_id",
                                (DELIVERY_HOURS_DECISION, branch['id'])).fetchone()
        if not decision:
            continue
        result = 'preserved_owner_hours'
        if not branch['settings'].get('delivery_hours'):
            conn.execute("UPDATE branches SET settings=jsonb_set(settings,'{delivery_hours}',%s::jsonb),updated_at=now() WHERE id=%s",
                         (db.json(DEFAULT_DELIVERY_HOURS), branch['id']))
            db.audit(conn, None, 'branch.owner_delivery_hours', 'branch', branch['id'],
                     {'decision_id': DELIVERY_HOURS_DECISION, 'hours': DEFAULT_DELIVERY_HOURS,
                      'timezone': 'Asia/Riyadh', 'service_enablement_unchanged': True})
            result = 'applied_16_to_04'
        conn.execute('UPDATE delivery_hours_decisions SET result=%s,evaluated_at=now() WHERE branch_id=%s',
                     (result, branch['id']))
