"""Read-only online/gift reporting over canonical orders and permanent claims.

Dates are Riyadh calendar dates, not accounting closure dates. This report never
releases a gift claim, edits an order, or replaces an immutable shift report.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Response
from . import auth, db

router = APIRouter(prefix='/api/reports/promotions', tags=['campaign-reporting'])
RIYADH = ZoneInfo('Asia/Riyadh')


def period(start: date, end: date):
    if end == date.max or end < start or (end-start).days > 92:
        raise db.error(422, 'REPORT_PERIOD_INVALID', 'اختر فترة لا تتجاوز 93 يومًا وبنهاية بعد البداية.')
    return datetime.combine(start, time.min, RIYADH), datetime.combine(end+timedelta(days=1), time.min, RIYADH)


def report_scope(conn, user, branch_id):
    if user['role'] not in {'owner', 'delivery_manager'} or not auth.has_permission(user, 'reports_view'):
        raise db.error(403, 'REPORT_FORBIDDEN', 'التقرير متاح للمالك ومدير التوصيل المخول فقط.')
    # Morning duty has stricter shift-specific history rules. A branch-wide
    # marketing report must not broaden those permissions.
    if user.get('_morning_manager_context'):
        raise db.error(403, 'REPORT_FORBIDDEN', 'استخدم تقرير المناوبة ضمن نطاق شفتك الحالي.')
    rows = conn.execute('SELECT id,name FROM branches WHERE brand_id=%s ORDER BY branch_no', (user['brand_id'],)).fetchall()
    allowed = {row['id'] for row in rows if user['role']=='owner' or row['id'] in user.get('branch_ids', [])}
    if branch_id and branch_id not in allowed:
        raise db.error(404, 'BRANCH_NOT_FOUND', 'الفرع غير موجود ضمن صلاحياتك.')
    return [row for row in rows if row['id'] in allowed and (not branch_id or row['id']==branch_id)]


# The historical customer key is canonical Saudi mobile when possible, with a
# private customer-id fallback for records without a valid mobile. It is never
# returned. Current normalized orders need no data correction/deduplication.
BASE = r"""
WITH scoped_orders AS (
 SELECT o.*,regexp_replace(translate(o.customer->>'phone','٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹','01234567890123456789'),
        U&'[[:space:]()\200B-\200F\202A-\202E\2066-\2069\FEFF-]', '', 'g') compact_phone
 FROM orders o WHERE o.brand_id=%s AND o.branch_id=ANY(%s)
   AND o.snapshot->>'order_source' IS DISTINCT FROM 'delivery_center'
   AND o.test_void IS NULL AND o.created_at<%s
), history AS (
 SELECT o.*,coalesce(normalized_identity_phone(CASE
        WHEN compact_phone ~ '^5[0-9]{8}$' THEN '+966'||compact_phone
        WHEN compact_phone ~ '^9665[0-9]{8}$' THEN '+'||compact_phone
        ELSE compact_phone END),'customer:'||o.customer_id) customer_key,
        (o.created_at AT TIME ZONE 'Asia/Riyadh')::date report_date,
        (o.preparation_status IN ('cancelled','rejected') OR o.delivery_status='cancelled') cancelled
 FROM scoped_orders o
), first_orders AS (
 SELECT DISTINCT ON (customer_key) customer_key,id first_order_id
 FROM history WHERE NOT cancelled ORDER BY customer_key,created_at,id
), selected AS (
 SELECT h.*,f.first_order_id,g.policy_version,g.claimed_at,
        CASE WHEN g.phone IS NULL THEN NULL ELSE '••••'||right(g.phone,4) END phone_suffix
 FROM history h LEFT JOIN first_orders f USING(customer_key)
 LEFT JOIN first_order_gift_claims g ON g.order_id=h.id AND g.brand_id=h.brand_id
 WHERE h.created_at>=%s
)
"""


@router.get('/daily')
def daily_report(start: date, end: date, response: Response,
                 branch_id: str | None = Query(default=None, min_length=1, max_length=80),
                 policy_version: int | None = Query(default=None, ge=1),
                 user=Depends(auth.current_user)):
    start_at, end_at = period(start, end)
    response.headers['Cache-Control'] = 'private, no-store'
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        branches = report_scope(conn, user, branch_id)
        branch_ids = [row['id'] for row in branches]
        args = (user['brand_id'], branch_ids, end_at, start_at)
        # An optional version filters gift metrics only: online totals must not
        # silently become a count of claimants when reviewing a campaign.
        claim_filter = 'policy_version IS NOT NULL' + (' AND policy_version=%s' if policy_version else '')
        totals = """count(*) online_orders,
          count(*) FILTER (WHERE NOT cancelled) non_cancelled_orders,
          count(*) FILTER (WHERE cancelled) cancelled_orders,
          count(*) FILTER (WHERE NOT cancelled AND (delivery_status='delivered' OR preparation_status='completed')) completed_orders,
          count(DISTINCT customer_key) FILTER (WHERE NOT cancelled) unique_customers,
          count(DISTINCT customer_key) FILTER (WHERE NOT cancelled AND id=first_order_id) first_time_customers,
          count(DISTINCT customer_key) FILTER (WHERE NOT cancelled AND id<>first_order_id) repeat_customers,
          count(*) FILTER (WHERE """+claim_filter+""") gift_claims,
          count(*) FILTER (WHERE ("""+claim_filter+""") AND NOT cancelled) non_cancelled_gift_orders,
          count(*) FILTER (WHERE ("""+claim_filter+""") AND cancelled) cancelled_gift_orders"""
        query_args = (*args, *((policy_version,)*3 if policy_version else ()))
        summary = conn.execute(BASE+'SELECT '+totals+' FROM selected', query_args).fetchone()
        daily = conn.execute(BASE+'SELECT report_date,'+totals+' FROM selected GROUP BY report_date ORDER BY report_date', query_args).fetchall()
        db.audit(conn, user['id'], 'campaign.report.read', 'brand', user['brand_id'],
                 {'start':start.isoformat(), 'end':end.isoformat(), 'branch_ids':branch_ids, 'policy_version':policy_version})
    return {'brand_id':user['brand_id'], 'timezone':'Asia/Riyadh', 'date_basis':'calendar_day',
            'start':start.isoformat(), 'end':end.isoformat(), 'branches':branches,
            'policy_version':policy_version, 'customer_history_scope':'brand' if user['role']=='owner' and not branch_id else 'selected_branches',
            'definitions':{'online_orders':'Customer-entered orders; staff delivery-center orders and owner-voided tests excluded.',
                           'first_time_customers':'First non-cancelled online order in the visible branch history.',
                           'repeat_customers':'Another non-cancelled online order exists earlier in the visible branch history; a customer may be first and repeat within one period.',
                           'gift_claims':'Permanent gift redemptions at order creation; cancellation never restores eligibility.'},
            'summary':summary, 'daily':daily}


@router.get('/beneficiaries')
def beneficiaries(start: date, end: date, response: Response,
                  branch_id: str | None = Query(default=None, min_length=1, max_length=80),
                  policy_version: int | None = Query(default=None, ge=1),
                  offset: int = Query(default=0, ge=0, le=100000),
                  limit: int = Query(default=50, ge=1, le=100),
                  user=Depends(auth.current_user)):
    start_at, end_at = period(start, end)
    response.headers['Cache-Control'] = 'private, no-store'
    with db.tx() as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        branches = report_scope(conn, user, branch_id)
        branch_ids = [row['id'] for row in branches]
        args = (user['brand_id'], branch_ids, end_at, start_at)
        where = 'policy_version IS NOT NULL'
        if policy_version:
            where += ' AND policy_version=%s'
            args += (policy_version,)
        rows = conn.execute(BASE+"""SELECT id order_id,order_no,branch_id,report_date,created_at,claimed_at,
            policy_version,phone_suffix,cancelled,payment_status,
            (id=first_order_id) first_online_order
            FROM selected WHERE """+where+' ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s', (*args,limit,offset)).fetchall()
        db.audit(conn, user['id'], 'campaign.beneficiaries.read', 'brand', user['brand_id'],
                 {'start':start.isoformat(),'end':end.isoformat(),'branch_ids':branch_ids,'policy_version':policy_version,'offset':offset,'returned':len(rows)})
    return {'brand_id':user['brand_id'],'timezone':'Asia/Riyadh','date_basis':'calendar_day',
            'beneficiaries':rows,'offset':offset,'limit':limit,'phone_display':'masked_suffix_only'}
