"""Read-only operational summaries; accounting status comes from its canonical ledger.

These are presentations, never new closure snapshots or accounting approvals.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Response
from . import auth, db, multibrand, operations as ops, service_hours
from .employee_permissions import reports_supervisor
from .journey_timing import stamp
from .report_filenames import dated_filename, disposition as report_disposition

router = APIRouter(prefix='/api/operating-reports', tags=['operating-reports'])
TZ = ZoneInfo('Asia/Riyadh')
DAYS = ('الاثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد')
BRANDS = {'kps': 'كبز', 'enbishan': 'عنبيشن'}


def filename(day, scope):
    day = date.fromisoformat(str(day))
    return dated_filename(day, kind='تشغيل', padded=True)


def disposition(name):
    return report_disposition(name)


def allowed_manager(user, shift, scope):
    if shift['brand_id'] != user['brand_id']:
        return False
    if not ops.unrestricted_owner(user) and (not shift['branch_ids'] or not set(shift['branch_ids']) <= set(user.get('branch_ids', []))):
        return False
    if scope == 'shift' and user['role'] != 'owner' and shift['user_id'] != user['id']:
        if not reports_supervisor(user) or shift.get('status') != 'closed':
            return False
    from .morning_manager_context import shift_allowed
    return shift_allowed(user, shift)


def progress(entries, approvals, managers, pending_managers=0):
    """Balance + cash + explicit variance review; final day approval is separate.

