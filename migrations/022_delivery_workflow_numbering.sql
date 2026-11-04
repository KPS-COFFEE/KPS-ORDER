-- Per-branch calendar-day counters; historical order snapshots stay unchanged.
CREATE TABLE IF NOT EXISTS order_daily_counters (
 branch_id TEXT NOT NULL REFERENCES branches(id),
 receipt_date DATE NOT NULL,
 last_number BIGINT NOT NULL CHECK(last_number > 0),
 PRIMARY KEY(branch_id,receipt_date)
);
CREATE UNIQUE INDEX IF NOT EXISTS orders_branch_daily_reference
 ON orders(brand_id,(snapshot->>'order_reference'))
 WHERE snapshot ? 'order_reference';
-- One-time owner-approved temporary operation. Future setting changes persist.
UPDATE app_settings SET settings=settings || '{"delivery_workflow":"manager_dispatch"}'::jsonb
 WHERE brand_id='kps' AND NOT settings ? 'delivery_workflow';
