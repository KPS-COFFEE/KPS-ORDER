"""Owner-controlled customer entry flow and operational stage targets.

Brand-scoped settings share the existing database, authorization and replay engine.
Targets are captured with each new order; historical reports remain immutable.
"""
from copy import deepcopy
from fastapi import APIRouter, Depends, Header, Query
from . import auth, db

router = APIRouter(prefix='/api/experience', tags=['experience'])
STAGES = ('manager_wait', 'assignment', 'driver_acceptance', 'delivery', 'preparer_wait', 'preparation')


def defaults():
    return {'version': 0, 'customer_entry_enabled': False, 'timing_enabled': False,
            'assignment_start': 'manager_accepted',
            'total_seconds': {'hafar': 2700, 'outside': 1800},
            'stages': {'manager_wait': {'orange': 60, 'red': 90},
                       'assignment': {'orange': 720, 'red': 1020},
                       'driver_acceptance': {'orange': None, 'red': 180},
                       'delivery': {'orange': 900, 'red': 1500},
                       'preparer_wait': {'orange': 60, 'red': 90},
                       'preparation': {'orange': 300, 'red': 720}}}


def policy(conn, brand_id):
    row = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s', (brand_id,)).fetchone()
    # Published staff-only policies predate the customer entry switch. Preserve
    # their version and targets while introducing the new switch disabled.
    return defaults() | deepcopy((row or {}).get('settings', {}).get('experience_policy') or {})


def validate(value):
    shape = set(defaults()) - {'version'}
    if not isinstance(value, dict) or set(value) != shape:
        raise db.error(422, 'EXPERIENCE_POLICY_INVALID', 'أرسل جميع معايير المراحل المعتمدة.')
    if any(type(value[k]) is not bool for k in ('customer_entry_enabled', 'timing_enabled')):
        raise db.error(422, 'EXPERIENCE_POLICY_INVALID', 'اختر حالة التفعيل بصورة صحيحة.')
    if value['assignment_start'] not in ('created', 'manager_accepted'):
        raise db.error(422, 'EXPERIENCE_POLICY_INVALID', 'اختر بداية عداد الإسناد.')
    totals = value['total_seconds']
    if not isinstance(totals, dict) or set(totals) != {'hafar', 'outside'} or any(
            type(v) is not int or not 60 <= v <= 14400 for v in totals.values()):
        raise db.error(422, 'EXPERIENCE_POLICY_INVALID', 'حدد إجمالي الطلب بين دقيقة و240 دقيقة.')
    if not isinstance(value['stages'], dict) or set(value['stages']) != set(STAGES):
        raise db.error(422, 'EXPERIENCE_POLICY_INVALID', 'راجع جميع مراحل الطلب.')
    for limits in value['stages'].values():
        if not isinstance(limits, dict) or set(limits) != {'orange', 'red'}:
            raise db.error(422, 'EXPERIENCE_POLICY_INVALID', 'حدد البرتقالي والأحمر لكل مرحلة.')
        orange, red = limits['orange'], limits['red']
        if type(red) is not int or not 1 <= red <= 14400 or (orange is not None and (
                type(orange) is not int or not 1 <= orange < red)):
            raise db.error(422, 'EXPERIENCE_POLICY_INVALID', 'البرتقالي أقل من الأحمر؛ يمكن ترك البرتقالي فارغًا.')
    return deepcopy(value)


@router.get('/public')
def public_policy(brand_id: str | None = Query(default=None, max_length=80)):
    from .storefront_scope import resolve_brand
    brand_id = resolve_brand(brand_id)
    from .storefront_scope import require_brand
    require_brand(brand_id)
    with db.tx() as conn:
        if not conn.execute('SELECT 1 FROM brands WHERE id=%s AND active', (brand_id,)).fetchone():
            raise db.error(404, 'BRAND_NOT_FOUND', 'البراند غير متاح.')
        p = policy(conn, brand_id)
        from .first_order_gift import configured, eligibility
        row = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s', (brand_id,)).fetchone()
        gift = configured((row or {}).get('settings'))
        offer_active = eligibility(gift, verified=False, claimed=False) == 'verification_required'
    return {'customer_entry_enabled': p['customer_entry_enabled'], 'gift_offer_active': offer_active}


@router.get('/policy')
def read_policy(user=Depends(auth.require_roles('owner'))):
    with db.tx() as conn:
        return {'policy': policy(conn, user['brand_id'])}


