CREATE TABLE driver_float_requests (
 id text PRIMARY KEY,
 brand_id text NOT NULL REFERENCES brands(id),
 driver_id text NOT NULL REFERENCES users(id),
 branch_ids jsonb NOT NULL,
 base_request_id text REFERENCES driver_float_requests(id) DEFERRABLE INITIALLY DEFERRED,
 amount_halalas bigint NOT NULL CHECK(amount_halalas>=0),
 reference text NOT NULL CHECK(length(trim(reference))>0),
 reason text NOT NULL CHECK(length(trim(reason))>0),
 requested_by text NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE driver_float_decisions (
 request_id text PRIMARY KEY REFERENCES driver_float_requests(id),
 brand_id text NOT NULL REFERENCES brands(id),
 approved boolean NOT NULL,
 reviewed_by text NOT NULL REFERENCES users(id),
 reference text NOT NULL CHECK(length(trim(reference))>0),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX driver_float_driver ON driver_float_requests(driver_id,created_at);
CREATE TRIGGER driver_float_requests_immutable BEFORE UPDATE OR DELETE ON driver_float_requests
 FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER driver_float_decisions_immutable BEFORE UPDATE OR DELETE ON driver_float_decisions
 FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
ALTER TABLE shifts ADD COLUMN permanent_float jsonb;
