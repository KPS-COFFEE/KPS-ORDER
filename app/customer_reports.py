"""Owner customer cohorts and retention over the canonical CRM purchase ledger.

Read-only: no orders, identities, gifts, consent or marketing are changed here.
Calendar dates are Riyadh dates. Historical results reflect current completion
and refund evidence, rather than pretending to be immutable accounting closes.
"""
from datetime import date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from . import auth, customer_crm, db

router = APIRouter(tags=['customer-reports'])
RIYADH = ZoneInfo('Asia/Riyadh')
POLICY = {'version': 1, 'minimum_purchase_days': 5, 'recent_gaps': 12,
          'late_multiple': 2, 'late_minimum_days': 7,
          'lapsed_multiple': 3, 'lapsed_minimum_days': 14,
          'irregular_quartile_multiple': 3}


def report_filters(start: date | None = None, end: date | None = None,
                   q: str = Query(default='', max_length=100),
                   segment: Literal['all', 'new', 'repeat', 'inactive'] = 'all',
                   activity: Literal['all', 'regular', 'late', 'lapsed', 'insufficient', 'irregular', 'no_repeat'] = 'all',
                   sort: Literal['orders', 'value', 'absence'] = 'orders',
                   offset: int = Query(default=0, ge=0, le=1000000),
                   limit: int = Query(default=50, ge=1, le=100)):
    today = datetime.now(RIYADH).date()
    end = end or today
    start = start or end.replace(day=1)
    if start > end or end > today or (end-start).days > 365:
        raise db.error(422, 'CUSTOMER_REPORT_PERIOD', 'اختر فترة صحيحة حتى اليوم، وبحد أقصى سنة.')
    return dict(start=start, end=end, q=q.strip(), segment=segment, activity=activity,
                sort=sort, offset=offset, limit=limit)


# Reuse the verified identity, test-data cutoff and actual net collection rules.
# History is deliberately calculated BEFORE filtering the requested date range.
FACTS = """, report_parameters AS (
 SELECT %s::date AS start_date,%s::date AS end_date,
 (%s::date::timestamp AT TIME ZONE 'Asia/Riyadh') AS starts_at,
 ((%s::date+1)::timestamp AT TIME ZONE 'Asia/Riyadh') AS ends_at
), history AS (
 SELECT q.*,(q.created_at AT TIME ZONE 'Asia/Riyadh')::date AS order_date,
 row_number() OVER(PARTITION BY q.canonical_id ORDER BY q.created_at,q.id) AS purchase_number
 FROM qualified q,report_parameters p WHERE q.created_at<p.ends_at AND q.created_at<=now()
), lifetime AS (
 SELECT canonical_id,count(*) AS lifetime_orders,
 sum(net_spending_halalas)::bigint AS lifetime_value_halalas,
 min(created_at) AS first_order_at,max(created_at) AS latest_order_at,
 count(DISTINCT order_date) AS purchase_days
 FROM history GROUP BY canonical_id
), purchase_days AS (
 SELECT canonical_id,order_date,
 row_number() OVER(PARTITION BY canonical_id ORDER BY order_date DESC) AS position,
 order_date-lead(order_date) OVER(PARTITION BY canonical_id ORDER BY order_date DESC) AS gap
 FROM (SELECT DISTINCT canonical_id,order_date FROM history) d
), cadence AS (
 SELECT canonical_id,percentile_cont(0.5) WITHIN GROUP(ORDER BY gap) AS usual_gap_days,
 percentile_cont(0.25) WITHIN GROUP(ORDER BY gap) AS gap_p25,
 percentile_cont(0.75) WITHIN GROUP(ORDER BY gap) AS gap_p75
 FROM purchase_days WHERE position<=12 AND gap>0 GROUP BY canonical_id
), period_orders AS (
 SELECT h.* FROM history h,report_parameters p WHERE h.created_at>=p.starts_at
), period_stats AS (
 SELECT canonical_id,count(*) AS orders,sum(net_spending_halalas)::bigint AS value_halalas
 FROM period_orders GROUP BY canonical_id
), customer_facts AS (
 SELECT v.id,v.display_name,v.phone,l.*,
 COALESCE(s.orders,0) AS orders,COALESCE(s.value_halalas,0)::bigint AS value_halalas,
 p.end_date-(l.latest_order_at AT TIME ZONE 'Asia/Riyadh')::date AS days_absent,
 c.usual_gap_days,
 CASE WHEN s.orders IS NULL THEN 'inactive'
 WHEN l.first_order_at>=p.starts_at THEN 'new' ELSE 'repeat' END AS segment,
 CASE WHEN l.lifetime_orders=1 THEN 'no_repeat'
 WHEN l.purchase_days<5 OR c.usual_gap_days IS NULL THEN 'insufficient'
 WHEN c.gap_p75>3*c.gap_p25 THEN 'irregular'
 WHEN p.end_date-(l.latest_order_at AT TIME ZONE 'Asia/Riyadh')::date>=GREATEST(14,3*c.usual_gap_days) THEN 'lapsed'
 WHEN p.end_date-(l.latest_order_at AT TIME ZONE 'Asia/Riyadh')::date>=GREATEST(7,2*c.usual_gap_days) THEN 'late'
 ELSE 'regular' END AS activity
 FROM verified v JOIN lifetime l ON l.canonical_id=v.id
 LEFT JOIN period_stats s ON s.canonical_id=v.id
 LEFT JOIN cadence c ON c.canonical_id=v.id CROSS JOIN report_parameters p
), matching AS (
 SELECT * FROM customer_facts WHERE
 (%s='' OR display_name ILIKE %s ESCAPE '!' OR phone ILIKE %s ESCAPE '!')
 AND (%s='all' OR segment=%s) AND (%s='all' OR activity=%s)
)
"""
SORTS = {'orders': 'orders DESC,value_halalas DESC,id',
         'value': 'value_halalas DESC,orders DESC,id',
         'absence': 'days_absent DESC,lifetime_orders DESC,id'}


