-- Separate production ledger: never promote a preview or rewrite an existing sale.
CREATE SEQUENCE fiscal_document_number_seq;
CREATE TABLE fiscal_policy_decisions (
 id TEXT PRIMARY KEY,
 brand_id TEXT NOT NULL REFERENCES brands(id),
 actor_id TEXT NOT NULL REFERENCES users(id),
 decision JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX fiscal_policy_latest ON fiscal_policy_decisions(brand_id,created_at DESC,id DESC);
CREATE TABLE fiscal_documents (
 number TEXT PRIMARY KEY,
 order_id TEXT NOT NULL REFERENCES orders(id),
 kind TEXT NOT NULL CHECK(kind IN ('invoice','credit_note')),
 original_number TEXT REFERENCES fiscal_documents(number) DEFERRABLE INITIALLY DEFERRED,
 refund_id TEXT UNIQUE REFERENCES refunds(id) DEFERRABLE INITIALLY DEFERRED,
 policy_id TEXT NOT NULL REFERENCES fiscal_policy_decisions(id),
 document JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 CHECK ((kind='credit_note') = (refund_id IS NOT NULL)),
 CHECK ((kind='credit_note') = (original_number IS NOT NULL)),
 CHECK(document->>'status'='issued'),
 CHECK(document->>'number'=number AND document->>'order_id'=order_id AND document->>'kind'=kind),
 CHECK(document->>'phase'='1'),
 CHECK((document->>'total_halalas')::bigint >= 0),
 CHECK((document->>'net_halalas')::bigint + (document->>'tax_halalas')::bigint = (document->>'total_halalas')::bigint)
);
CREATE UNIQUE INDEX fiscal_document_original_sale ON fiscal_documents(order_id) WHERE kind='invoice';
CREATE FUNCTION reject_fiscal_document_mutation() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'Issued fiscal documents and policy decisions are immutable'; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER fiscal_documents_immutable BEFORE UPDATE OR DELETE ON fiscal_documents
FOR EACH ROW EXECUTE FUNCTION reject_fiscal_document_mutation();
CREATE TRIGGER fiscal_policy_immutable BEFORE UPDATE OR DELETE ON fiscal_policy_decisions
FOR EACH ROW EXECUTE FUNCTION reject_fiscal_document_mutation();
-- Prevent bypassing the canonical refund workflow (including future callers).
CREATE FUNCTION require_fiscal_credit_for_refund() RETURNS trigger AS $$
BEGIN
 IF EXISTS (SELECT 1 FROM fiscal_documents WHERE order_id=NEW.order_id AND kind='invoice')
 AND NOT EXISTS (SELECT 1 FROM fiscal_documents WHERE refund_id=NEW.id AND order_id=NEW.order_id
   AND kind='credit_note' AND (document->>'total_halalas')::bigint=NEW.amount_halalas) THEN
   RAISE EXCEPTION 'An issued sale requires a matching fiscal credit note before refund';
 END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE CONSTRAINT TRIGGER fiscal_refund_credit_required AFTER INSERT ON refunds
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION require_fiscal_credit_for_refund();
