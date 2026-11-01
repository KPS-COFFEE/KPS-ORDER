-- A routed amendment refreshes the same destination and preserves its history.
-- Runtime markers alone remain insufficient to change an issued/routed order.
CREATE FUNCTION exact_preparation_amendment(prior orders, revised orders) RETURNS boolean AS $$
 SELECT EXISTS(
  SELECT 1 FROM order_preparation_routes oldroute
  JOIN order_preparation_routes newroute ON newroute.order_id=oldroute.order_id
  JOIN order_revisions r ON r.order_id=newroute.order_id AND r.revision=newroute.amendment_revision
  JOIN order_amendments a ON a.id=r.amendment_id AND a.order_id=r.order_id
  WHERE oldroute.id=prior.preparation_route_id AND oldroute.order_id=prior.id
   AND newroute.id=revised.preparation_route_id AND newroute.id<>oldroute.id
   AND newroute.brand_id=prior.brand_id AND newroute.source_branch_id=prior.branch_id
   AND newroute.target_branch_id=oldroute.target_branch_id
   AND newroute.previous_branch_id=oldroute.target_branch_id
   AND newroute.order_version=revised.version AND revised.version=prior.version+1
   AND newroute.amendment_revision=revised.amendment_revision
   AND revised.amendment_revision=prior.amendment_revision+1
   AND newroute.requested_by=r.approved_by AND newroute.evidence->>'amendment_id'=a.id
   AND newroute.evidence->>'previous_route_id'=oldroute.id
   AND newroute.evidence->'after_snapshot'=revised.snapshot
   AND newroute.evidence->'after_customer'=revised.customer
   AND a.status='applied' AND a.base_revision=prior.amendment_revision
   AND a.reviewed_by=r.approved_by AND a.consent_by=r.consent_by
   AND r.before_snapshot=prior.snapshot AND r.before_customer=prior.customer AND r.before_total=prior.total_halalas
   AND r.after_snapshot=revised.snapshot AND r.after_customer=revised.customer AND r.after_total=revised.total_halalas
   AND revised.branch_id=prior.branch_id AND revised.brand_id=prior.brand_id
   AND revised.service=prior.service AND revised.service='delivery'
   AND revised.preparation_status=prior.preparation_status AND revised.delivery_status=prior.delivery_status
   AND revised.completed_at IS NULL
 )
$$ LANGUAGE sql;

CREATE OR REPLACE FUNCTION validate_order_preparation_pointer() RETURNS trigger AS $$
BEGIN
 IF NEW.preparation_route_id IS DISTINCT FROM OLD.preparation_route_id THEN
  IF NEW.preparation_route_id IS NULL OR NOT EXISTS (
   SELECT 1 FROM order_preparation_routes r JOIN branches b ON b.id=r.target_branch_id
   WHERE r.id=NEW.preparation_route_id AND r.order_id=NEW.id AND r.brand_id=NEW.brand_id
    AND r.source_branch_id=NEW.branch_id AND b.brand_id=NEW.brand_id
    AND r.order_version=NEW.version AND r.amendment_revision=NEW.amendment_revision
  ) THEN RAISE EXCEPTION 'Preparation destination requires its immutable scoped route'; END IF;
  IF exact_preparation_amendment(OLD,NEW) AND preparation_runtime_knows(NEW.preparation_route_id) THEN RETURN NEW; END IF;
  IF NEW.service<>'delivery' OR NEW.preparation_status<>'new' OR NEW.delivery_status<>'pending'
    OR OLD.preparation_status NOT IN ('new','accepted') OR NEW.completed_at IS NOT NULL
    OR EXISTS (SELECT 1 FROM branch_print_jobs WHERE order_id=NEW.id)
    OR EXISTS (SELECT 1 FROM driver_tasks WHERE order_id=NEW.id)
  THEN RAISE EXCEPTION 'Preparation destination is locked after printing, preparation or assignment'; END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION guard_preparation_runtime_order() RETURNS trigger AS $$
DECLARE destination text;
BEGIN
 IF NEW.amendment_revision IS DISTINCT FROM OLD.amendment_revision AND EXISTS (
  SELECT 1 FROM order_preparation_routes r WHERE r.id=OLD.preparation_route_id
   AND r.order_id=OLD.id AND r.target_branch_id<>r.source_branch_id
 ) AND NOT (exact_preparation_amendment(OLD,NEW) AND preparation_runtime_knows(NEW.preparation_route_id)) THEN
  RAISE EXCEPTION 'PREPARATION_ROUTE_AMENDMENT: exact approved revision and current route required';
 END IF;
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
