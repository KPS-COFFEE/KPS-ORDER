-- A preparation destination is operational custody, never a new sale or seller.
-- The original order/financial snapshot and branch ownership remain immutable.
CREATE TABLE order_preparation_routes (
 id text PRIMARY KEY,
 order_id text NOT NULL REFERENCES orders(id),
 brand_id text NOT NULL REFERENCES brands(id),
 source_branch_id text NOT NULL REFERENCES branches(id),
 previous_branch_id text NOT NULL REFERENCES branches(id),
 target_branch_id text NOT NULL REFERENCES branches(id),
 order_version integer NOT NULL CHECK (order_version > 0),
 amendment_revision integer NOT NULL CHECK (amendment_revision >= 0),
 requested_by text NOT NULL REFERENCES users(id),
 reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 3 AND 500),
 evidence jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE (order_id, order_version)
);
CREATE INDEX order_preparation_routes_current ON order_preparation_routes(order_id,order_version DESC);
CREATE TRIGGER order_preparation_routes_immutable BEFORE UPDATE OR DELETE ON order_preparation_routes
 FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
ALTER TABLE orders ADD COLUMN preparation_route_id text REFERENCES order_preparation_routes(id) DEFERRABLE INITIALLY DEFERRED;
CREATE VIEW latest_order_preparation_routes AS
 SELECT r.* FROM order_preparation_routes r JOIN orders o ON o.preparation_route_id=r.id AND o.id=r.order_id;

CREATE FUNCTION validate_order_preparation_pointer() RETURNS trigger AS $$
BEGIN
 IF NEW.preparation_route_id IS DISTINCT FROM OLD.preparation_route_id THEN
  IF NEW.preparation_route_id IS NULL OR NOT EXISTS (
   SELECT 1 FROM order_preparation_routes r JOIN branches b ON b.id=r.target_branch_id
   WHERE r.id=NEW.preparation_route_id AND r.order_id=NEW.id AND r.brand_id=NEW.brand_id
    AND r.source_branch_id=NEW.branch_id AND b.brand_id=NEW.brand_id
    AND r.order_version=NEW.version AND r.amendment_revision=NEW.amendment_revision
  ) THEN RAISE EXCEPTION 'Preparation destination requires its immutable scoped route'; END IF;
  IF NEW.service<>'delivery' OR NEW.preparation_status<>'new' OR NEW.delivery_status<>'pending'
    OR OLD.preparation_status NOT IN ('new','accepted') OR NEW.completed_at IS NOT NULL
    OR EXISTS (SELECT 1 FROM branch_print_jobs WHERE order_id=NEW.id)
    OR EXISTS (SELECT 1 FROM driver_tasks WHERE order_id=NEW.id)
  THEN RAISE EXCEPTION 'Preparation destination is locked after printing, preparation or assignment'; END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER order_preparation_pointer_guard BEFORE UPDATE ON orders
 FOR EACH ROW EXECUTE FUNCTION validate_order_preparation_pointer();

-- Old workers may overlap a rolling deployment. They cannot send a physical
-- command using the former branch after a route-aware worker selected another.
-- Money-only updates and canonical cancellation remain on their existing path.
CREATE FUNCTION preparation_runtime_knows(route_id text) RETURNS boolean AS $$
 SELECT coalesce(nullif(current_setting('kps.preparation_route_ids',true),''),'[]')::jsonb ? route_id
$$ LANGUAGE sql;

CREATE FUNCTION guard_preparation_runtime_order() RETURNS trigger AS $$
DECLARE destination text;
BEGIN
 IF NEW.preparation_route_id IS NOT NULL AND (
   (NEW.preparation_status IS DISTINCT FROM OLD.preparation_status AND NEW.preparation_status NOT IN ('cancelled','rejected'))
   OR (NEW.delivery_status IS DISTINCT FROM OLD.delivery_status AND NEW.delivery_status<>'cancelled')
 ) THEN
  SELECT target_branch_id INTO destination FROM order_preparation_routes WHERE id=NEW.preparation_route_id;
  IF destination<>NEW.branch_id AND NOT preparation_runtime_knows(NEW.preparation_route_id) THEN
   RAISE EXCEPTION 'PREPARATION_ROUTE_RUNTIME_REQUIRED: use a preparation-aware application';
  END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER order_preparation_runtime_guard BEFORE UPDATE ON orders
 FOR EACH ROW EXECUTE FUNCTION guard_preparation_runtime_order();

CREATE FUNCTION guard_preparation_runtime_child() RETURNS trigger AS $$
DECLARE routed record;
BEGIN
 SELECT r.id,r.brand_id,r.source_branch_id,r.target_branch_id INTO routed
  FROM orders o JOIN order_preparation_routes r ON r.id=o.preparation_route_id WHERE o.id=NEW.order_id;
 IF FOUND AND routed.target_branch_id<>routed.source_branch_id THEN
  IF TG_TABLE_NAME='driver_tasks' THEN
   IF NEW.status='cancelled' THEN RETURN NEW; END IF;
  END IF;
  IF NOT preparation_runtime_knows(routed.id) THEN
   RAISE EXCEPTION 'PREPARATION_ROUTE_RUNTIME_REQUIRED: use a preparation-aware application';
  END IF;
  IF TG_TABLE_NAME='branch_print_jobs' THEN
   IF NEW.branch_id<>routed.target_branch_id OR NEW.brand_id<>routed.brand_id THEN
    RAISE EXCEPTION 'PREPARATION_ROUTE_PRINT_DESTINATION: print only at the assigned preparation branch';
   END IF;
  END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER preparation_print_runtime_guard BEFORE INSERT ON branch_print_jobs
 FOR EACH ROW EXECUTE FUNCTION guard_preparation_runtime_child();
CREATE TRIGGER preparation_task_runtime_guard BEFORE INSERT OR UPDATE OF status,driver_id,shift_id ON driver_tasks
 FOR EACH ROW EXECUTE FUNCTION guard_preparation_runtime_child();
CREATE TRIGGER preparation_trip_runtime_guard BEFORE INSERT ON driver_work_trip_orders
 FOR EACH ROW EXECUTE FUNCTION guard_preparation_runtime_child();
