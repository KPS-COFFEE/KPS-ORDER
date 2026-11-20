-- Preserve the issued original; approved revisions append a linked claim note.
DO $$ DECLARE constraint_name text; BEGIN
 FOR constraint_name IN SELECT conname FROM pg_constraint
 WHERE conrelid='fiscal_documents'::regclass AND contype='c'
 AND (pg_get_constraintdef(oid) LIKE '%kind%invoice%credit_note%'
      OR pg_get_constraintdef(oid) LIKE '%original_number IS NOT NULL%')
 LOOP EXECUTE format('ALTER TABLE fiscal_documents DROP CONSTRAINT %I',constraint_name); END LOOP;
END $$;
ALTER TABLE fiscal_documents ADD CONSTRAINT fiscal_document_kind CHECK(kind IN ('invoice','credit_note','debit_note'));
ALTER TABLE fiscal_documents ADD CONSTRAINT fiscal_document_original_link CHECK((kind='invoice')=(original_number IS NULL));
ALTER TABLE fiscal_documents ADD CONSTRAINT fiscal_credit_source CHECK(coalesce((
 (kind='invoice' AND refund_id IS NULL) OR
 (kind='credit_note' AND (refund_id IS NOT NULL OR coalesce(document->>'credit_reason','')='order_cancelled'
    OR (document->>'credit_reason'='approved_price_correction' AND length(coalesce(document->>'treatment_id',''))>0 AND document->>'no_money_posted'='true')))
 OR (kind IN ('credit_note','debit_note') AND refund_id IS NULL AND document->>'credit_reason'='approved_order_amendment'
     AND length(coalesce(document->>'amendment_id',''))>0 AND document->>'no_money_posted'='true')
),false));
CREATE UNIQUE INDEX fiscal_one_amendment_correction ON fiscal_documents((document->>'amendment_id'))
 WHERE document->>'credit_reason'='approved_order_amendment';

CREATE FUNCTION require_approved_fiscal_amendment() RETURNS trigger AS $$
BEGIN
 IF NEW.document->>'credit_reason'='approved_order_amendment' AND NOT EXISTS(
   SELECT 1 FROM order_revisions r WHERE r.order_id=NEW.order_id
   AND r.amendment_id=NEW.document->>'amendment_id'
   AND r.revision=(NEW.document->>'source_revision')::integer
   AND r.before_total=(NEW.document->>'before_total_halalas')::bigint
   AND r.after_total=(NEW.document->>'after_total_halalas')::bigint
   AND r.after_snapshot=NEW.document->'after_snapshot'
   AND r.approved_by IS NOT NULL
 ) THEN RAISE EXCEPTION 'A fiscal amendment correction requires its exact approved revision'; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE CONSTRAINT TRIGGER fiscal_amendment_revision_required AFTER INSERT ON fiscal_documents
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION require_approved_fiscal_amendment();

CREATE OR REPLACE FUNCTION protect_issued_sale_values() RETURNS trigger AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM fiscal_documents WHERE order_id=OLD.id AND kind='invoice')
 AND (NEW.total_halalas IS DISTINCT FROM OLD.total_halalas
 OR NEW.subtotal_halalas IS DISTINCT FROM OLD.subtotal_halalas
 OR NEW.delivery_fee_halalas IS DISTINCT FROM OLD.delivery_fee_halalas
 OR NEW.snapshot->'items' IS DISTINCT FROM OLD.snapshot->'items')
 AND NOT EXISTS(
   SELECT 1 FROM order_revisions r JOIN fiscal_documents d ON d.order_id=r.order_id
     AND d.document->>'amendment_id'=r.amendment_id
     AND d.document->>'credit_reason'='approved_order_amendment'
   WHERE r.order_id=OLD.id AND r.revision=NEW.amendment_revision
     AND NEW.amendment_revision=OLD.amendment_revision+1
     AND r.before_total=OLD.total_halalas AND r.after_total=NEW.total_halalas
     AND r.before_snapshot=OLD.snapshot AND r.after_snapshot=NEW.snapshot
     AND (d.document->>'after_total_halalas')::bigint=NEW.total_halalas
     AND d.document->'after_snapshot'=NEW.snapshot AND r.approved_by IS NOT NULL
 ) THEN RAISE EXCEPTION 'Issued invoice values require an exact approved revision and linked correction'; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;

-- A refund settling an existing amendment credit posts money only once: it must
-- not create a second tax credit for the same decrease in the sale claim.
CREATE OR REPLACE FUNCTION require_fiscal_credit_for_refund() RETURNS trigger AS $$
BEGIN
 IF EXISTS (SELECT 1 FROM fiscal_documents WHERE order_id=NEW.order_id AND kind='invoice')
 AND NOT EXISTS (SELECT 1 FROM fiscal_documents WHERE refund_id=NEW.id AND order_id=NEW.order_id
   AND kind='credit_note' AND (document->>'total_halalas')::bigint=NEW.amount_halalas)
 AND NOT (NEW.amendment_id IS NOT NULL AND EXISTS(
   SELECT 1 FROM fiscal_documents d JOIN order_revisions r ON r.order_id=d.order_id
     AND r.amendment_id=d.document->>'amendment_id'
   WHERE d.order_id=NEW.order_id AND d.kind='credit_note'
     AND d.document->>'credit_reason'='approved_order_amendment'
     AND r.amendment_id=NEW.amendment_id
     AND (SELECT coalesce(sum(amount_halalas),0) FROM refunds f
          WHERE f.order_id=NEW.order_id AND f.amendment_id=NEW.amendment_id)
         <= (d.document->>'total_halalas')::bigint
 )) THEN RAISE EXCEPTION 'An issued sale requires a matching fiscal credit before refund'; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
