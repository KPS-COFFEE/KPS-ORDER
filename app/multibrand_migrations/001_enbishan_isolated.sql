-- Executed exclusively through the local test-database isolation guard.
CREATE TABLE staff_brand_memberships (
 user_id text NOT NULL REFERENCES users(id), brand_id text NOT NULL REFERENCES brands(id),
 branch_ids jsonb NOT NULL CHECK(jsonb_typeof(branch_ids)='array'), active boolean NOT NULL DEFAULT true,
 granted_by text NOT NULL REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(user_id,brand_id)
);
CREATE TABLE driver_brand_dedications (
 id text PRIMARY KEY, driver_id text NOT NULL REFERENCES users(id), brand_id text NOT NULL REFERENCES brands(id),
 starts_at timestamptz NOT NULL, ends_at timestamptz NOT NULL CHECK(ends_at>starts_at),
 reason text NOT NULL, created_by text NOT NULL REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now(),
 cancelled_at timestamptz
);
CREATE INDEX dedication_driver_period ON driver_brand_dedications(driver_id, starts_at, ends_at) WHERE cancelled_at IS NULL;
-- Financial subledgers stay brand-specific, while the employee ID is shared.
DROP INDEX shifts_one_active_user;
CREATE UNIQUE INDEX shifts_one_active_user_brand ON shifts(user_id,brand_id) WHERE status<>'closed';
CREATE UNIQUE INDEX branches_brand_identity ON branches(brand_id,id);
ALTER TABLE orders ADD CONSTRAINT dual_order_branch_brand FOREIGN KEY(brand_id,branch_id) REFERENCES branches(brand_id,id);
CREATE UNIQUE INDEX shifts_brand_identity ON shifts(brand_id,id);
ALTER TABLE shifts ADD CONSTRAINT dual_manager_shift_brand FOREIGN KEY(brand_id,manager_shift_id) REFERENCES shifts(brand_id,id);
CREATE FUNCTION guard_dual_collection_brand() RETURNS trigger AS $$
BEGIN
 IF NEW.shift_id IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM orders o JOIN shifts s ON s.brand_id=o.brand_id WHERE o.id=NEW.order_id AND s.id=NEW.shift_id
 ) THEN RAISE EXCEPTION 'Collection shift belongs to another brand'; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER dual_collection_brand BEFORE INSERT ON collections FOR EACH ROW EXECUTE FUNCTION guard_dual_collection_brand();
CREATE TRIGGER dual_refund_brand BEFORE INSERT ON refunds FOR EACH ROW EXECUTE FUNCTION guard_dual_collection_brand();
CREATE FUNCTION guard_dual_transfer_brand() RETURNS trigger AS $$
BEGIN
 IF NEW.to_shift_id IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM shifts a JOIN shifts b ON b.brand_id=a.brand_id WHERE a.id=NEW.from_shift_id AND b.id=NEW.to_shift_id
 ) THEN RAISE EXCEPTION 'Cash transfer crosses brands'; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER dual_transfer_brand BEFORE INSERT ON custody_transfers FOR EACH ROW EXECUTE FUNCTION guard_dual_transfer_brand();