@router.put('/policy')
def save_policy(body: dict, user=Depends(auth.require_roles('owner')),
                idem: str | None = Header(None, alias='Idempotency-Key')):
    if set(body) != {'policy', 'expected_version', 'reason'} or type(body['expected_version']) is not int:
        raise db.error(422, 'EXPERIENCE_POLICY_INVALID', 'أرسل إصدار المسودة وسبب التعديل.')
    proposed = validate(body['policy'])
    reason = body['reason']
    if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 500:
        raise db.error(422, 'REASON_REQUIRED', 'أدخل سبب التعديل من 3 إلى 500 حرف.')
    with db.tx() as conn:
        # An authenticated owner confirms this operational change in the UI.
        def perform():
            row = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s FOR UPDATE', (user['brand_id'],)).fetchone()
            if not row:
                raise db.error(404, 'BRAND_NOT_FOUND', 'إعدادات البراند غير متاحة.')
            previous = row['settings'].get('experience_policy') or defaults()
            if previous['version'] != body['expected_version']:
                raise db.error(409, 'POLICY_CHANGED', 'عُدلت المعايير من جلسة أخرى. أعد فتحها قبل الحفظ.')
            saved = proposed | {'version': previous['version'] + 1}
            conn.execute('UPDATE app_settings SET settings=%s,updated_at=now() WHERE brand_id=%s',
                         (db.json(row['settings'] | {'experience_policy': saved}), user['brand_id']))
            db.audit(conn, user['id'], 'experience.policy.updated', 'brand', user['brand_id'],
                     {'before': previous, 'after': saved, 'reason': reason.strip()})
            return {'policy': saved, 'applies_to': 'new_orders', 'historical_orders_preserved': True}
        return db.idempotent(conn, user['id'], idem, 'experience.policy.update', body, perform)


def classification(seconds, limits):
    if seconds is None:
        return 'unavailable'
    if seconds >= limits['red']:
        return 'red'
    if limits['orange'] is not None and seconds >= limits['orange']:
        return 'orange'
    return 'normal'


def evaluate(order, points, tasks, now):
    """Only recorded event timestamps can start/end a phase. No read resets clocks."""
    from .performance import _date
    p = (order.get('performance_snapshot') or {}).get('stage_policy')
    if not p or not p.get('timing_enabled') or order.get('service') != 'delivery':
        return None
    cancelled = order.get('preparation_status') in {'cancelled', 'rejected'} or order.get('delivery_status') == 'cancelled'
    delivered = order.get('completed_at') or points.get('task.delivered')
    terminal = bool(cancelled or delivered or order.get('delivery_status') == 'delivered')
    end = _date(delivered) if delivered else now if not terminal else None
    created = _date(order.get('created_at'))
    total = max(0, (end-created).total_seconds()) if end and created else None
    city = ''.join(str(order.get('branch_city') or order.get('snapshot', {}).get('branch_city', '')).split())
    cap = p['total_seconds']['hafar' if city == 'حفرالباطن' else 'outside']
    phases = {}
    def phase(key, start, stop=None, ended=False):
        a, b = _date(start), _date(stop) if stop else now if not terminal and not ended else None
        seconds = max(0, (b-a).total_seconds()) if a and b else None
        phases[key] = {'seconds': seconds, 'running': bool(a and not stop and not ended and not terminal),
                       'color': classification(seconds, p['stages'][key])}
    accepted = points.get('preparation.accepted')
    printed = points.get('order.print_output_confirmed')
    preparing = points.get('preparation.preparing') or printed
    ready = points.get('preparation.ready')
    active_tasks = [t for t in tasks if t.get('status') not in {'cancelled', 'reassigned', 'transferred'}]
    task = active_tasks[-1] if active_tasks else None
    assigned = task.get('created_at') if task else None
    driver_accepted = task.get('accepted_at') if task else None
    pickup = task.get('picked_up_at') if task else points.get('task.picked_up')
    morning = order.get('snapshot', {}).get('delivery_period') == 'morning'
    phase('preparer_wait' if morning else 'manager_wait', created, accepted, ended=bool(preparing or assigned or pickup))
    if not morning:
        phase('preparer_wait', accepted, preparing, ended=bool(ready or pickup))
    phase('preparation', accepted if morning else preparing, ready, ended=bool(pickup))
    phase('assignment', created if p['assignment_start'] == 'created' else accepted, assigned, ended=bool(pickup))
    phase('driver_acceptance', assigned, driver_accepted, ended=bool(pickup))
    phase('delivery', pickup, delivered)
    reasons = [{'stage': k, **v} for k, v in phases.items() if v['running'] and v['color'] in {'red', 'orange'}]
    if total is not None and total >= cap and not cancelled:
        reasons.append({'stage': 'total', 'color': 'red', 'seconds': total, 'running': not terminal})
    color = 'unavailable' if cancelled or total is None else 'red' if any(r['color'] == 'red' for r in reasons) else 'orange' if reasons else 'normal'
    return {'version': p['version'], 'total_seconds': total, 'total_limit_seconds': cap,
            'color': color, 'phases': phases, 'reasons': reasons, 'closed': terminal}
