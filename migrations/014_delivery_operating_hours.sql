-- Approved delivery day only; preserve all historical timestamps and records.
ALTER TABLE shifts ADD COLUMN delivery_operational_date DATE;
ALTER TABLE shifts ADD COLUMN delivery_window_snapshot JSONB;
ALTER TABLE orders ADD COLUMN delivery_operational_date DATE;

-- A branch is evaluated only once, after catalog import. Owner overrides and
-- deliberate later clearing of hours survive every restart and restore.
CREATE TABLE delivery_hours_decisions (
 branch_id TEXT PRIMARY KEY REFERENCES branches(id), decision_id TEXT NOT NULL,
 result TEXT NOT NULL, evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
