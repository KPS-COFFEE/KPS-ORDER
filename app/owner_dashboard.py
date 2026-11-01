"""Owner-only, read-only overview over the canonical accounting projection.

Financial cards and paginated details share one population and one ledger.
Cross-brand access is rebuilt from current grants; an owner of one brand is
never implicitly an unrestricted owner of another brand.
"""
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response

from . import accounting, auth, catalog, central_operations, db, multibrand, service_hours
from .operations import safe

router = APIRouter(prefix='/api/owner-dashboard', tags=['owner-dashboard'])

Period = Literal['today', 'yesterday', 'last7', 'month', 'custom']
Sort = Literal['created_desc', 'created_asc', 'value_desc', 'value_asc']

DEFINITION = (
    'اليوم التشغيلي لطلبات التوصيل ومبيعاتها وتحصيلها هو يوم شفت مدير التوصيل '
    'المحفوظ، حتى عند امتداد العمل بعد 04:00 أو منتصف الليل. المرتجع يتبع شفت '
    'تنفيذه. الطلبات دون شفت تتبع توقيت الحركة بحد 04:00 بتوقيت الرياض. '
    'الفواتير المستوردة تتبع يومها الأصلي الموثق. أوقات الإنشاء والإكمال الفعلية محفوظة.'
)
SCOPE_NOTE = (
    'الأعداد وحالات الطلبات تخص الطلبات المنسوبة للفترة، وتشمل الضيافة والتعويض. '
    'المبالغ تطابق المحاسبة التجارية: إجمالي المكتمل قبل المرتجعات وتصحيحات السعر، '
    'وصافي المبيعات بعد أثرها المحاسبي، والتحصيل حسب حركات الفترة. '
    'قد يظهر طلب أقدم بسبب تحصيل أو مرتجع أو تصحيح خلال الفترة دون زيادة عدد الطلبات. '
    'قيمة الضيافة مرجعية منفصلة ولا تدخل المبيعات؛ القيمة غير الموثقة تظهر كغير متاحة. '
    'السجلات الملغاة كتجارب مستبعدة. الفروع هي فروع تنفيذ الطلب، والسائق حسب '
    'المهمة الفعلية بعد تصحيحات الإسناد. حالة اكتمال التقفيلات تخص الفروع المختارة.'
)

# Names, not a second financial implementation. Values are produced by the
# same accumulate/finalize steps that feed /api/accounting/reports.
FINANCIAL_FIELDS = {
    'gross_completed_halalas': 'gross',
    'net_sales_halalas': 'net_sales',
    'cash_halalas': 'cash',
    'card_halalas': 'card',
    'refunds_halalas': 'refunds',
    'net_collected_halalas': 'total',
    'sales_refunds_halalas': 'sales_refunds',
    'price_credits_halalas': 'price_credits',
    'sales_price_credits_halalas': 'sales_price_credits',
}


def selected_period(preset, start, end):
    today = service_hours.delivery_operational_date()
    if preset == 'custom':
        try:
            # Require an explicit inclusive range; never silently broaden a
            # malformed or partially specified custom request to today's data.
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if first.isoformat() != start or last.isoformat() != end:
                raise ValueError('Use ISO calendar dates')
            last + timedelta(days=1)
        except (TypeError, ValueError, OverflowError):
            raise db.error(422, 'DATE_INVALID', 'حدد تاريخ بداية ونهاية صحيحين.')
    elif preset == 'yesterday':
        first = last = today - timedelta(days=1)
    elif preset == 'last7':
        first, last = today - timedelta(days=6), today
    elif preset == 'month':
        first, last = today.replace(day=1), today
    else:
        first = last = today
    if last < first or (last - first).days >= 93:
        raise db.error(422, 'PERIOD_INVALID', 'اختر فترة مرتبة لا تتجاوز 93 يومًا.')
    return first, last


