"""Current-scope driver intent and canonical incentive ledgers in one workspace."""
import calendar
from datetime import date, datetime

from fastapi import APIRouter, Depends
from . import auth, db, multibrand, service_hours
from . import central_workdays as days, driver_destination as destinations, driver_incentives as incentives
from . import operations as ops

router = APIRouter(tags=['central-driver-overview'])


def own_legs(c, user):
    if user['role'] != 'driver' or user.get('_morning_manager_context'):
        raise db.error(403, 'CENTRAL_DRIVER_REQUIRED', 'هذه الشاشة للسائق فقط.')
    row = c.execute("SELECT s.id FROM shifts s JOIN central_workday_legs l ON l.shift_id=s.id "
                    "WHERE s.user_id=%s AND s.kind='driver' AND s.status='open' ORDER BY s.id LIMIT 1", (user['id'],)).fetchone()
    if not row:
        raise db.error(409, 'CENTRAL_DRIVER_SHIFT_REQUIRED', 'يلزم فتح الوردية المشتركة.')
    days.require_enabled(user, 'shifts_view', existing_sid=row['id'])
    day, legs = days.authorized(c, user, row['id'])
    if any(s['status'] != 'open' for _, s in legs):
        raise db.error(409, 'CENTRAL_WORKDAY_CLOSED', 'الوردية لا تستقبل وجهة جديدة.')
    return day, legs


def destination_view(c, user, legs):
    views = [(s, destinations.own_view(c, p, s)) for p, s in legs]
    active = sum(v['visible_active_task_count'] for _, v in views)
    brands = {s['brand_id'] for s, v in views if v['visible_active_task_count']}
    chosen = next(((s, v) for s, v in views if v['next_destination']), None)
    destination = chosen[1]['next_destination'] if chosen and not active else None
    if destination:
        # A task/return in either ledger invalidates the single physical intent.
        saved = chosen[0].get('next_destination') or {}
        try:
            declared = datetime.fromisoformat(saved['declared_at'])
            ids = [s['id'] for _, s in legs]
            changed = c.execute('SELECT 1 FROM driver_tasks WHERE shift_id=ANY(%s) AND created_at>%s '
                                'UNION ALL SELECT 1 FROM driver_work_trips WHERE shift_id=ANY(%s) '
                                'AND returned_at>=%s LIMIT 1', (ids, declared, ids, declared)).fetchone()
            if changed:
                destination = None
        except (ValueError, KeyError, TypeError):
            destination = None
    branch_rows = [dict(b, brand_id=s['brand_id']) for s, v in views for b in v['branches']]
    hafar = bool(views) and all(v['hafar'] for _, v in views)
    movement = ('delivering' if any(v['movement_state']=='delivering' for _, v in views) else
                'awaiting_pickup' if active else 'returning' if destination else 'awaiting_destination')
    brand = next(iter(brands)) if len(brands)==1 else 'mixed' if brands else None
    return {'movement_state':movement, 'visible_active_task_count':active,
            'active_order_brand':({'id':brand,'name':{'kps':'KPS','enbishan':'Enbishan','mixed':'Mixed'}[brand],
                                   'source':'active_task'} if brand else None),
            'next_destination':destination, 'destination_choice_required':hafar and not active and destination is None,
            'hafar':hafar, 'external_enbishan_allowed':False, 'branches':branch_rows}


@router.get('/driver-destination')
def own_destination(user=Depends(auth.current_user)):
    with db.tx() as c:
        _, legs = own_legs(c, user)
        return ops.safe(destination_view(c, user, legs))


@router.put('/driver-destination')
def choose_destination(body: destinations.DestinationChoice, user=Depends(auth.current_user)):
    with db.tx() as c:
        days.lock_lanes(c)
        _, legs = own_legs(c, user)
        if body.kind != 'branch':
            raise db.error(422, 'CENTRAL_BRANCH_DESTINATION', 'اختر فرعًا من فروع الوردية المشتركة.')
        view = destination_view(c, user, legs)
        if view['visible_active_task_count']:
            raise db.error(409, 'CENTRAL_DESTINATION_TASKS', 'أكمل الطلبات الحالية قبل اختيار الوجهة التالية.')
        if not view['hafar']:
            raise db.error(403, 'CENTRAL_DESTINATION_SCOPE', 'العودة خارج حفر الباطن إلى فرع الانطلاق.')
        selected = next(((p,s) for p,s in legs if body.branch_id in s['branch_ids'] and body.branch_id in p['branch_ids']), None)
        if not selected:
            raise db.error(403, 'CENTRAL_DESTINATION_SCOPE', 'الفرع خارج صلاحيات الوردية الحالية.')
        principal, shift = selected
        destinations.save_choice(c, principal, body)
        for _, other in legs:
            if other['id'] != shift['id']:
                c.execute('UPDATE shifts SET next_destination=%s WHERE id=%s', (db.json({}), other['id']))
        refreshed = [(p, c.execute('SELECT * FROM shifts WHERE id=%s',(s['id'],)).fetchone()) for p,s in legs]
        return ops.safe(destination_view(c, user, refreshed))


@router.get('/driver-incentives')
def driver_incentives(month: str = '', driver_id: str = '', user=Depends(auth.current_user)):
    from .central_operations import enabled
    if not enabled() or user['role']=='customer' or user.get('_morning_manager_context'):
        raise db.error(404, 'CENTRAL_DRIVER_OVERVIEW_UNAVAILABLE', 'الشاشة غير متاحة.')
    incentives.read_access(user)
    if user['role']=='driver' and driver_id and driver_id != user['id']:
        ops.fail(403, 'العداد خاص بك فقط')
    try:
        today = service_hours.delivery_operational_date()
        first = date.fromisoformat((month or today.strftime('%Y-%m'))+'-01')
        last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    except (ValueError, TypeError):
        ops.fail(422, 'الشهر غير صالح')
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        reports = []
        for brand in multibrand.available(c, user):
            principal = multibrand.project(c, user, brand['id'])
            if not principal:
                continue
            bids = None if principal['role']=='owner' and not principal.get('home_brand_id') else principal['branch_ids']
            report = incentives.report_data(c, principal, first, last, driver_id, branch_ids=bids)
            reports.append((brand['id'], report))
        output = {'rows':[], 'entries':[], 'today':today, 'period':{'start':first,'end':last},
                  'definitions':'الاستحقاق من سجلات كل براند المعتمدة؛ الشرائح محفوظة وفق سياسة السجل الأصلي.'}
        for key in ('eligible','excluded','pending_review','awaiting_approval','open_shifts'):
            output[key] = sum(r[key] for _,r in reports)
        for brand, report in reports:
            for key in ('rows','entries'):
                output[key].extend(dict(row, brand_id=brand) for row in report[key])
        output['rows'].sort(key=lambda r:(r['driver_name'],r['day'],r['brand_id']))
        return ops.safe(output)
