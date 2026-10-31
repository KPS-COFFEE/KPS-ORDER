-- Existing settlement events already approved the balance. New cash-first
-- receipts explicitly retain balance_approved=false until its paper is reviewed.
ALTER TABLE driver_accounting_settlements ADD COLUMN balance_approved boolean NOT NULL DEFAULT true;
CREATE TABLE accounting_daily_closures (
 id text PRIMARY KEY,
 manager_shift_id text NOT NULL UNIQUE REFERENCES shifts(id),
 brand_id text NOT NULL REFERENCES brands(id),
 branch_ids jsonb NOT NULL,
 operational_date date NOT NULL,
 snapshot jsonb NOT NULL,
 approved_by text NOT NULL REFERENCES users(id),
 note text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX accounting_daily_closures_period ON accounting_daily_closures(brand_id,operational_date);
CREATE TRIGGER accounting_daily_closures_immutable BEFORE UPDATE OR DELETE ON accounting_daily_closures
 FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
