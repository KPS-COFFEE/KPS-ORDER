-- Explicit owner finalization of a calendar-period marketing report only.
-- No orders, claims, gifts, advertising or accounting closure is changed.
CREATE TABLE promotion_report_closures (
 id text PRIMARY KEY,
 brand_id text NOT NULL REFERENCES brands(id),
 actor_id text NOT NULL REFERENCES users(id),
 start_date date NOT NULL,
 end_date date NOT NULL CHECK(end_date>=start_date AND end_date-start_date<=92),
 branch_ids jsonb NOT NULL CHECK(jsonb_typeof(branch_ids)='array'),
 policy_version integer CHECK(policy_version>0),
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 500),
 sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'
  AND snapshot ? 'brand_id' AND snapshot->>'brand_id'=brand_id
  AND jsonb_typeof(snapshot->'beneficiaries')='array'),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX promotion_report_closures_scope ON promotion_report_closures(brand_id,created_at DESC,id DESC);
CREATE FUNCTION promotion_report_closure_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'Final promotion reports are append-only'; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER promotion_report_closure_immutable BEFORE UPDATE OR DELETE ON promotion_report_closures
 FOR EACH ROW EXECUTE FUNCTION promotion_report_closure_immutable();