def authorized_scopes(conn, user):
    # Authentication already reloads the session. Re-read its underlying role
    # and memberships inside the same snapshot as the data, including when the
    # request arrived with an already projected foreign-brand principal.
    original = conn.execute(
        'SELECT id,brand_id,branch_ids,role,active FROM users WHERE id=%s',
        (user['id'],),
    ).fetchone()
    if not original or not original['active'] or original['role'] != 'owner':
        raise db.error(403, 'OWNER_DASHBOARD_FORBIDDEN', 'لوحة الملاك متاحة للمالك فقط.')
    if central_operations.enabled():
        brands = multibrand.available(conn, original)
    else:
        # A normal brand session keeps its existing brand boundary. Central
        # aggregation is available only with the independently enabled rollout.
        brands = conn.execute(
            'SELECT id,name FROM brands WHERE id=%s AND active',
            (user['brand_id'],),
        ).fetchall()
    scopes = []
    for brand in brands:
        principal = multibrand.project(conn, original, brand['id'])
        if not principal or principal['role'] != 'owner':
            continue
        branches = conn.execute(
            'SELECT id,name,branch_no,city FROM branches WHERE brand_id=%s '
            'AND (%s OR id=ANY(%s)) ORDER BY branch_no,id',
            (brand['id'], not principal.get('home_brand_id'), principal.get('branch_ids', [])),
        ).fetchall()
        scopes.append({
            'brand': brand, 'principal': principal,
            'branches': [dict(branch, city=catalog.normalize_city(branch['city']),
                              brand_id=brand['id'], brand_name=brand['name']) for branch in branches],
        })
    if not scopes:
        raise db.error(403, 'BRAND_SCOPE', 'لا يوجد براند مخول لهذا الحساب.')
    return scopes


def driver_options(conn, branches):
    """Current membership plus historical effective tasks in permitted branches.

    Historic work follows the order's execution branch. Moving or disabling a
    driver today never transfers or hides previously authorized order results.
    All driver lookups are batched, and no personal contact data is published.
    """
    if not branches:
        return []
    bids = [branch['id'] for branch in branches]
    brands = sorted({branch['brand_id'] for branch in branches})
    membership = ''
    if multibrand.enabled():
        membership = (
            ' OR EXISTS(SELECT 1 FROM staff_brand_memberships membership '
            'WHERE membership.user_id=u.id AND membership.brand_id=b.brand_id '
            'AND membership.active AND membership.branch_ids ? b.id)'
        )
    current = conn.execute(
        "SELECT u.id,u.display_name,u.active,b.id branch_id,b.brand_id FROM users u "
        "JOIN branches b ON b.id=ANY(%s) AND b.brand_id=ANY(%s) "
        "WHERE u.role='driver' AND ((u.brand_id=b.brand_id AND u.branch_ids ? b.id)"
        + membership + ')',
        (bids, brands),
    ).fetchall()
    historic = conn.execute(
        'SELECT DISTINCT u.id,u.display_name,u.active,o.branch_id,o.brand_id '
        'FROM effective_driver_tasks t JOIN orders o ON o.id=t.order_id '
        'JOIN users u ON u.id=t.driver_id '
        'WHERE o.branch_id=ANY(%s) AND o.brand_id=ANY(%s) AND o.test_void IS NULL',
        (bids, brands),
    ).fetchall()
    output = {}
    for row in [*current, *historic]:
        driver = output.setdefault(row['id'], {
            'id': row['id'], 'name': row['display_name'], 'active': row['active'],
            'branch_ids': set(), 'brand_ids': set(),
        })
        driver['branch_ids'].add(row['branch_id'])
        driver['brand_ids'].add(row['brand_id'])
    return [dict(driver, branch_ids=sorted(driver['branch_ids']), brand_ids=sorted(driver['brand_ids']))
            for driver in sorted(output.values(), key=lambda row: (row['name'], row['id']))]


def financial_values(accounting_row):
    values = {key: int(accounting_row[source]) for key, source in FINANCIAL_FIELDS.items()}
    values['collected_halalas'] = values['cash_halalas'] + values['card_halalas']
    return values


def empty_summary():
    return dict(orders=0, completed=0, active=0, cancelled=0,
                complimentary_orders=0, complimentary_value_halalas=0,
                **financial_values(accounting.new_row('')))


def add_financial(target, values):
    for field in [*FINANCIAL_FIELDS, 'collected_halalas']:
        target[field] += values[field]


def add_order(target, order):
    if order['record_kind'] != 'order':
        return
    target['orders'] += 1
    target[order['status']] += 1
    if order['is_complimentary']:
        target['complimentary_orders'] += 1
        value = order['complimentary_value_halalas']
        if value is None:
            target['complimentary_value_halalas'] = None
        elif target['complimentary_value_halalas'] is not None:
            target['complimentary_value_halalas'] += value


