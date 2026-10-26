-- Reconciliation evidence only: no collection/refund/cash posting is generated.
CREATE TABLE bank_reconciliation_batches (
 id text PRIMARY KEY, brand_id text NOT NULL REFERENCES brands(id), branch_ids jsonb NOT NULL,
 batch_reference text NOT NULL, source_snapshot jsonb NOT NULL,
 gross_halalas bigint NOT NULL CHECK(gross_halalas>=0), refund_halalas bigint NOT NULL CHECK(refund_halalas>=0),
 fees_halalas bigint NOT NULL CHECK(fees_halalas>=0), expected_net_halalas bigint NOT NULL,
 evidence_reference text NOT NULL, note text NOT NULL, proposed_by text NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK(expected_net_halalas=gross_halalas-refund_halalas-fees_halalas)
);
CREATE TABLE bank_reconciliation_receipts (
 id text PRIMARY KEY, batch_id text NOT NULL REFERENCES bank_reconciliation_batches(id),
 bank_account_reference text NOT NULL, statement_reference text NOT NULL, line_reference text NOT NULL,
 amount_halalas bigint NOT NULL CHECK(amount_halalas<>0), statement_amount_halalas bigint NOT NULL CHECK(statement_amount_halalas<>0), received_on date NOT NULL,
 evidence_reference text NOT NULL, note text NOT NULL, proposed_by text NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE bank_reconciliation_decisions (
 id text PRIMARY KEY, batch_id text NOT NULL REFERENCES bank_reconciliation_batches(id),
 receipt_id text REFERENCES bank_reconciliation_receipts(id), decision text NOT NULL CHECK(decision IN ('approved','rejected')),
 evidence_reference text NOT NULL, note text NOT NULL, decided_by text NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX bank_batch_one_decision ON bank_reconciliation_decisions(batch_id) WHERE receipt_id IS NULL;
CREATE UNIQUE INDEX bank_receipt_one_decision ON bank_reconciliation_decisions(receipt_id) WHERE receipt_id IS NOT NULL;
CREATE TABLE bank_reconciliation_source_claims (
 source_kind text NOT NULL CHECK(source_kind IN ('collection','refund')), source_id text NOT NULL,
 batch_id text NOT NULL REFERENCES bank_reconciliation_batches(id), amount_halalas bigint NOT NULL CHECK(amount_halalas>0),
 PRIMARY KEY(source_kind,source_id)
);
-- One actual bank line, with explicit accountant allocations across batches/brands.
CREATE TABLE bank_reconciliation_bank_lines (
 bank_account_reference text NOT NULL, statement_reference text NOT NULL, line_reference text NOT NULL,
 amount_halalas bigint NOT NULL CHECK(amount_halalas<>0),
 evidence_reference text NOT NULL, recorded_by text NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(bank_account_reference,statement_reference,line_reference)
);
CREATE TABLE bank_reconciliation_bank_claims (
 receipt_id text PRIMARY KEY REFERENCES bank_reconciliation_receipts(id),
 bank_account_reference text NOT NULL, statement_reference text NOT NULL, line_reference text NOT NULL,
 amount_halalas bigint NOT NULL CHECK(amount_halalas<>0),
 FOREIGN KEY(bank_account_reference,statement_reference,line_reference)
 REFERENCES bank_reconciliation_bank_lines(bank_account_reference,statement_reference,line_reference)
);
CREATE INDEX bank_reconciliation_scope ON bank_reconciliation_batches(brand_id,created_at DESC);
CREATE INDEX bank_reconciliation_receipt_batch ON bank_reconciliation_receipts(batch_id,created_at);
CREATE TRIGGER bank_batches_immutable BEFORE UPDATE OR DELETE ON bank_reconciliation_batches FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER bank_receipts_immutable BEFORE UPDATE OR DELETE ON bank_reconciliation_receipts FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER bank_decisions_immutable BEFORE UPDATE OR DELETE ON bank_reconciliation_decisions FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER bank_sources_immutable BEFORE UPDATE OR DELETE ON bank_reconciliation_source_claims FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER bank_lines_immutable BEFORE UPDATE OR DELETE ON bank_reconciliation_bank_claims FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
CREATE TRIGGER bank_original_lines_immutable BEFORE UPDATE OR DELETE ON bank_reconciliation_bank_lines FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
