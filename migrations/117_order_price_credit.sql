-- An approved price correction credits the original claim without a cash refund.
ALTER TABLE fiscal_documents DROP CONSTRAINT fiscal_credit_source;
ALTER TABLE fiscal_documents ADD CONSTRAINT fiscal_credit_source CHECK (
 (kind='invoice' AND refund_id IS NULL) OR
 (kind='credit_note' AND (refund_id IS NOT NULL OR coalesce(document->>'credit_reason','')='order_cancelled'
   OR (document->>'credit_reason'='approved_price_correction' AND length(coalesce(document->>'treatment_id',''))>0
       AND document->>'no_money_posted'='true')))
);
CREATE UNIQUE INDEX fiscal_one_source_price_credit ON fiscal_documents((document->>'treatment_id'))
 WHERE kind='credit_note' AND document->>'credit_reason'='approved_price_correction';
CREATE OR REPLACE FUNCTION protect_financial_review_audit() RETURNS trigger AS $$
BEGIN
  IF OLD.action LIKE 'accounting.financial_treatment.%'
     OR OLD.action LIKE 'accounting.refund_item_allocation.%'
     OR OLD.action LIKE 'accounting.order_financial_treatment.%' THEN
    RAISE EXCEPTION 'Immutable financial review record';
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
-- The linked approval must exist when the transaction commits. A note alone
-- cannot produce an unbacked credit document through another caller.
CREATE FUNCTION require_approved_source_price_credit() RETURNS trigger AS $$
BEGIN
 IF NEW.document->>'credit_reason'='approved_price_correction'
 AND NOT EXISTS(SELECT 1 FROM audit_events a WHERE a.entity_type='order' AND a.entity_id=NEW.order_id
   AND a.action='accounting.order_financial_treatment.approved'
   AND a.details->>'kind'='price_correction'
   AND a.details->>'treatment_id'=NEW.document->>'treatment_id'
   AND a.details->'source_document'->>'number'=NEW.number
   AND (a.details->>'credit_halalas')::bigint=(NEW.document->>'total_halalas')::bigint) THEN
   RAISE EXCEPTION 'A source price credit requires its linked independent approval';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE CONSTRAINT TRIGGER source_price_credit_approval_required AFTER INSERT ON fiscal_documents
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION require_approved_source_price_credit();
