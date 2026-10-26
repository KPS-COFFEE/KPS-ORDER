-- Explicit owner grants and append-only late-delivery records. No historical
-- receipt, collection, report or closure is rewritten.
CREATE TABLE prior_delivery_grants (
 id text PRIMARY KEY, brand_id text NOT NULL REFERENCES brands(id),
 manager_shift_id text NOT NULL REFERENCES shifts(id), manager_id text NOT NULL REFERENCES users(id),
 driver_shift_ids jsonb NOT NULL, operational_date date NOT NULL,
 authorized_by text NOT NULL REFERENCES users(id), reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 CHECK(expires_at > created_at)
);
CREATE TABLE prior_delivery_revocations (
 grant_id text PRIMARY KEY REFERENCES prior_delivery_grants(id),
 actor_id text NOT NULL REFERENCES users(id), reason text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE shifts ADD COLUMN supplement_of_shift_id text REFERENCES shifts(id) DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX shift_supplements ON shifts(supplement_of_shift_id) WHERE supplement_of_shift_id IS NOT NULL;
CREATE TABLE prior_delivery_records (
 id text PRIMARY KEY, grant_id text NOT NULL REFERENCES prior_delivery_grants(id),
 brand_id text NOT NULL REFERENCES brands(id), order_id text NOT NULL UNIQUE REFERENCES orders(id),
 original_shift_id text NOT NULL REFERENCES shifts(id), ledger_shift_id text NOT NULL REFERENCES shifts(id),
 driver_id text NOT NULL REFERENCES users(id), actor_id text NOT NULL REFERENCES users(id),
 operational_date date NOT NULL, source_reference text NOT NULL,
 actual_delivered_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL DEFAULT now(),
 cash_halalas bigint NOT NULL CHECK(cash_halalas>=0), card_halalas bigint NOT NULL CHECK(card_halalas>=0),
 reason text NOT NULL, details jsonb NOT NULL,
 CHECK(cash_halalas+card_halalas>0),
 UNIQUE(brand_id,driver_id,operational_date,source_reference)
);
CREATE TRIGGER prior_delivery_grants_immutable BEFORE UPDATE OR DELETE ON prior_delivery_grants FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER prior_delivery_revocations_immutable BEFORE UPDATE OR DELETE ON prior_delivery_revocations FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER prior_delivery_records_immutable BEFORE UPDATE OR DELETE ON prior_delivery_records FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
