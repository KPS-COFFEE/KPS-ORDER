"""Read-only geographic comparison; never a dispatch or printing restriction."""
from datetime import date

from fastapi import APIRouter, Depends, Response

from . import accounting, auth, catalog, db, service_hours
from .operations import safe

router = APIRouter(prefix='/api/branch-proximity', tags=['branch-proximity'])


def comparison(point, source, branches):
    city = catalog.normalize_city(source.get('city'))
    ranked = []
    if catalog.valid_location(point) and city:
        for branch in branches:
            if (branch['brand_id'] == source['brand_id'] and branch.get('active', True)
                    and catalog.normalize_city(branch.get('city')) == city
                    and catalog.location_verified(branch)):
                ranked.append((catalog.direct_distance(point, branch['settings']['location']), branch['id'], branch))
    ranked.sort(key=lambda item: (item[0], item[1]))
    nearest = ranked[0] if ranked else None
    source_distance = (catalog.direct_distance(point, source['settings']['location'])
                       if catalog.valid_location(point) and catalog.location_verified(source) else None)
    return {
        'distance_to_customer_meters': round(source_distance, 1) if source_distance is not None else None,
        'nearest_branch_id': nearest[2]['id'] if nearest else None,
        'nearest_branch_name': nearest[2]['name'] if nearest else None,
        'nearest_distance_meters': round(nearest[0], 1) if nearest else None,
        'is_nearest': abs(source_distance - nearest[0]) < 1 if nearest and source_distance is not None else None,
    }


@router.get('')
def report(response: Response, day: date | None = None, user=Depends(auth.current_user)):
    if user['role'] not in {'owner', 'delivery_manager'} or not auth.has_permission(user, 'reports_view'):
        raise db.error(403, 'PROXIMITY_REPORT_FORBIDDEN', 'التقرير متاح للمدير والمالك المخولين فقط.')
    if not auth.has_permission(user, 'orders_view'):
        raise db.error(403, 'PROXIMITY_REPORT_FORBIDDEN', 'صلاحية عرض الطلبات مطلوبة.')
    day = day or service_hours.delivery_operational_date()
    response.headers['Cache-Control'] = 'private, no-store'
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        # Use the same shift-owned date as accounting, including extended duties.
        projection = accounting.build(conn, user, day.isoformat(), day.isoformat(), order_projection=True)
        ids = [row['id'] for row in projection['orders'] if row['record_kind'] == 'order']
        branches = conn.execute('SELECT id,brand_id,name,branch_no,city,settings FROM branches '
                                'WHERE brand_id=%s AND id=ANY(%s)',
                                (user['brand_id'], [b['id'] for b in projection['branches']])).fetchall()
        by_id = {branch['id']: branch for branch in branches}
        from .morning_manager_context import order_filter
        morning_clause, morning_args = order_filter(user)
        orders = conn.execute("""SELECT o.id,o.order_no,o.snapshot->>'order_reference' reference,
            o.customer->>'neighborhood' neighborhood,o.customer->'location' location,o.branch_id,
            coalesce(r.target_branch_id,o.branch_id) preparation_branch_id,
            p.branch_id print_branch_id,p.status print_status
            FROM orders o LEFT JOIN order_preparation_routes r
                ON r.id=o.preparation_route_id AND r.order_id=o.id AND r.brand_id=o.brand_id
            LEFT JOIN LATERAL (SELECT j.branch_id,j.status FROM branch_print_jobs j
                WHERE j.order_id=o.id AND j.brand_id=o.brand_id AND j.kind='order' AND j.status<>'cancelled'
                ORDER BY j.created_at DESC,j.id DESC LIMIT 1) p ON true
            WHERE o.id=ANY(%s) AND o.brand_id=%s AND o.service='delivery' AND o.test_void IS NULL
            AND """ + morning_clause + " ORDER BY o.created_at DESC,o.id", (ids, user['brand_id'], *morning_args)).fetchall() if ids else []
        rows = []
        for order in orders:
            source = by_id[order['branch_id']]
            preparation = by_id.get(order['preparation_branch_id'])
            printed = by_id.get(order['print_branch_id'])
            rows.append({
                'id': order['id'], 'brand_id': user['brand_id'],
                'reference': order['reference'] or str(order['order_no']),
                'neighborhood': order['neighborhood'], 'city': source['city'],
                'order_branch_name': source['name'],
                'preparation_branch_name': preparation['name'] if preparation else None,
                'print_branch_name': printed['name'] if printed else None,
                'print_status': order['print_status'],
                **(comparison(order['location'], preparation, branches) if preparation else {
                    'distance_to_customer_meters':None, 'nearest_branch_id':None, 'nearest_branch_name':None,
                    'nearest_distance_meters':None, 'is_nearest':None}),
            })
    return safe({'day': day, 'rows': rows,
        'note': 'المسافات مباشرة تقريبية وفق الدبابيس المعتمدة الحالية، وليست مسافة طريق. '
                'الأقرب جغرافيًا ضمن براند ومحافظة وصلاحيات التقرير؛ لا يثبت أهلية الأصناف أو التغطية وقت الطلب. '
                'وجهة الطباعة هي آخر أمر طباعة للفاتورة مع حالته، وليست إثبات خروج الطلب أو الورقة. '
                'اليوم يتبع شفت مدير التوصيل كما في المحاسبة.'})
