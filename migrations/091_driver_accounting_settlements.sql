-- Accounting approvals and physical receipt are separate immutable events.
CREATE TABLE driver_accounting_settlements (
 id text PRIMARY KEY, report_id text NOT NULL REFERENCES closure_reports(id),
 brand_id text NOT NULL REFERENCES brands(id), revision integer NOT NULL CHECK(revision>0),
 stage text NOT NULL CHECK(stage IN ('balance_approved','cash_received')),
 bank_halalas bigint NOT NULL CHECK(bank_halalas BETWEEN 0 AND 100000000000),
 cash_due_halalas bigint NOT NULL CHECK(cash_due_halalas BETWEEN 0 AND 100000000000),
 cash_received_halalas bigint CHECK(cash_received_halalas BETWEEN 0 AND 100000000000),
 received_on date, receipt_reference text, note text NOT NULL,
 actor_id text NOT NULL REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(report_id,revision),
 CHECK((stage='balance_approved' AND cash_received_halalas IS NULL AND received_on IS NULL AND receipt_reference IS NULL)
    OR (stage='cash_received' AND cash_received_halalas IS NOT NULL AND received_on IS NOT NULL AND length(receipt_reference)>0))
);
CREATE INDEX driver_accounting_settlements_scope ON driver_accounting_settlements(brand_id,report_id,revision DESC);
CREATE TRIGGER driver_accounting_settlements_immutable BEFORE UPDATE OR DELETE ON driver_accounting_settlements
 FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
