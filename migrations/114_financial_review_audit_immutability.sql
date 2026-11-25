-- Financial review/treatment records share the existing audit ledger. Protect
-- their source evidence and decisions without rewriting any historical row.
CREATE OR REPLACE FUNCTION protect_financial_review_audit() RETURNS trigger AS $$
BEGIN
  IF OLD.action LIKE 'accounting.financial_treatment.%'
     OR OLD.action LIKE 'accounting.refund_item_allocation.%' THEN
    RAISE EXCEPTION 'Immutable financial review record';
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER financial_review_audit_immutable BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION protect_financial_review_audit();
CREATE INDEX financial_review_audit_sources ON audit_events(entity_type,entity_id,created_at,id)
WHERE action LIKE 'accounting.financial_treatment.%' OR action LIKE 'accounting.refund_item_allocation.%';