def require_complete_brand_scope(c, user):
    """Brand-wide cohorts need the complete history, not one branch grant.

    A foreign-brand owner retains explicit branch scope. Reporting a partial
    history would both expose other branches' customers and mislabel returning
    customers as new, so allow this report only with every branch granted.
    """
    if not user.get('home_brand_id'):
        return
    branches = {row['id'] for row in c.execute(
        'SELECT id FROM branches WHERE brand_id=%s', (user['brand_id'],)).fetchall()}
    if not branches or not branches <= set(user.get('branch_ids') or []):
        raise db.error(403, 'CUSTOMER_REPORT_BRAND_SCOPE_REQUIRED',
                       'تقرير العملاء الشامل يتطلب صلاحية جميع فروع البراند. / Full brand branch access is required for customer cohorts.')


def parameters(user, filters):
    q = filters['q'].replace('!', '!!').replace('%', '!%').replace('_', '!_')
    return (*customer_crm.arguments(user), filters['start'], filters['end'],
            filters['start'], filters['end'], filters['q'], '%'+q+'%', '%'+q+'%',
            filters['segment'], filters['segment'], filters['activity'], filters['activity'])


def definitions():
    return dict(customer_crm.definitions(),
        new_customer='First qualifying purchase date falls inside the selected period; registration is not a purchase.',
        repeat_customer='Purchased in the selected period with a qualifying purchase before its start.',
        daily_new='First qualifying purchase falls on this Riyadh calendar date. Further same-day orders remain with the new-customer cohort.',
        daily_repeat='Purchased on this date and first purchased on an earlier date. Each customer counted once per day.',
        repeat_orders='All orders after the first qualifying order, including repeat orders on the first day.',
        period='Riyadh calendar dates, inclusive; not the 16:00 operational accounting day.',
        ranking='Selected-period orders and actual net value; zero-period buyers remain available for retention follow-up.',
        history='Known certified operating history up to period end. Current completion/refund evidence; not an immutable historical close.',
        retention='Indicative absence, not proof of churn. Median of up to 12 recent gaps between distinct purchase days; at least 5 purchase days.',
        brand='Only the authorized selected brand; an inactive customer here may still buy from another brand.',
        authorization='A foreign-brand owner must have every brand branch granted; partial branch history cannot establish brand-wide cohorts.',
        campaigns='Read-only audience preparation. No messages, coupons, gifts or marketing consent are created.')