def merge_product_rows(rows, brand, unallocated_refunds=0):
    """Explicit aliases join historical IDs; names alone never merge products."""
    grouped = {}
    fields = ('quantity', 'returned_quantity', 'gross', 'gift_quantity',
              'hospitality_quantity', 'advertising_quantity',
              'compensation_quantity', 'zero_value_quantity')
    for item in rows:
        pid = catalog.canonical_product_id(brand['id'], item['product_id'])
        target = grouped.setdefault(pid, dict(product_id=pid, label=item['label'],
            brand_id=brand['id'], brand_name=brand['name'],
            **{field: 0 for field in fields}))
        if item['product_id'] == pid:
            target['label'] = item['label']
        for field in fields:
            target[field] += item.get(field, 0)
    output = []
    for row in grouped.values():
        row['net_quantity'] = None if unallocated_refunds else row['quantity'] - row['returned_quantity']
        # Keep active zero-sales products discoverable; archived aliases have
        # already joined their canonical row and never duplicate quantities.
        output.append(row)
    return output


@router.get('')
def overview(
    response: Response,
    brand_id: str = Query(default='', max_length=200),
    period: Period = 'today',
    start: str = Query(default='', max_length=10),
    end: str = Query(default='', max_length=10),
    city: str = Query(default='', max_length=120),
    branch_id: str = Query(default='', max_length=200),
    driver_id: str = Query(default='', max_length=200),
    search: str = Query(default='', max_length=120),
    sort: Sort = 'created_desc',
    offset: int = Query(default=0, ge=0, le=1000000),
    limit: int = Query(default=25, ge=1, le=100),
    user=Depends(auth.require_roles('owner')),
):
    first, last = selected_period(period, start, end)
    city, search = catalog.normalize_city(city), search.strip()
    if '\x00' in search:
        raise db.error(422, 'SEARCH_INVALID', 'نص البحث غير صالح.')
    response.headers['Cache-Control'] = 'no-store'
    with db.tx() as conn:
        # Options, cards, chart and detail rows observe the same financial and
        # authorization snapshot. The dashboard cannot perform a business write.
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        scopes = authorized_scopes(conn, user)
        brands = [scope['brand'] for scope in scopes]
        selected_brand = brand_id or ('all' if central_operations.enabled() else user['brand_id'])
        if selected_brand != 'all' and selected_brand not in {brand['id'] for brand in brands}:
            raise db.error(403, 'BRAND_SCOPE', 'البراند خارج نطاق الحساب.')
        selected_scopes = [scope for scope in scopes
                           if selected_brand == 'all' or scope['brand']['id'] == selected_brand]
        all_branches = [branch for scope in selected_scopes for branch in scope['branches']]
        cities = sorted({branch['city'] for branch in all_branches if branch['city']})
        if city and city not in cities:
            raise db.error(403, 'CITY_SCOPE', 'المدينة خارج نطاق الفروع المتاحة.')
        branch_options = [branch for branch in all_branches if not city or branch['city'] == city]
        if branch_id and branch_id not in {branch['id'] for branch in branch_options}:
            raise db.error(403, 'BRANCH_SCOPE', 'الفرع خارج نطاق البراند والمدينة المختارين.')
        selected_branches = [branch for branch in branch_options if not branch_id or branch['id'] == branch_id]
        drivers = driver_options(conn, selected_branches)
        if driver_id and driver_id not in {driver['id'] for driver in drivers}:
            raise db.error(403, 'PERSON_SCOPE', 'السائق خارج نطاق الفروع المختارة.')

        summary = empty_summary()
        trend = {(first + timedelta(days=n)).isoformat(): empty_summary()
                 for n in range((last - first).days + 1)}
        breakdown, rows, product_rows = [], [], []
        unallocated_product_refunds = 0
        financial_completion_blocked = False
        branches_by_id = {branch['id']: branch for branch in selected_branches}
        from .aggregate_driver_closure import pending_financial_rows
        for scope in selected_scopes:
            brand = scope['brand']
            totals = dict(brand_id=brand['id'], brand_name=brand['name'], **empty_summary())
            breakdown.append(totals)
            bids = [branch['id'] for branch in selected_branches if branch['brand_id'] == brand['id']]
            if not bids:
                continue
            projection = accounting.build(
                conn, scope['principal'], first.isoformat(), last.isoformat(),
                branch_ids=','.join(bids), driver_id=driver_id,
                order_projection=True, reference_search=search,
            )
            item_report = accounting.build(
                conn, scope['principal'], first.isoformat(), last.isoformat(),
                branch_ids=','.join(bids), driver_id=driver_id, group='items',
                reference_search=search, item_projection=True,
            )
            unallocated_product_refunds += item_report['unallocated_refund_count']
            product_rows.extend(merge_product_rows(item_report['rows'], brand,
                                item_report['unallocated_refund_count']))
            financial_completion_blocked = bool(pending_financial_rows(
                conn, brand_id=brand['id'], branch_ids=bids, before=projection['period']['until'],
            )) or financial_completion_blocked
            for projected in projection['orders']:
                order = dict(projected)
                values = financial_values(order.pop('_accounting'))
                daily = order.pop('_accounting_days')
                branch = branches_by_id[order['branch_id']]
                order.update(values, brand_id=brand['id'], brand_name=brand['name'],
                             branch_name=branch['name'], city=branch['city'])
                for target in (summary, totals):
                    add_financial(target, values)
                    add_order(target, order)
                for day, posting in daily.items():
                    add_financial(trend[day], financial_values(posting))
                if order['record_kind'] == 'order':
                    add_order(trend[order['operational_date']], order)
                rows.append(order)

        # Cards and charts are complete before slicing; changing a page never
        # changes the population or the totals displayed above it.
        key = (lambda row: (row['value_halalas'], row['created_at'], row['id'])) if sort.startswith('value_') else (
            lambda row: (row['created_at'], row['id']))
        rows.sort(key=key, reverse=sort.endswith('_desc'))
        total = len(rows)
        page = rows[offset:offset + limit]
        next_offset = offset + limit if offset + limit < total else None
        name = 'كل البراندات المخولة' if selected_brand == 'all' else next(
            brand['name'] for brand in brands if brand['id'] == selected_brand)
        result = safe({
            'schema_version': 1,
            'brand': {'id': selected_brand, 'name': name},
            'period': {'preset': period, 'start': first, 'end': last, 'timezone': 'Asia/Riyadh',
                       'basis': 'manager_shift', 'fallback_start': '04:00', 'definition': DEFINITION},
            'filters': {'brand_id': selected_brand, 'city': city, 'branch_id': branch_id,
                        'driver_id': driver_id, 'search': search, 'sort': sort},
            'options': {'brands': brands, 'cities': [{'id': name, 'name': name} for name in cities],
                        'branches': branch_options, 'drivers': drivers},
            'summary': summary,
            'status_breakdown': [{'status': status, 'count': summary[status]}
                                 for status in ('completed', 'active', 'cancelled')],
            'trend': [dict(day=day, **values) for day, values in trend.items()],
            'brand_breakdown': breakdown,
            'product_sales': sorted(product_rows, key=lambda row: (-row['quantity'], row['label'], row['product_id'])),
            'product_sales_unallocated_refunds': unallocated_product_refunds,
            'rows': page,
            'pagination': {'offset': offset, 'limit': limit, 'total': total, 'next_offset': next_offset},
            'generated_at': datetime.now(service_hours.RIYADH),
            'scope_note': SCOPE_NOTE,
            'financial_completion_blocked': financial_completion_blocked,
        })
    # A report may take longer than a grant change. Recheck in a new database
    # snapshot, not the repeatable-read snapshot used for the financial totals.
    # Refuse to return either option metadata or money from a revoked scope.
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        fresh = {scope['brand']['id']: {branch['id'] for branch in scope['branches']}
                 for scope in authorized_scopes(conn, user)}
        if any(scope['brand']['id'] not in fresh or
               not {branch['id'] for branch in scope['branches']} <= fresh[scope['brand']['id']]
               for scope in scopes):
            raise db.error(403, 'DASHBOARD_SCOPE_CHANGED', 'تغير نطاق الحساب أثناء تحميل اللوحة؛ حدّث الصفحة.')
    return result
