"""Central reporting over canonical, freshly granted branch scopes.

Money retains its canonical ledger semantics. Timing is weighted by valid
observations; grouped shifts count once as physical workdays.
"""
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal

from fastapi import APIRouter, Depends

from . import auth, db, multibrand, operations

router = APIRouter()


def _weighted(rows):
    count = sum(int(row.get('valid') or 0) for row in rows)
    return (sum(Decimal(str(row['average_seconds'])) * int(row.get('valid') or 0)
                for row in rows if row.get('average_seconds') is not None) / count
            if count else None)


def merge_dashboards(parts):
    """Merge disjoint canonical brand cohorts, never means of means."""
    result = deepcopy(parts[0])
    result['summary'] = {key: sum(int(p['summary'].get(key) or 0) for p in parts)
                         for key in set().union(*(p['summary'] for p in parts))}
    for name in ('delivery_time', 'delivery_performance'):
        rows = [p[name] for p in parts]
        combined = deepcopy(rows[0])
        for key in set().union(*(row.keys() for row in rows)):
            if key == 'average_seconds':
                combined[key] = _weighted(rows)
            elif isinstance(combined.get(key), dict):
                combined[key] = {code: sum(int(row.get(key, {}).get(code) or 0) for row in rows)
                                 for code in set().union(*(row.get(key, {}) for row in rows))}
            elif key not in {'threshold_minutes', 'threshold_configured'} and isinstance(combined.get(key), (int, float)) and not isinstance(combined.get(key), bool):
                combined[key] = sum(row.get(key, 0) or 0 for row in rows)
        result[name] = combined
    thresholds = {p['delivery_time'].get('threshold_minutes') for p in parts}
    result['delivery_time']['threshold_minutes'] = next(iter(thresholds)) if len(thresholds) == 1 else None
    result['delivery_time']['threshold_configured'] = all(p['delivery_time'].get('threshold_configured') for p in parts)
    methods = defaultdict(int)
    for p in parts:
        for row in p['collections']:
            methods[row['method']] += int(row['amount_halalas'])
    result['collections'] = [{'method': key, 'amount_halalas': value} for key, value in sorted(methods.items())]
    # Product/branch identifiers are globally unique; retaining each brand's
    # top N cannot omit a candidate for the combined top N.
    result['top_items'] = sorted([row for p in parts for row in p['top_items']],
                                 key=lambda r: (-int(r['quantity']), r['product_id'] or ''))[:10]
    result['top_branches'] = sorted([row for p in parts for row in p['top_branches']],
                                    key=lambda r: (-int(r['completed']), r['branch_id']))[:20]
    return result


@router.get('/dashboard')
def dashboard(period: str = 'today', start: str | None = None, end: str | None = None,
              brand_id: str | None = None, branch_id: str | None = None,
              service: str | None = None, user=Depends(auth.current_user)):
    from .central_operations import context, enabled
    if not enabled() or user['role'] == 'customer' or user.get('_morning_manager_context'):
        raise db.error(404, 'CENTRAL_DASHBOARD_UNAVAILABLE', 'التقرير غير متاح.')
    operations.roles(user, 'owner', 'delivery_manager', 'delivery_staff', 'branch_manager')
    if not auth.has_permission(user, 'dashboard_view'):
        raise db.error(403, 'STAFF_PERMISSION_FORBIDDEN', 'صلاحية التقرير غير متاحة.')
    if brand_id:
        raise db.error(422, 'CENTRAL_DASHBOARD_BRAND', 'اختر الفرع من شاشة التشغيل الموحدة.')
    with db.tx() as c:
        scopes = context(c, user)['brands']
        scopes = [scope for scope in scopes if scope['branch_ids']]
        if branch_id:
            scopes = [scope for scope in scopes if branch_id in scope['branch_ids']]
        if not scopes:
            raise db.error(403, 'CENTRAL_BRANCH_SCOPE', 'لا توجد فروع مخولة للتقرير.')
        scopes.sort(key=lambda scope: (scope['id'] != user['brand_id'], scope['id']))
        principals = []
        for scope in scopes:
            principal = multibrand.project(c, user, scope['id'])
            if not principal:
                raise db.error(403, 'CENTRAL_BRANCH_SCOPE', 'صلاحية التقرير تغيرت؛ أعد المحاولة.')
            # Canonical owners bypass branch filters. Narrow this read-only
            # query principal, including a foreign owner's explicit grants.
            principal = dict(principal, branch_ids=scope['branch_ids'])
            if principal['role'] == 'owner':
                principal['role'] = 'branch_manager'
            principals.append(principal)
    parts = []
    for principal in principals:
        part = operations.dashboard(period=period, start=start, end=end, branch_id=branch_id,
                                    service=service, user=principal)
        if not parts:
            # All brands use one displayed interval, even if their local
            # non-delivery reporting-day settings differ.
            start, end = part['period']['start'], part['period']['end']
        parts.append(part)
    result = merge_dashboards(parts)
    physical = {}
    with db.tx() as c:
        current_scopes = {scope['id']: set(scope['branch_ids']) for scope in context(c, user)['brands']}
        for principal in principals:
            # Recheck grants before returning the aggregate; no cached scope
            # may survive a membership revocation during this request.
            fresh = multibrand.project(c, user, principal['brand_id'])
            if not fresh or not set(principal['branch_ids']) <= current_scopes.get(principal['brand_id'], set()):
                raise db.error(403, 'CENTRAL_BRANCH_SCOPE', 'صلاحية التقرير تغيرت؛ أعد المحاولة.')
            rows = c.execute("SELECT s.id,s.status,s.submission,l.workday_id FROM shifts s "
                "LEFT JOIN central_workday_legs l ON l.shift_id=s.id WHERE s.brand_id=%s "
                "AND s.opened_at<%s AND coalesce(s.closed_at,%s)>=%s AND s.branch_ids <@ %s::jsonb "
                "AND s.branch_ids ?| %s",
                (principal['brand_id'], end, end, start, db.json(principal['branch_ids']),
                 [branch_id] if branch_id else principal['branch_ids'])).fetchall()
            for row in rows:
                identity = row['workday_id'] or row['id']
                entry = physical.setdefault(identity, {'status': row['status'], 'difference_halalas': 0})
                entry['difference_halalas'] += int((row['submission'] or {}).get('difference_halalas') or 0)
    counts = {}
    for row in physical.values():
        item = counts.setdefault(row['status'], {'status': row['status'], 'count': 0, 'difference_halalas': 0})
        item['count'] += 1
        item['difference_halalas'] += row['difference_halalas']
    result['shifts'] = list(counts.values())
    return operations.safe(result)
