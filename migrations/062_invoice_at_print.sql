-- A cancellation reverses an unpaid invoice without inventing a cash refund.
DO $$ DECLARE constraint_name text; BEGIN
 FOR constraint_name IN SELECT conname FROM pg_constraint WHERE conrelid='fiscal_documents'::regclass AND contype='c'
 AND pg_get_constraintdef(oid) LIKE '%refund_id IS NOT NULL%'
 LOOP EXECUTE format('ALTER TABLE fiscal_documents DROP CONSTRAINT %I',constraint_name); END LOOP;
END $$;
ALTER TABLE fiscal_documents ADD CONSTRAINT fiscal_credit_source CHECK (
 (kind='invoice' AND refund_id IS NULL) OR
 (kind='credit_note' AND (refund_id IS NOT NULL OR coalesce(document->>'credit_reason','')='order_cancelled'))
);
CREATE UNIQUE INDEX fiscal_one_cancellation_credit ON fiscal_documents(order_id)
 WHERE kind='credit_note' AND document->>'credit_reason'='order_cancelled';
-- Freeze the issued sale amounts/items even for callers outside the amendment API.
CREATE FUNCTION protect_issued_sale_values() RETURNS trigger AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM fiscal_documents WHERE order_id=OLD.id AND kind='invoice')
 AND (NEW.total_halalas IS DISTINCT FROM OLD.total_halalas
 OR NEW.subtotal_halalas IS DISTINCT FROM OLD.subtotal_halalas
 OR NEW.delivery_fee_halalas IS DISTINCT FROM OLD.delivery_fee_halalas
 OR NEW.snapshot->'items' IS DISTINCT FROM OLD.snapshot->'items') THEN
 RAISE EXCEPTION 'Issued invoice values are immutable; cancel or refund the sale';
 END IF;
 RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER issued_sale_values_immutable BEFORE UPDATE ON orders
 FOR EACH ROW EXECUTE FUNCTION protect_issued_sale_values();