100% means the actual day is approved, not simply that the papers were reviewed.
"""
    steps = done = overdue = late = 0
    for entry in entries:
        variance = bool(entry.get('reconciliation_complete') and entry.get('net_difference_halalas'))
        steps += 2 + int(variance)
        done += int(bool(entry.get('balance_approved'))) + int(bool(entry.get('cash_recorded')))
        done += int(variance and bool(entry.get('variance_confirmation')))
        complete = entry.get('reconciliation_complete') and not entry.get('variance_pending')
        if entry.get('overdue'):
            if complete:
                late += 1
            else:
                overdue += 1
    steps += len(managers)
    approved_ids = {a['manager_shift_id'] for a in approvals}
    done += len(set(managers) & approved_ids)
    complete = bool(managers) and not pending_managers and set(managers) <= approved_ids and done == steps
    return dict(percent=100 if complete else min(99, int(done * 100 / steps)) if steps else 0,
                completed_steps=done, total_steps=steps, overdue=overdue, late=late,
                approved=complete, approvals=approvals if complete else [])


def snapshot(c, shift):
    saved = c.execute('SELECT id,snapshot FROM closure_reports WHERE shift_id=%s AND brand_id=%s ORDER BY version DESC,created_at DESC LIMIT 1',
                      (shift['id'], shift['brand_id'])).fetchone()
    if saved:
        return saved['snapshot'], saved['id']
    if shift['status'] == 'closed':
        # A missing immutable snapshot is missing evidence, never permission to
        # reconstruct historical sales from today's mutable orders.
        return dict(shift=shift,orders=[],refunds=[],external_enbishan=[],evidence_missing=True), None
    # Active/submitted shifts use the same effective attribution views. A GET
    # never creates a closure and never writes a provisional accounting record.
    orders = c.execute('SELECT DISTINCT ON (o.id) o.*,t.status task_status,t.delivered_at task_delivered_at FROM effective_driver_tasks t JOIN orders o ON o.id=t.order_id '
                       "WHERE t.shift_id=%s AND t.status NOT IN ('transferred','reassigned') ORDER BY o.id,t.created_at DESC,t.id DESC",
                       (shift['id'],)).fetchall()
    attested = {r['order_id'] for r in c.execute('SELECT order_id FROM legacy_delivery_attestations WHERE shift_id=%s UNION SELECT order_id FROM legacy_invoice_imports WHERE ledger_shift_id=%s', (shift['id'],shift['id'])).fetchall()}
    for order in orders:
        order['fulfilled_in_report'] = order['task_status'] == 'delivered' and bool(order.get('task_delivered_at') or order['id'] in attested) and not order.get('test_void')
    refunds = c.execute('SELECT * FROM refunds WHERE shift_id=%s', (shift['id'],)).fetchall()
    collections = c.execute('SELECT * FROM effective_collections WHERE shift_id=%s', (shift['id'],)).fetchall()
    return dict(shift=shift, orders=orders, collections=collections, refunds=refunds, external_enbishan=[shift['external_enbishan']] if shift.get('external_enbishan') else []), None


def project_driver(shift, snap):
    """No repricing, no double deduction of complimentary goods or refunds."""
    brand = 'enbishan' if shift['brand_id'] == 'enbishan' else 'kps'
    goods = {key: dict(count=0, amount=0) for key in BRANDS}
    cases = []
    refunds = [r for r in snap.get('refunds', []) if r.get('record_kind') != 'test_void']
    seen = set()
    for order in snap.get('orders', []):
        if order['id'] in seen:
            continue
        seen.add(order['id'])
        free = (order.get('snapshot') or {}).get('complimentary') or {}
        complete = order.get('fulfilled_in_report', False)
        if complete:
            goods[brand]['count'] += 1
            goods[brand]['amount'] += order.get('total_halalas', 0)
        if complete and free:
            from .complimentary import accounting_values
            values = accounting_values(order)
            cases.append(dict(brand=brand, kind=values['kind'], amount=values['amount'] if type(values['amount']) is int else None,
                              reference=str(order.get('order_no') or order['id']), reason=values['reason'],
                              approval='معتمدة تشغيليًا' if values.get('authorized_by') else 'اعتماد غير موثق'))
        elif order.get('delivery_status') == 'cancelled' or order.get('preparation_status') in ('cancelled', 'rejected'):
            cases.append(dict(brand=brand, kind='إلغاء', amount=order.get('total_halalas'),
                              reference=str(order.get('order_no') or order['id']), reason='راجع سجل الطلب', approval='مسجل'))
    for refund in refunds:
        cases.append(dict(brand=brand, kind='مرتجع', amount=refund['amount_halalas'],
                          reference=str(refund.get('order_id') or ''), reason=refund.get('reason') or 'غير مسجل', approval='مسجل'))
    for external in snap.get('external_enbishan', []):
        goods['enbishan']['count'] += external.get('order_count', 0)
        goods['enbishan']['amount'] += external.get('total_halalas', 0)
    net = sum(v['amount'] for v in goods.values()) - sum(r['amount_halalas'] for r in refunds)
    return dict(brands=goods, net=net, cases=cases)


def collection_channels(snap):
    """Recorded order collections only; never cash handover or accountant receipts."""
    if snap.get('evidence_missing') or 'collections' not in snap:
        return None, None
    eligible = {o['id'] for o in snap.get('orders', []) if o.get('fulfilled_in_report') and not o.get('test_void')}
    channels = {'card': 0, 'cash': 0}
    seen = set()
    for record in snap['collections']:
        if record.get('order_id') not in eligible or record.get('record_kind') == 'test_void':
            continue
        identity = record.get('id')
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        if record.get('method') in channels:
            channels[record['method']] += record['amount_halalas']
    for record in snap.get('refunds', []):
        if record.get('record_kind') != 'test_void' and record.get('method') in channels:
            channels[record['method']] -= record['amount_halalas']
    for external in snap.get('external_enbishan', []):
        if not external or not external.get('total_halalas'):
            continue
        if any(external.get(k) is None for k in ('card_halalas', 'cash_halalas')):
            return None, None
        channels['card'] += external['card_halalas']
        channels['cash'] += external['cash_halalas']
    return channels['card'], channels['cash']


def matching(row):
    if row.get('network') is None or row.get('cash') is None:
        return dict(shortage=None, surplus=None, status='بانتظار البيانات')
    delta = row['network'] + row['cash'] - row['net']
    return dict(shortage=max(0, -delta), surplus=max(0, delta),
                status='عجز' if delta < 0 else 'فائض' if delta > 0 else 'متوافق')


def complete_sum(rows, field):
    return sum(r[field] for r in rows) if rows and all(r.get(field) is not None for r in rows) else None


def task_intervals(c, where, args, cutoff):
    # Physical tasks, not the accounting reassignment overlay. A handover may
    # copy the pickup time to the new task, so its custody starts at creation.
    return c.execute(
        "SELECT t.id,t.order_id,t.driver_id,u.display_name,t.created_at,t.picked_up_at,t.status,s.closed_at shift_closed_at, "
        "CASE WHEN t.status='delivered' THEN COALESCE(t.delivered_at,e.ended_at) "
        "WHEN t.status IN ('transferred','reassigned','cancelled') THEN e.ended_at END ended_at "
        "FROM driver_tasks t JOIN shifts s ON s.id=t.shift_id JOIN orders o ON o.id=t.order_id JOIN users u ON u.id=t.driver_id "
        "LEFT JOIN LATERAL (SELECT min(e.created_at) ended_at FROM order_events e WHERE e.order_id=t.order_id "
        "AND ((e.event_type IN ('task.cancelled','task.delivered') AND e.details->>'task_id'=t.id) "
        "OR (e.event_type IN ('task.reassigned','task.handover.accepted') AND e.details->>'previous_task_id'=t.id))) e ON true "
        "WHERE t.created_at<=%s AND " + where + " ORDER BY t.created_at,t.id",
        (cutoff, *args)).fetchall()


def active_at(task, instant):
    if task['created_at'] > instant:
        return False
    if task['ended_at'] is not None:
        return instant < task['ended_at']
    if task['status'] in ('delivered', 'transferred', 'reassigned', 'cancelled'):
        return False if task['shift_closed_at'] and task['shift_closed_at'] <= instant else None
    return True


def add_delay_workload(c, workload_scope, morning, delayed):
    if not delayed:
        return
    cutoff = max(row['delay_at'] for row in delayed)
    tasks = defaultdict(list)
    for task in task_intervals(c, 't.order_id=ANY(%s)', ([row['id'] for row in delayed],), cutoff):
        tasks[task['order_id']].append(task)
    observed = []
    for row in delayed:
        instant = row['delay_at']
        history = tasks[row['id']]
        # The latest actual assignment before the instant supersedes earlier
        # assignments. An incomplete final transition is reported as unknown.
        task = next((t for t in reversed(history) if t['created_at'] <= instant), None)
        active = active_at(task, instant) if task else False
        row['workload'] = {'state': 'unknown' if active is None else 'unassigned'}
        if active:
            observed.append((row, task))
    if not observed:
        return
    by_driver = defaultdict(list)
    ids = sorted({task['driver_id'] for _, task in observed})
    scope_filter = (
        "t.driver_id=ANY(%s) AND o.test_void IS NULL "
        "AND EXISTS (SELECT 1 FROM jsonb_to_recordset(%s::jsonb) "
        "AS a(brand_id text, branch_ids jsonb, unrestricted boolean) "
        "WHERE a.brand_id=o.brand_id AND (a.unrestricted OR a.branch_ids ? o.branch_id)) "
        "AND (NOT %s OR o.snapshot->>'delivery_period'='morning')")
    for task in task_intervals(c, scope_filter, (ids, db.json(workload_scope), morning), cutoff):
        by_driver[task['driver_id']].append(task)
    for row, assigned in observed:
        instant = row['delay_at']
        active_orders, carried = set(), set()
        uncertain = set()
        for task in by_driver[assigned['driver_id']]:
            active = active_at(task, instant)
            if active is None:
                uncertain.add(task['order_id'])
            elif active:
                active_orders.add(task['order_id'])
                if task['picked_up_at'] and task['picked_up_at'] <= instant:
                    carried.add(task['order_id'])
        known = not (uncertain - active_orders)
        row['workload'] = dict(state='assigned' if known else 'unknown',
                               driver=assigned['display_name'], assigned=len(active_orders) if known else None,
                               onboard=len(carried) if known else None)


def delivery_orders(c, principal, candidates, visible, locations, day, now, workload_scope, *, details):
    from .performance import enrich_orders
    from .reporting import display_order_label

    branches = sorted({branch for shift in visible for branch in shift['branch_ids']})
    visible_ids = {shift['id'] for shift in visible}
    if not branches:
        return []
    orders = c.execute(
        "SELECT o.*, COALESCE(o.snapshot->>'manager_shift_id', accepted.manager_id, first_task.manager_id) timing_shift_id "
        "FROM orders o "
        "LEFT JOIN LATERAL (SELECT e.details->>'manager_shift_id' manager_id FROM order_events e "
        "WHERE e.order_id=o.id AND e.event_type='preparation.accepted' AND e.details->>'manager_shift_id' IS NOT NULL "
        "ORDER BY e.created_at,e.id LIMIT 1) accepted ON true "
        "LEFT JOIN LATERAL (SELECT s.manager_shift_id manager_id FROM driver_tasks t JOIN shifts s ON s.id=t.shift_id "
        "WHERE t.order_id=o.id AND s.brand_id=o.brand_id ORDER BY t.created_at,t.id LIMIT 1) first_task ON true "
        "WHERE o.brand_id=%s AND o.branch_id=ANY(%s) AND o.service='delivery' AND o.test_void IS NULL "
        "AND (o.delivery_operational_date BETWEEN %s::date-1 AND %s::date "
        "OR (o.delivery_operational_date IS NULL AND o.created_at >= (%s::date-1)::timestamp AT TIME ZONE 'Asia/Riyadh' "
        "AND o.created_at < ((%s::date+1)::timestamp + interval '4 hours') AT TIME ZONE 'Asia/Riyadh')) ORDER BY o.created_at,o.id",
        (principal['brand_id'], branches, day, day, day, day)).fetchall()

    scoped = []
    for order in orders:
        shift_id = order['timing_shift_id']
        period = (order.get('snapshot') or {}).get('delivery_period') or 'evening'
        if principal.get('_morning_manager_context') and period != 'morning':
            continue
        if shift_id is None:
            order_day = (order['created_at'].astimezone(TZ).date() if period == 'morning' else
                         order.get('delivery_operational_date') or service_hours.delivery_operational_date(order['created_at']))
            if order_day != day:
                continue
            matches = [shift for shift in candidates if order['branch_id'] in shift['branch_ids']
                       and (shift.get('delivery_window_snapshot') or {}).get('period', 'evening') == period
                       and shift['opened_at'] <= order['created_at']
                       and (not (end := shift.get('staffing_ended_at') or shift.get('closed_at'))
                            or order['created_at'] < end)]
            if len(matches) == 1:
                shift_id = matches[0]['id']
        if shift_id in visible_ids:
            scoped.append(order)
    output = []
    for offset in range(0, len(scoped), 250):
        for order in enrich_orders(c, scoped[offset:offset + 250], now):
            snap = order.get('snapshot') or {}
            city = snap.get('branch_city') or locations[order['branch_id']]['city'] or 'غير مسجلة'
            city = {'حفرالباطن': 'حفر الباطن', 'قبه': 'قبة'}.get(''.join(city.split()), city)
            journey = order['journey_timing']
            stage = order['stage_timing']
            timing = order['timings']['total']
            elapsed = journey['elapsed_seconds'] if journey else timing['seconds']
            limit = journey['limit_seconds'] if journey else (stage or {}).get('total_limit_seconds')
            if ''.join(city.split()) == 'القيصومة':
                limit = 27 * 60
            cancelled = timing['state'] == 'cancelled'
            delivered = order['delivery_status'] == 'delivered'
            if cancelled:
                status = 'cancelled'
            elif limit is None or elapsed is None or (not delivered and timing['state'] != 'running'):
                status = 'unavailable'
            elif delivered:
                status = 'late' if elapsed > limit else 'on_time'
            else:
                status = 'open_late' if elapsed > limit else 'open'
            phases = stage['phases'] if stage else {}
            pickup = next((step for step in (journey or {}).get('stages', []) if step['stage'] == 'pickup'), {})
            flags = order['delivery_timing_flags']
            delay_at = None
            if status in ('late', 'open_late'):
                delay_at = order['created_at'] + timedelta(seconds=limit)
            elif not cancelled:
                deadlines = [
                    stamp(phases.get('preparation', {}).get('red_at')) if flags['preparation'] else None,
                    stamp(pickup.get('deadline_at')) if (pickup.get('late_seconds') or 0) > 0 else None,
                    stamp(order.get('delivery_road_deadline_at') or phases.get('delivery', {}).get('red_at')) if flags['delivery'] else None,
                ]
                delay_at = min((value for value in deadlines if value is not None), default=None)
            output.append(dict(id=order['id'], delay_at=delay_at, city=city, limit_seconds=limit,
                               status=status, elapsed_seconds=elapsed,
                               reference=display_order_label(order), brand=order['brand_id'],
                               branch=locations[order['branch_id']]['name'], driver=order.get('driver_name'),
                               preparation_seconds=phases.get('preparation', {}).get('seconds', order['timings']['preparation']['seconds']),
                               delivery_seconds=order['timings']['delivery']['seconds'],
                               preparation_late=flags['preparation'], delivery_late=flags['delivery'],
                               pickup_late=not cancelled and (pickup.get('late_seconds') or 0) > 0))
    delayed = [order for order in output if order['delay_at'] is not None] if details else []
    for offset in range(0, len(delayed), 250):
        add_delay_workload(c, workload_scope, bool(principal.get('_morning_manager_context')),
                           delayed[offset:offset + 250])
    for order in output:
        del order['id']
    return output


def delivery_timing_summary(orders, *, details=True):
    groups = {}
    for order in orders:
        city = order['city']
        group = groups.setdefault(city, dict(city=city, limits=[], on_time=0, late=0, open_late=0,
                                            open=0, cancelled=0, unavailable=0, delayed_orders=[]))
        limit = order['limit_seconds']
        if limit is not None and limit not in group['limits']:
            group['limits'].append(limit)
        group[order['status']] += 1
        if details and (order['status'] in ('late', 'open_late') or (
                order['status'] != 'cancelled' and any(order[k] for k in ('preparation_late', 'pickup_late', 'delivery_late')))):
            group['delayed_orders'].append(order)
    for group in groups.values():
        rated = group['on_time'] + group['late']
        group['compliance'] = round(100 * group['on_time'] / rated, 1) if rated else None
        group['limits'].sort()
    return sorted(groups.values(), key=lambda group: group['city'])


def can_view_delay_details(user):
    # The owner assigned this report to the general manager. His stable
    # identity is the same one used by the manager notification directive.
    return (not user.get('_morning_manager_context') and auth.has_permission(user, 'reports_view')
            and (user['role'] == 'owner' or
                 (user['role'] == 'delivery_manager'
                  and user['id'] == 'staff_a9c2cb536601498aa89edc6b9fc6fef8')))


def build(c, user, day, scope, manager_shift_id=None, *, delay_details=False):
    ops.roles(user, 'owner', 'delivery_manager')
    if not auth.has_permission(user, 'reports_view'):
        raise db.error(403, 'OPERATING_REPORT_FORBIDDEN', 'صلاحية التقارير غير متاحة.')
    if delay_details and not can_view_delay_details(user):
        raise db.error(403, 'DELIVERY_DELAY_REPORT_FORBIDDEN', 'تفاصيل التأخير متاحة للمالك والمدير العام.')
    if scope == 'day' and user.get('_morning_manager_context'):
        raise db.error(403, 'OPERATING_REPORT_MORNING', 'تقرير الصباح يخص مناوبتك فقط.')
    principals = [user]
    if multibrand.enabled() and not user.get('_morning_manager_context'):
        principals = [p for b in multibrand.available(c, user) if (p := multibrand.project(c, user, b['id'])) and auth.has_permission(p, 'reports_view')]
    selected_manager_ids = {manager_shift_id} if scope == 'shift' and manager_shift_id else None
    if selected_manager_ids and multibrand.enabled():
        # A selected component identifies the entire physical manager duty.
        # This lookup supplies identities only; every leg still passes the
        # report's date, brand, owner and current branch authorization below.
        legs = c.execute('SELECT l.shift_id FROM central_workday_legs l '
                         'JOIN central_workday_legs anchor ON anchor.workday_id=l.workday_id '
                         'WHERE anchor.shift_id=%s', (manager_shift_id,)).fetchall()
        if legs:
            selected_manager_ids = {leg['shift_id'] for leg in legs}
    managers, driver_rows, cases, entries, approvals = [], {}, [], [], []
    day_allowed = user['role'] == 'owner' or reports_supervisor(user)
    pending = 0
    timing_orders = []
    workload_scope = [dict(brand_id=p['brand_id'], branch_ids=p.get('branch_ids', []),
                           unrestricted=ops.unrestricted_owner(p)) for p in principals]
    now = datetime.now(TZ)
    for principal in principals:
        locations = {b['id']: b for b in c.execute('SELECT id,name,city FROM branches WHERE brand_id=%s', (principal['brand_id'],)).fetchall()}
        candidates = c.execute("SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.kind='manager' AND s.brand_id=%s "
                               "AND COALESCE(s.reporting_operational_date,s.delivery_operational_date,(s.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '4 hours')::date)=%s ORDER BY s.opened_at,s.id",
                               (principal['brand_id'], day)).fetchall()
        visible = [s for s in candidates if allowed_manager(principal, s, scope)
                   and (selected_manager_ids is None or s['id'] in selected_manager_ids)]
        if delay_details:
            visible = [s for s in candidates if allowed_manager(principal, s, 'day')]
            timing_orders.extend(delivery_orders(c, principal, candidates, visible, locations, day, now,
                                                 workload_scope, details=True))
            continue
        if (scope == 'day' and reports_supervisor(principal)
                and any(s['user_id'] != user['id'] and s['status'] != 'closed' for s in visible)):
            raise db.error(409, 'SUPERVISED_REPORT_PENDING', 'التقرير اليومي بانتظار اعتماد جميع الشفتات المخولة.')
        day_allowed |= any(s['user_id'] == user['id'] and (s.get('delivery_window_snapshot') or {}).get('period', 'evening') == 'evening' for s in visible)
        for manager in visible:
            if principal.get('_morning_manager_context'):
                ops.shift_access(c,principal,manager,capability='reports_view')
            managers.append(manager)
            drivers = c.execute("SELECT s.*,u.display_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.manager_shift_id=%s AND s.kind='driver' ORDER BY s.opened_at,s.id", (manager['id'],)).fetchall()
            # Never emit a partially visible physical duty as a complete report.
            if any(s['brand_id'] != principal['brand_id'] or (not ops.unrestricted_owner(principal) and not set(s['branch_ids']) <= set(principal.get('branch_ids', []))) for s in drivers):
                raise db.error(403, 'OPERATING_REPORT_SCOPE', 'توجد تقفيلة خارج نطاق التقرير المصرح.')
            for shift in drivers:
                snap, rid = snapshot(c, shift)
                data = project_driver(shift, snap)
                period = (manager.get('delivery_window_snapshot') or {}).get('period', 'evening')
                key = (shift['user_id'], period)
                row = driver_rows.setdefault(key, dict(name=shift['display_name'], period=period, brands={k: dict(count=0, amount=0) for k in BRANDS}, net=0, closed=True,evidence_missing=False,network=0,cash=0,cities=set(),branches=set()))
                for brand, item in data['brands'].items():
                    for field in ('count', 'amount'):
                        row['brands'][brand][field] += item[field]
                row['net'] += data['net']
                network, cash = collection_channels(snap)
                row['network'] = row['network'] + network if row['network'] is not None and network is not None else None
                row['cash'] = row['cash'] + cash if row['cash'] is not None and cash is not None else None
                branch_ids = {o['branch_id'] for o in snap.get('orders', []) if o.get('fulfilled_in_report') and o.get('branch_id')}
                if not branch_ids:
                    branch_ids = set(shift.get('branch_ids', []))
                for bid in branch_ids:
                    if bid in locations:
                        row['cities'].add(locations[bid]['city'])
                        row['branches'].add(locations[bid]['name'])
                # Physical staffing can end before accounting closes the
                # component ledger (vault bags and approved remote duties).
                # The guarded marker is operational evidence, never approval.
                row['closed'] &= not snap.get('evidence_missing') and (
                    shift.get('staffing_ended_at') is not None
                    or shift['status'] == 'closed' and rid is not None)
                row['evidence_missing'] |= bool(snap.get('evidence_missing'))
                for case in data['cases']:
                    cases.append(dict(case, driver=shift['display_name']))
                if not rid:
                    entries.append({})
            if manager['status'] != 'closed' and manager.get('staffing_ended_at') is None:
                pending += 1
        if visible:
            timing_orders.extend(delivery_orders(c, principal, candidates, visible, locations, day, now, workload_scope, details=False))
            # Reuse canonical accounting deadlines, extensions and completion;
            # expose status only, not the underlying accounting money/notes.
            from .accounting_statements import manager_cards
            bids = sorted({b for m in visible for b in m['branch_ids']})
            ids = {m['id'] for m in visible}
            cards = manager_cards(c, principal, bids, manager_shift_ids=list(ids))['cards']
            for card in cards:
                if card['id'] in ids:
                    entries.extend(card['drivers'])
            approvals.extend(c.execute('SELECT a.id,a.manager_shift_id,a.created_at,u.display_name approved_name FROM accounting_daily_closures a JOIN users u ON u.id=a.approved_by WHERE a.manager_shift_id=ANY(%s) AND a.brand_id=%s ORDER BY a.created_at,a.id', (list(ids), principal['brand_id'])).fetchall())
            # Closed driver reports whose manager is still open have not yet
            # entered accounting's canonical queue. They remain pending.
            card_ids = {r['id'] for g in cards if g['id'] in ids for r in g['drivers']}
            for manager in visible:
                for r in c.execute("SELECT r.id FROM closure_reports r JOIN shifts s ON s.id=r.shift_id WHERE s.manager_shift_id=%s AND r.kind='driver'", (manager['id'],)).fetchall():
                    if r['id'] not in card_ids:
                        entries.append({})
    if delay_details:
        return ops.safe(dict(day=day, delivery_timing=delivery_timing_summary(timing_orders)))
    if selected_manager_ids is not None and ({m['id'] for m in managers} != selected_manager_ids
                                             or manager_shift_id not in selected_manager_ids):
        raise db.error(404, 'OPERATING_REPORT_SHIFT', 'المناوبة غير متاحة ضمن نطاق التقرير المصرح.')
    if scope == 'day' and not day_allowed:
        raise db.error(403, 'OPERATING_REPORT_DAY', 'اليوم الكامل لمدير المساء صاحب المناوبة والمالك.')
    rows = sorted(driver_rows.values(), key=lambda r: (r['period'] != 'morning', r['name']))
    for row in rows:
        row['city'] = ' / '.join(sorted(row.pop('cities'))) or 'غير مسجل'
        row['branch'] = ' / '.join(sorted(row.pop('branches'))) or 'غير مسجل'
        row.update(matching(row))
    accounting = progress(entries, approvals, [m['id'] for m in managers], pending)
    return ops.safe(dict(day=day, scope=scope, can_day=day_allowed and not bool(user.get('_morning_manager_context')), filename=filename(day, scope), manager=user['display_name'],
                         generated_at=now, rows=rows, cases=cases, accounting=accounting,
                         delivery_timing=delivery_timing_summary(timing_orders, details=False),
                         can_view_delay_details=can_view_delay_details(user),
                         operational_complete=bool(managers) and not pending and all(r['closed'] for r in rows),evidence_missing=any(r['evidence_missing'] for r in rows),
                         count=sum(v['count'] for r in rows for v in r['brands'].values()), net=sum(r['net'] for r in rows)))


@router.get('')
def listing(day: date | None = None, scope: Literal['shift', 'day'] = 'shift', manager_shift_id: str | None = None,
            previous_day: bool = False, delay_details: bool = False, user=Depends(auth.current_user)):
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        default_day = service_hours.delivery_operational_date() - timedelta(days=int(previous_day))
        return build(c, user, day or default_day, 'day' if delay_details else scope,
                     None if delay_details else manager_shift_id, delay_details=delay_details)


def money(value):
    return '—' if value is None else f'{value / 100:,.2f}'.rstrip('0').rstrip('.')


def report_tables(report):
    headers = ['السائق', 'الوردية', 'المدينة / المحافظة', 'كبز: عدد', 'كبز: قيمة', 'عنبيشن: عدد', 'عنبيشن: قيمة', 'قيمة الطلبات', 'شبكة', 'كاش', 'العجز', 'الفائض', 'المطابقة', 'التقفيلة التشغيلية']
    rows = [[r['name'], 'صباحي' if r['period'] == 'morning' else 'مسائي', r.get('city') or 'غير مسجل',
             r['brands']['kps']['count'], money(r['brands']['kps']['amount']), r['brands']['enbishan']['count'], money(r['brands']['enbishan']['amount']), money(r['net']),
             money(r.get('network')), money(r.get('cash')), money(matching(r)['shortage']), money(matching(r)['surplus']), matching(r)['status'],
             'لقطة مفقودة' if r.get('evidence_missing') else 'مكتملة' if r['closed'] else 'معلّقة'] for r in report['rows']]
    projected = [dict(r, **matching(r)) for r in report['rows']]
    totals = ['الإجمالي', '', '', *[sum(r['brands'][b]['count'] for r in report['rows']) if k == 'count' else money(sum(r['brands'][b]['amount'] for r in report['rows'])) for b in BRANDS for k in ('count', 'amount')], money(report['net']),
              *[money(complete_sum(projected, k)) for k in ('network', 'cash', 'shortage', 'surplus')], '', '']
    cases = [[r['driver'], BRANDS.get(r['brand'], r['brand']), r['kind'], money(r['amount']), r['reference'], r['reason'], r['approval']] for r in report['cases']]
    return headers, rows, totals, cases


def city_tables(report):
    groups = defaultdict(list)
    for row in report['rows']:
        groups[row.get('city') or 'غير مسجل'].append(dict(row, **matching(row)))
    headers = ['المدينة / المحافظة', 'الطلبات', 'قيمة الطلبات', 'شبكة', 'كاش', 'العجز', 'الفائض']
    rows = [[city, sum(v['count'] for r in items for v in r['brands'].values()), money(sum(r['net'] for r in items)),
             *[money(complete_sum(items, field)) for field in ('network', 'cash', 'shortage', 'surplus')]] for city,items in sorted(groups.items())]
    return headers, rows


def delivery_timing_tables(report):
    def duration(seconds, late=False):
        if seconds is None:
            return '—'
        minutes, seconds = divmod(int(seconds), 60)
        return f'{minutes}:{seconds:02}' + (' · متأخر' if late else '')

    def workload(order):
        evidence = order.get('workload') or {}
        state = evidence.get('state')
        if state == 'assigned':
            return f"{evidence['driver']} · مسند {evidence['assigned']} · معه {evidence['onboard']}"
        if state == 'unassigned':
            return 'غير مسند عند التأخير'
        return 'السجل غير كافٍ' + (f" · {evidence['driver']}" if evidence.get('driver') else '')

    summary, details = [], []
    for group in report.get('delivery_timing', []):
        limits = ' / '.join(str(limit // 60) for limit in group['limits'])
        summary.append([group['city'], limits or '—', group['on_time'], group['late'], group['open_late'],
                        group['open'], group['cancelled'], group['unavailable'],
                        '—' if group['compliance'] is None else f"{group['compliance']:g}%"])
        for order in group['delayed_orders']:
            details.append([order['reference'], group['city'], BRANDS.get(order['brand'], order['brand']),
                            order['branch'], order['driver'] or '—',
                            duration(order['preparation_seconds'], order['preparation_late']),
                            'متأخر' if order['pickup_late'] else '—',
                            duration(order['delivery_seconds'], order['delivery_late']),
                            duration(order['elapsed_seconds'], order['status'] in ('late', 'open_late')),
                            datetime.fromisoformat(str(order['delay_at'])).astimezone(TZ).strftime('%H:%M:%S')
                            if order.get('delay_at') else '—', workload(order)])
    return [
        ('الالتزام بوقت التوصيل', ['المحافظة', 'الحد بالدقائق', 'ضمن الوقت', 'وصل متأخرًا',
                                 'متأخر ولم يسلّم', 'مفتوح ضمن الوقت', 'ملغى', 'غير قابل للتقييم', 'الالتزام'], summary),
        ('تفاصيل الطلبات المتأخرة', ['الطلب', 'المحافظة', 'البراند', 'الفرع', 'السائق', 'مدة التجهيز',
                                   'موعد الاستلام', 'مدة التوصيل', 'إجمالي المدة', 'وقت التأخير', 'حمل السائق عند التأخير'], details),
    ]


def accounting_lines(report):
    a = report['accounting']
    lines = [f"اكتمال المراجعة المحاسبية: {a['percent']}٪ ({a['completed_steps']} من {a['total_steps']} خطوة)"]
    if a['overdue']:
        lines.append(f"متأخر: {a['overdue']} تقفيلة تحتاج متابعة")
    if a['late']:
        lines.append(f"سجل التأخير: {a['late']} تقفيلة أُنجزت بعد الموعد")
    if a['approved']:
        for approval in a['approvals']:
            at = datetime.fromisoformat(approval['created_at']).astimezone(TZ)
            lines.append(f"معتمد محاسبيًا | المعتمد: {approval['approved_name']} | {at:%d-%m-%Y %H:%M} | {approval['id']}")
    else:
        lines.append('قيد المراجعة المحاسبية — الاعتماد النهائي لم يكتمل')
    return lines


def make_html(report):
    e = lambda value: escape(str(value))
    headers, rows, totals, cases = report_tables(report)
    def table(head, data, total=None, timing=False):
        return '<table><thead><tr>'+''.join('<th>'+e(x)+'</th>' for x in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join(('<td class="or-timing-late">' if timing and str(x).endswith('متأخر') else '<td>')+e(x)+'</td>' for x in r)+'</tr>' for r in data)+'</tbody>'+('<tfoot><tr>'+''.join('<td>'+e(x)+'</td>' for x in total)+'</tr></tfoot>' if total else '')+'</table>'
    body = '<div class="or-print-logos"><img src="/assets/operating-report/kps.jpeg" alt="كبز"><img src="/assets/operating-report/enbishan.jpeg" alt="عنبيشن"></div><h1>'+('تقرير اليوم التشغيلي' if report['scope'] == 'day' else 'تقرير شفتي')+'</h1><p>'+e(report['day'])+' · '+e(report['manager'])+'</p>'
    body += '<p>'+str(report['count'])+' طلب · صافي المبيعات '+money(report['net'])+' ر.س</p>'+table(headers, rows, totals)
    city_head, city_rows = city_tables(report)
    body += '<h2>ملخص المدن / المحافظات</h2>'+table(city_head, city_rows)
    body += '<p>المطابقة: قيمة الطلبات = شبكة + كاش. النقص عجز والزيادة فائض. — بيانات غير متاحة.</p>'
    for title, headers, data in delivery_timing_tables(report)[:1]:
        if data:
            body += '<h2>'+e(title)+'</h2>'+table(headers, data, timing=True)
    if cases:
        body += '<h2>الضيافة والتعويضات والحالات</h2>'+table(['السائق', 'البراند', 'النوع', 'القيمة المرجعية', 'المرجع', 'السبب', 'الحالة'], cases)
    else:
        body += '<p>لا توجد ضيافة أو تعويضات أو حالات مسجلة.</p>'
    body += '<section class="accounting '+('approved' if report['accounting']['approved'] else '')+'"><h2>متابعة المراجعة المحاسبية</h2><progress max="100" value="'+str(report['accounting']['percent'])+'"></progress>'+''.join('<p>'+e(line)+'</p>' for line in accounting_lines(report))+'</section>'
    body += '<p class="note">تقرير تشغيلي. قيم الضيافة والتعويضات مرجعية ولا تخصم مرة أخرى من التحصيل. لا يشمل تأكيد إيداع البنك إلا وفق سجل المحاسبة.</p>'
    return '<!doctype html><html lang="ar" dir="rtl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+e(report['filename'][:-4])+'</title><link rel="stylesheet" href="/assets/operating-reports.css?v=20261108-compact-delivery"><script src="/report.js" defer></script><main><nav class="screen-only"><button data-print-report>طباعة</button></nav>'+body+'</main></html>'


def make_pdf(report, paper='A3', *, compact=False):
    """Readable landscape report, paginating all evidence at a fixed font size."""
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.pagesizes import A4, A3, landscape
    from reportlab.lib import colors
    import arabic_reshaper
    from bidi.algorithm import get_display
    font = 'OperatingKufi'
    if font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font, str(Path(__file__).resolve().parents[1] / 'public/assets/fonts/NotoKufiArabic-Regular.ttf')))
    def visual(text):
        return get_display(arabic_reshaper.reshape(str(text)))
    head, rows, total, cases = report_tables(report)
    lines = accounting_lines(report)
    city_head, city_rows = city_tables(report)
    size = landscape(A4 if paper == 'A4' else A3)
    width, height = size
    out = BytesIO()
    class NumberedCanvas(canvas.Canvas):
        # Replay finished pages after their total is known, so every footer is
        # accurate without estimating content height or rebuilding the report.
        def showPage(self):
            self.saved_pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            pages = self.saved_pages
            for state in pages:
                self.__dict__.update(state)
                text('تقرير تشغيلي · قيم الحالات مرجعية · جميع المبالغ بالريال السعودي', width-margin, margin+10, 8)
                text(report['filename'], width-margin, margin-2, 7)
                text(f'صفحة {self._pageNumber} من {len(pages)}', margin+100, margin+10, 8)
                super().showPage()
            super().save()

    cv = NumberedCanvas(out, pagesize=size, invariant=1)
    cv.saved_pages = []
    cv.setTitle(report['filename'][:-4])
    day_layout = compact and report['scope'] == 'day'
    margin = 16 if day_layout else 22
    usable = width - 2 * margin
    y = height - margin
    def text(value, right, baseline, pts=9, color='#232d34'):
        cv.setFillColor(colors.HexColor(color)); cv.setFont(font, pts)
        cv.drawRightString(right, baseline, visual(value))
    # Embed the owner-supplied files without tracing, recolouring or stretching.
    logo_root = Path(__file__).resolve().parents[1] / 'public/assets/operating-report'
    for i, brand in enumerate(BRANDS):
        cv.drawImage(str(logo_root / (brand + '.jpeg')), margin + i*49, y-44,
                     width=43, height=44, preserveAspectRatio=True, anchor='c')
    text('التقرير التشغيلي', width-margin, y-12, 17)
    y -= 34
    operating_day = date.fromisoformat(str(report['day']))
    text(f"{DAYS[operating_day.weekday()]}، {operating_day:%d-%m-%Y} · {report['manager']}", width-margin, y, 10)
    text("اليوم التشغيلي · الصباحي والمسائي" if report["scope"] == "day" else "شفتي", width-margin, y-14, 8, "#687078")
    y -= 25 if day_layout else 32
    for i, (value, label) in enumerate([(str(report['count']), 'طلب'),
                                      (str(len(report['rows'])), 'عدد السائقين'),
                                      (money(report['net']) + ' ر.س', 'صافي الطلبات')]):
        cell = usable / 3
        right = width-margin-i*cell
        cv.setFillColor(colors.HexColor('#fafafa'))
        cv.setStrokeColor(colors.HexColor('#e0e3e6'))
        cv.rect(right-cell, y-43, cell, 43, stroke=1, fill=1)
        text(value, right-12, y-18, 13)
        text(label, right-12, y-34, 8, '#687078')
    y -= 49 if day_layout else 58
    # Wrap logical Arabic before shaping. Measure all rows before drawing.
    def wrapped(value, max_width, pts):
        result, line = [], ''
        for word in str(value).split():
            if pdfmetrics.stringWidth(visual((line+' '+word).strip()), font, pts) > max_width and line:
                result.append(line); line = ''
            for char in word:
                candidate = line + char
                if pdfmetrics.stringWidth(visual(candidate), font, pts) > max_width and line:
                    result.append(line); line = ''
                line += char
            line += ' '
        return result + [line.strip()]
    row_padding = 3 if day_layout else 4 if compact else 8
    line_spacing = 2 if day_layout else 3
    def layout(data, widths, pts):
        return [(cells, max(len(x) for x in cells)*(pts+line_spacing)+row_padding) for cells in [[wrapped(v, w-8, pts) for v,w in zip(row,widths)] for row in data]]
    weights = ([112,60,108,37,55,37,60,78,56,56,45,49,82,104] if day_layout
               else [100,51,89,35,62,35,62,77,63,63,51,51,86,83])
    widths = [usable*x/sum(weights) for x in weights]
    case_widths = [usable*x for x in (.15,.07,.12,.10,.12,.29,.15)]
    city_widths = [usable*x for x in (.25,.10,.17,.12,.12,.12,.12)]
    pts = 12 if day_layout else 10
    leading = pts + line_spacing
    cell_baseline = pts + (1 if day_layout else 4)
    title_height = 19 if day_layout else 25
    bottom = margin + (24 if day_layout else 34)  # Reserve the footer on every page.
    main_layout = layout([head, *rows, total], widths, pts)
    case_layout = layout([['السائق','البراند','النوع','القيمة المرجعية','المرجع','السبب','الحالة'], *cases], case_widths, pts) if cases else []
    city_layout = layout([city_head, *city_rows], city_widths, pts)

    def new_page():
        nonlocal y
        cv.showPage()
        y = height - margin
        text('التقرير التشغيلي · ' + str(report['day']), width-margin, y-12, 11)
        y -= 32

    def ensure(space):
        if y - space < bottom:
            new_page()

    def draw_row(cells, cols, dark=False, shaded=False, timing=False):
        nonlocal y
        rh = max(len(cell) for cell in cells)*leading+row_padding
        cv.setFillColor(colors.HexColor('#232d34' if dark else '#f4f7f9' if shaded else '#ffffff'))
        cv.rect(margin, y-rh, usable, rh, stroke=0, fill=1)
        x = width-margin
        for col, cell in zip(cols, cells):
            color = '#ffffff' if dark else '#b42318' if timing and ' '.join(cell).endswith('متأخر') else '#232d34'
            for j,line in enumerate(cell):
                text(line, x-4, y-cell_baseline-j*leading, pts, color)
            cv.setStrokeColor(colors.HexColor('#cfd6de'))
            cv.rect(x-col,y-rh,col,rh,stroke=1,fill=0)
            x -= col
        y -= rh

    def draw_table(data, cols, total_row=False, heading=None, grouped=False, timing=False):
        nonlocal y
        header, header_height = data[0]
        def table_header():
            nonlocal y
            if heading:
                text(heading,width-margin,y-11,12 if day_layout else 10)
                y -= title_height
            if grouped:
                draw_groups()
            draw_row(header, cols, dark=True)
        header_space = header_height + (title_height if heading else 0) + (group_height if grouped else 0)
        # Keep a section title and headings with at least one body line.
        ensure(header_space + leading + 8)
        table_header()
        for index,(cells, rh) in enumerate(data[1:], 1):
            remaining = [list(cell) for cell in cells]
            # Prefer an intact row. Only rows taller than a whole fresh page
            # are split; every cell's wrapped lines are retained in sequence.
            fresh_capacity = height-margin-32-bottom-header_space
            if y-rh < bottom and rh <= fresh_capacity:
                new_page()
                table_header()
            while any(remaining):
                capacity = int((y-bottom-8)//leading)
                if capacity < 1:
                    new_page()
                    table_header()
                    continue
                fragment = [cell[:capacity] for cell in remaining]
                draw_row(fragment, cols, dark=total_row and index == len(data)-1, shaded=index%2 == 0, timing=timing)
                remaining = [cell[capacity:] for cell in remaining]
                if any(remaining):
                    new_page()
                    table_header()
        y -= 8 if day_layout else 12
    # Grouped brand headings preserve the two-brand comparison on paper.
    x=width-margin
    groups = [(head[i], widths[i], '#edf2f6') for i in range(3)]
    groups += [('كبز', sum(widths[3:5]), '#fff0e3'), ('عنبيشن', sum(widths[5:7]), '#e6f7f7')]
    groups += [(head[i], widths[i], '#edf2f6') for i in range(7, len(head))]
    group_height = max(len(wrapped(label, span-8, pts)) for label,span,_ in groups)*leading+(5 if day_layout else 8)
    def draw_groups():
        nonlocal y
        x = width-margin
        for label,span,fill in groups:
            cv.setFillColor(colors.HexColor(fill))
            cv.rect(x-span,y-group_height,span,group_height,stroke=0,fill=1)
            for j,line in enumerate(wrapped(label,span-8,pts)):
                text(line,x-4,y-cell_baseline-j*leading,pts)
            x-=span
        y-=group_height
    main_layout[0]=layout([['','','','عدد','قيمة','عدد','قيمة','','','','','','','']],widths,pts)[0]
    draw_table(main_layout,widths,True,grouped=True)
    draw_table(city_layout,city_widths,heading='ملخص المدن / المحافظات')
    for title, headers, data in delivery_timing_tables(report)[:1]:
        if data:
            cols = [usable / len(headers)] * len(headers)
            draw_table(layout([headers, *data], cols, pts), cols, heading=title, timing=True)
    if cases:
        draw_table(case_layout,case_widths,heading='حالات التشغيل')
    else:
        note_height = 15 if day_layout else 20
        ensure(note_height)
        text('لا توجد ضيافة أو تعويضات أو حالات مسجلة.',width-margin,y-10,9)
        y -= note_height
    approved = report['accounting']['approved']
    normal_lines=[line for line in lines if not line.startswith('معتمد محاسبيًا')]
    approval_lines=[line for line in lines if line.startswith('معتمد محاسبيًا')]
    approval_parts = [part for line in approval_lines for part in wrapped(line.removeprefix('معتمد محاسبيًا | '),usable-18,9)]
    accounting_height = 40 + sum(len(wrapped(line,usable-18,9))*17 for line in normal_lines)
    if approved:
        accounting_height += 48 + len(approval_parts)*17
    # Keep ordinary review details and their stamp together. Exceptionally
    # large approval evidence is safely split below.
    ensure(min(accounting_height,height-margin-32-bottom))
    cv.setFillColor(colors.HexColor('#e4e9ed'));cv.roundRect(margin,y-8,usable,7,3,stroke=0,fill=1)
    if report['accounting']['percent']:
        cv.setFillColor(colors.HexColor('#08733a' if approved else '#e78216'))
        cv.roundRect(width-margin-usable*report['accounting']['percent']/100,y-8,usable*report['accounting']['percent']/100,7,3,stroke=0,fill=1)
    y-=23
    text('المطابقة: قيمة الطلبات = شبكة + كاش · النقص عجز والزيادة فائض · — بيانات غير متاحة',width-margin,y,9);y-=17
    for line in normal_lines:
        for part in wrapped(line,usable-18,9):
            ensure(17)
            text(part,width-margin,y-10,9,'#08733a' if approved else '#9b4d00');y-=17
    if approved:
        # Long approver names/IDs and many approvals can span pages. Each
        # chunk retains an approval border/title and never covers the footer.
        while approval_parts:
            ensure(64)
            capacity = max(1,int((y-bottom-42)//17))
            chunk, approval_parts = approval_parts[:capacity], approval_parts[capacity:]
            stamp_height = 36+len(chunk)*17
            cv.setStrokeColor(colors.HexColor('#08733a'));cv.setLineWidth(1.5)
            cv.roundRect(margin,y-stamp_height,usable,stamp_height,5,stroke=1,fill=0)
            text('معتمد محاسبيًا',width-margin-9,y-18,12,'#08733a')
            for i,part in enumerate(chunk):
                text(part,width-margin-9,y-36-i*17,9,'#08733a')
            y-=stamp_height+12
            if approval_parts:
                new_page()
    cv.showPage();cv.save()
    return out.getvalue()


@router.get('/export/{format}')
def export(format: Literal['pdf', 'print'], day: date | None = None, scope: Literal['shift', 'day'] = 'shift', manager_shift_id: str | None = None, user=Depends(auth.current_user)):
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        report = build(c,user,day or service_hours.delivery_operational_date(),scope,manager_shift_id)
    headers = {'Cache-Control': 'private, no-store'}
    if format == 'pdf':
        headers['Content-Disposition'] = disposition(report['filename'])
    return Response(make_pdf(report, compact=True) if format == 'pdf' else make_html(report),
                    media_type='application/pdf' if format == 'pdf' else 'text/html', headers=headers)
