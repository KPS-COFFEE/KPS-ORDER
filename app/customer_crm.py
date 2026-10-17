"""Owner-only CRM read models over existing verified canonical identities.

No phone matching, identity mutation, background geolocation or marketing grants.
All currency values are integer halalas; source snapshots remain unchanged.
"""
from pathlib import Path
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from . import auth, db

router = APIRouter(tags=['customer-crm'])
MIN_AREA_CUSTOMERS = 5

# Secure account-claim links are the only aliases. Never join by a typed phone.
BASE = """
WITH verified AS (
 SELECT id,brand_id,display_name,phone,phone_verified_at,email,email_verified_at,created_at
 FROM users WHERE brand_id=%s AND role='customer' AND phone_verified_at IS NOT NULL
 AND (phone_sms_verified_at IS NOT NULL OR phone_whatsapp_verified_at IS NOT NULL)
 AND NOT EXISTS(SELECT 1 FROM customer_identity_links l WHERE l.source_id=users.id AND l.brand_id=users.brand_id)
), identities AS (
 SELECT id AS source_id,id AS customer_id FROM verified
 UNION
 SELECT l.source_id,l.customer_id FROM customer_identity_links l
 JOIN verified v ON v.id=l.customer_id AND v.brand_id=l.brand_id
 JOIN users s ON s.id=l.source_id AND s.brand_id=l.brand_id AND s.role='customer'
), order_scope AS (
 SELECT o.id,o.branch_id,o.created_at,o.service,o.preparation_status,o.delivery_status,o.total_halalas,
 o.snapshot->>'branch_payment_mode' AS branch_payment_mode,i.customer_id AS canonical_id,
 CASE WHEN b.id IS NOT NULL THEN b.id ELSE o.branch_id END AS selected_branch_id,
 NULLIF(btrim(o.customer->>'city'),'') AS customer_city,
 NULLIF(btrim(o.customer->>'neighborhood'),'') AS neighborhood
 FROM orders o JOIN identities i ON i.source_id=o.customer_id
 LEFT JOIN branches b ON b.id=o.snapshot->'internal_routing'->'selected_branch'->>'branch_id'
 AND b.brand_id=o.brand_id
 WHERE o.brand_id=%s
), ledger AS (
 SELECT x.order_id,x.amount_halalas FROM collections x JOIN order_scope o ON o.id=x.order_id
 UNION ALL
 SELECT x.order_id,x.amount_halalas FROM branch_online_receipts x JOIN order_scope o ON o.id=x.order_id
 WHERE x.disposition='applied' AND x.mode='live'
 UNION ALL
 SELECT x.order_id,-x.amount_halalas FROM refunds x JOIN order_scope o ON o.id=x.order_id
 UNION ALL
 SELECT x.order_id,-x.amount_halalas FROM branch_online_refunds x JOIN order_scope o ON o.id=x.order_id
 WHERE x.status='verified'
), net_collections AS (
 SELECT order_id,sum(amount_halalas) AS net FROM ledger GROUP BY order_id
), scoped AS (
 SELECT o.*,LEAST(o.total_halalas,GREATEST(0,COALESCE(n.net,0)))::bigint AS net_spending_halalas
 FROM order_scope o LEFT JOIN net_collections n ON n.order_id=o.id
), qualified AS (
 SELECT * FROM scoped WHERE preparation_status NOT IN ('cancelled','rejected') AND delivery_status NOT IN ('cancelled','rejected')
 AND ((service='delivery' AND delivery_status='delivered')
 OR (service IN ('pickup','car') AND preparation_status='completed'))
 AND net_spending_halalas>0 AND COALESCE(branch_payment_mode,'')<>'test'
)
"""


def arguments(user):
    return (user['brand_id'], user['brand_id'])


def definitions():
    return {'currency': 'SAR', 'money_unit': 'halala', 'minimum_area_customers': MIN_AREA_CUSTOMERS,
            'identity': 'SMS/WhatsApp verified canonical customer and secure account-claim aliases; never phone matching',
            'orders': 'Completed, non-cancelled orders with positive actual net collection; fully refunded orders excluded',
            'spending': 'Actual collections plus applied live online receipts minus confirmed refunds, capped at order total; sandbox orders excluded',
            'geography': 'Verified purchasing customers by explicit order city/neighborhood only; no inferred city, raw pins or background tracking',
            'scope': 'Canonical historical data; historical test records remain until a separately certified safe reset',
            'marketing_consent': 'Not inferred from registration; this read model grants no marketing consent'}