@router.get('/api/reports/customers')
def customer_report(response: Response, filters=Depends(report_filters),
                    user=Depends(auth.require_roles('owner'))):
    response.headers['Cache-Control'] = 'no-store'
    order = SORTS[filters['sort']]
    query = customer_crm.BASE + FACTS + """
 SELECT (SELECT jsonb_build_object(
 'known_customers',count(*),'purchasing_customers',count(*) FILTER(WHERE orders>0),
 'new_customers',count(*) FILTER(WHERE segment='new'),
 'repeat_customers',count(*) FILTER(WHERE segment='repeat'),
 'inactive_customers',count(*) FILTER(WHERE orders=0),
 'orders',COALESCE(sum(orders),0),'value_halalas',COALESCE(sum(value_halalas),0),
 'late_customers',count(*) FILTER(WHERE activity='late'),
 'lapsed_customers',count(*) FILTER(WHERE activity='lapsed')) FROM matching) AS summary,
 (SELECT COALESCE(jsonb_agg(r ORDER BY r.rank),'[]'::jsonb) FROM (
 SELECT row_number() OVER(ORDER BY """ + order + """) AS rank,* FROM matching
 ORDER BY """ + order + """ LIMIT %s OFFSET %s) r) AS customers,
 (SELECT COALESCE(jsonb_agg(d ORDER BY d.day),'[]'::jsonb) FROM (
 SELECT p.start_date+series.n AS day,
 count(DISTINCT o.canonical_id) AS purchasing_customers,
 count(DISTINCT o.canonical_id) FILTER(WHERE (m.first_order_at AT TIME ZONE 'Asia/Riyadh')::date=o.order_date) AS new_customers,
 count(DISTINCT o.canonical_id) FILTER(WHERE (m.first_order_at AT TIME ZONE 'Asia/Riyadh')::date<o.order_date) AS repeat_customers,
 count(o.id) AS orders,COALESCE(sum(o.net_spending_halalas),0)::bigint AS value_halalas,
 count(o.id) FILTER(WHERE o.purchase_number>1) AS repeat_orders
 FROM report_parameters p CROSS JOIN LATERAL generate_series(0,p.end_date-p.start_date) series(n)
 LEFT JOIN (period_orders o JOIN matching m ON m.id=o.canonical_id)
 ON o.order_date=p.start_date+series.n
 GROUP BY p.start_date,series.n) d) AS daily,
 (SELECT jsonb_build_object('started_at',period_start,'period_valid',period_valid,
 'configured',settings ? 'operational_period_started_at') FROM crm_scope) AS operating_period
 """
    with db.tx() as c:
        # Check the branch grant against the very same snapshot used for the
        # report, including when a new branch is created concurrently.
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        # One statement gives summary, every day and the sorted page one MVCC snapshot.
        c.execute("SET LOCAL statement_timeout='8s'")
        require_complete_brand_scope(c, user)
        result = c.execute(query, (*parameters(user, filters), filters['limit'], filters['offset'])).fetchone()
    total = result['summary']['known_customers']
    return dict(result, brand_id=user['brand_id'], start=filters['start'], end=filters['end'],
                filters=filters, policy=POLICY, definitions=definitions(),
                next_offset=filters['offset']+filters['limit'] if filters['offset']+filters['limit']<total else None,
                generated_at=datetime.now(RIYADH))


@router.get('/customer-report', include_in_schema=False)
def report_page(user=Depends(auth.require_roles('owner'))):
    if user.get('home_brand_id'):
        with db.tx() as c:
            require_complete_brand_scope(c, user)
    return FileResponse(Path(__file__).resolve().parents[1] / 'public/customer-report.html',
                        headers={'Cache-Control': 'no-store'})
