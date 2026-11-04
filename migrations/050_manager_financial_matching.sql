CREATE TABLE manager_financial_matches (
 id text PRIMARY KEY,
 report_id text NOT NULL REFERENCES closure_reports(id),
 brand_id text NOT NULL REFERENCES brands(id),
 revision integer NOT NULL CHECK(revision>0),
 expected_bank_halalas bigint NOT NULL,
 expected_cash_halalas bigint NOT NULL,
 bank_halalas bigint NOT NULL CHECK(bank_halalas>=0),
 cash_halalas bigint NOT NULL CHECK(cash_halalas>=0),
 bank_reference text NOT NULL,
 cash_reference text NOT NULL,
 note text NOT NULL DEFAULT '',
 status text NOT NULL CHECK(status IN ('difference','matched')),
 reviewer_id text NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(report_id,revision)
);
CREATE TRIGGER manager_financial_matches_immutable BEFORE UPDATE OR DELETE ON manager_financial_matches
 FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
