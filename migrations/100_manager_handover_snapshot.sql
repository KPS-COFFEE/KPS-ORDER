-- Operational responsibility is distinct from a receipt of driver cash.
ALTER TABLE manager_handovers ADD COLUMN IF NOT EXISTS operational_snapshot JSONB NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS driver_support_requests (
 id TEXT PRIMARY KEY,
 driver_shift_id TEXT NOT NULL REFERENCES shifts(id),
 from_shift_id TEXT NOT NULL REFERENCES shifts(id),
 to_shift_id TEXT NOT NULL REFERENCES shifts(id),
 brand_id TEXT NOT NULL REFERENCES brands(id),
 branch_ids JSONB NOT NULL,
 reason TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','rejected')),
 requested_by TEXT NOT NULL REFERENCES users(id),
 accepted_by TEXT REFERENCES users(id),
 operational_snapshot JSONB NOT NULL DEFAULT '{}',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 accepted_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS driver_support_pending_driver ON driver_support_requests(driver_shift_id) WHERE status='pending';
CREATE OR REPLACE FUNCTION protect_accepted_driver_support() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.status='accepted' THEN RAISE EXCEPTION 'Accepted support transfer is immutable'; END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
DROP TRIGGER IF EXISTS immutable_driver_support ON driver_support_requests;
CREATE TRIGGER immutable_driver_support BEFORE UPDATE OR DELETE ON driver_support_requests FOR EACH ROW EXECUTE FUNCTION protect_accepted_driver_support();
ALTER TABLE driver_support_requests ADD COLUMN IF NOT EXISTS group_id TEXT;
CREATE INDEX IF NOT EXISTS driver_support_group ON driver_support_requests(group_id);
ALTER TABLE driver_support_requests ADD COLUMN IF NOT EXISTS resolution TEXT CHECK(resolution IN ('reject','withdraw'));
ALTER TABLE driver_support_requests ADD COLUMN IF NOT EXISTS resolved_by TEXT REFERENCES users(id);
ALTER TABLE driver_support_requests ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE driver_support_requests ADD COLUMN IF NOT EXISTS resolution_reason TEXT;
