"""Read-only counts of distinct assigned orders within authorized shifts."""
KEYS = ('total', 'completed', 'preparing', 'ready', 'on_way', 'pending', 'problem', 'cancelled')

QUERY = """
WITH latest AS (
 SELECT DISTINCT ON (parent.id, t.order_id)
   parent.id AS shift_id, t.order_id, t.status, o.preparation_status
 FROM shifts parent
 JOIN shifts driver ON (driver.id=parent.id OR driver.manager_shift_id=parent.id)
 JOIN driver_tasks t ON t.shift_id=driver.id
 JOIN orders o ON o.id=t.order_id
 WHERE parent.id=ANY(%s) AND parent.brand_id=%s
   AND driver.brand_id=parent.brand_id AND o.brand_id=parent.brand_id
   AND (%s OR o.branch_id=ANY(%s))
   AND t.status NOT IN ('transferred','reassigned')
 ORDER BY parent.id, t.order_id, t.created_at DESC, t.id DESC
), classified AS (
 SELECT shift_id, CASE
   WHEN status='delivered' THEN 'completed'
   WHEN status='cancelled' OR preparation_status IN ('cancelled','rejected') THEN 'cancelled'
   WHEN status='problem' THEN 'problem'
   WHEN status='picked_up' OR preparation_status='handed_over' THEN 'on_way'
   WHEN preparation_status='ready' THEN 'ready'
   WHEN preparation_status IN ('accepted','preparing') THEN 'preparing'
   ELSE 'pending' END AS bucket
 FROM latest
)
SELECT shift_id, bucket, count(*) AS count FROM classified GROUP BY shift_id,bucket
"""

def attach(c, rows, user):
    if not rows:
        return
    by_id = {row['id']: row for row in rows}
    for row in rows:
        row['order_counts'] = dict.fromkeys(KEYS, 0)
    for item in c.execute(QUERY, (list(by_id), user['brand_id'], user['role']=='owner', user.get('branch_ids', []))).fetchall():
        counts = by_id[item['shift_id']]['order_counts']
        counts[item['bucket']] += item['count']
        counts['total'] += item['count']