@router.get('/api/crm/overview')
def overview(response: Response, user=Depends(auth.require_roles('owner'))):
    response.headers['Cache-Control'] = 'no-store'
    with db.tx() as c:
        totals = c.execute(BASE + """SELECT (SELECT count(*) FROM verified) AS customers,
          count(*) AS qualifying_orders,count(DISTINCT canonical_id) AS purchasing_customers,
          COALESCE(sum(net_spending_halalas),0)::bigint AS net_spending_halalas,
          (SELECT count(*) FROM (SELECT canonical_id FROM qualified GROUP BY canonical_id HAVING count(*)>1) r) AS repeat_customers
          FROM qualified""", arguments(user)).fetchone()
        # Suppress the entire small-area cell, including branch and order count.
        areas = c.execute(BASE + """SELECT customer_city AS city,neighborhood,
          count(DISTINCT canonical_id) AS customers,count(*) AS completed_orders,
          (array_agg(selected_branch_id ORDER BY branch_count DESC,selected_branch_id))[1] AS most_requested_branch_id
          FROM (SELECT q.*,count(*) OVER(PARTITION BY customer_city,neighborhood,selected_branch_id) AS branch_count
          FROM qualified q WHERE customer_city IS NOT NULL OR neighborhood IS NOT NULL) a
          GROUP BY customer_city,neighborhood HAVING count(DISTINCT canonical_id)>=%s
          ORDER BY count(*) DESC,customer_city,neighborhood LIMIT 100""", (*arguments(user), MIN_AREA_CUSTOMERS)).fetchall()
        branches = c.execute(BASE + """SELECT branch_id,count(*) AS completed_orders,
          count(DISTINCT canonical_id) AS customers,COALESCE(sum(net_spending_halalas),0)::bigint AS net_spending_halalas
          FROM qualified GROUP BY branch_id ORDER BY completed_orders DESC,branch_id""", arguments(user)).fetchall()
    return {'totals': totals, 'areas': areas, 'branches': branches, 'definitions': definitions()}


@router.get('/api/crm/customers')
def customers(response: Response, after: str = Query(default='', max_length=150),
              limit: int = Query(default=50, ge=1, le=100), user=Depends(auth.require_roles('owner'))):
    response.headers['Cache-Control'] = 'no-store'
    with db.tx() as c:
        rows = c.execute(BASE + """, customer_stats AS (
          SELECT canonical_id,count(*) AS qualifying_orders,sum(net_spending_halalas)::bigint AS net_spending_halalas,
          min(created_at) AS first_order_at,max(created_at) AS latest_order_at
          FROM qualified GROUP BY canonical_id
          ) SELECT v.*,COALESCE(q.qualifying_orders,0) AS qualifying_orders,
          COALESCE(q.net_spending_halalas,0)::bigint AS net_spending_halalas,q.first_order_at,q.latest_order_at
          FROM verified v LEFT JOIN customer_stats q ON q.canonical_id=v.id
          WHERE v.id>%s ORDER BY v.id LIMIT %s""", (*arguments(user), after, limit + 1)).fetchall()
    return {'customers': rows[:limit], 'next_after': rows[limit-1]['id'] if len(rows)>limit else None,
            'definitions': definitions()}


@router.get('/api/crm/customers/{customer_id}')
def customer_detail(customer_id: str, response: Response, user=Depends(auth.require_roles('owner'))):
    response.headers['Cache-Control'] = 'no-store'
    with db.tx() as c:
        profile = c.execute(BASE + 'SELECT * FROM verified WHERE id=%s', (*arguments(user), customer_id)).fetchone()
        if not profile:
            raise db.error(404, 'CUSTOMER_NOT_FOUND', 'العميل غير موجود في النطاق المصرح.')
        branches = c.execute(BASE + """SELECT selected_branch_id,branch_id AS fulfilled_branch_id,
          service,count(*) AS qualifying_orders,max(created_at) AS latest_order_at,
          sum(net_spending_halalas)::bigint AS net_spending_halalas
          FROM qualified WHERE canonical_id=%s GROUP BY selected_branch_id,branch_id,service
          ORDER BY qualifying_orders DESC,selected_branch_id,branch_id,service""", (*arguments(user), customer_id)).fetchall()
        areas = c.execute(BASE + """SELECT customer_city AS city,neighborhood,count(*) AS qualifying_orders
          FROM qualified WHERE canonical_id=%s AND (customer_city IS NOT NULL OR neighborhood IS NOT NULL)
          GROUP BY customer_city,neighborhood ORDER BY qualifying_orders DESC,customer_city,neighborhood""", (*arguments(user), customer_id)).fetchall()
        addresses = c.execute("""SELECT a.id,a.label,a.address,a.neighborhood,a.updated_at
          FROM customer_addresses a WHERE a.brand_id=%s AND a.customer_id=%s ORDER BY a.updated_at DESC LIMIT 50""", (user['brand_id'], customer_id)).fetchall()
        conversations = c.execute(BASE + """SELECT x.id,x.category,x.status,x.route,x.created_at
          FROM conversations x JOIN identities i ON i.source_id=x.customer_id
          WHERE x.brand_id=%s AND i.customer_id=%s ORDER BY x.created_at DESC LIMIT 50""", (*arguments(user), user['brand_id'], customer_id)).fetchall()
    selected, fulfilled, channels = {}, {}, {}
    for row in branches:
        n = row['qualifying_orders']
        for counts, key in ((selected, row['selected_branch_id']), (fulfilled, row['fulfilled_branch_id']), (channels, row['service'])):
            counts[key] = counts.get(key, 0) + n
    favorite = lambda counts: min(counts, key=lambda key: (-counts[key], key)) if counts else None
    return {'customer': profile, 'branch_channels': branches, 'areas': areas, 'addresses': addresses,
            'preferences': {'most_selected_branch_id': favorite(selected), 'most_fulfilling_branch_id': favorite(fulfilled),
                            'selected_branch_counts': selected, 'fulfilled_branch_counts': fulfilled, 'channel_counts': channels},
            'service_history': conversations, 'definitions': definitions()}


@router.get('/customer-crm', include_in_schema=False)
def dashboard(user=Depends(auth.require_roles('owner'))):
    return FileResponse(Path(__file__).resolve().parents[1] / 'public/customer-crm.html',
                        headers={'Cache-Control': 'no-store'})
