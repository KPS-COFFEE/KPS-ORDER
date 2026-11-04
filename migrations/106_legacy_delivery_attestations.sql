-- Owner attests historical delivery without inventing time, payment or receipt.
CREATE TABLE legacy_delivery_attestations (
 id text PRIMARY KEY,
 order_id text NOT NULL UNIQUE REFERENCES orders(id),
 task_id text NOT NULL UNIQUE REFERENCES driver_tasks(id),
 shift_id text NOT NULL REFERENCES shifts(id),
 brand_id text NOT NULL REFERENCES brands(id),
 operational_date date NOT NULL,
 actor_id text NOT NULL REFERENCES users(id),
 reason text NOT NULL CHECK(length(btrim(reason))>=5),
 original jsonb NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER immutable_legacy_delivery_attestations BEFORE UPDATE OR DELETE
 ON legacy_delivery_attestations FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
