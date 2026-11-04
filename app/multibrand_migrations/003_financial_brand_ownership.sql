-- Forward-only ownership hardening behind the existing multibrand guard.
-- Validate retained history; a conflict aborts migration, never repairs data.
-- Keep original NO ACTION delete/update behavior and shared employee identity.
CREATE UNIQUE INDEX dual_orders_brand_identity ON orders(brand_id,id);
CREATE UNIQUE INDEX dual_orders_customer_identity ON orders(brand_id,id,customer_id);
CREATE UNIQUE INDEX dual_payment_attempt_brand_identity ON branch_payment_attempts(brand_id,id);
CREATE UNIQUE INDEX dual_payment_attempt_order_identity
 ON branch_payment_attempts(brand_id,id,order_id,branch_id);
CREATE UNIQUE INDEX dual_online_refund_brand_identity ON branch_online_refunds(brand_id,id);

ALTER TABLE branch_payment_attempts ADD CONSTRAINT dual_attempt_order_brand
 FOREIGN KEY(brand_id,order_id) REFERENCES orders(brand_id,id);
ALTER TABLE branch_payment_attempts ADD CONSTRAINT dual_attempt_branch_brand
 FOREIGN KEY(brand_id,branch_id) REFERENCES branches(brand_id,id);
ALTER TABLE branch_checkout_sessions ADD CONSTRAINT dual_checkout_attempt_brand
 FOREIGN KEY(brand_id,attempt_id) REFERENCES branch_payment_attempts(brand_id,id);
ALTER TABLE branch_payment_webhooks ADD CONSTRAINT dual_webhook_attempt_brand
 FOREIGN KEY(brand_id,attempt_id) REFERENCES branch_payment_attempts(brand_id,id);
ALTER TABLE branch_online_receipts ADD CONSTRAINT dual_receipt_attempt_order_brand
 FOREIGN KEY(brand_id,attempt_id,order_id,branch_id)
 REFERENCES branch_payment_attempts(brand_id,id,order_id,branch_id);
ALTER TABLE branch_online_refunds ADD CONSTRAINT dual_refund_attempt_order_brand
 FOREIGN KEY(brand_id,attempt_id,order_id,branch_id)
 REFERENCES branch_payment_attempts(brand_id,id,order_id,branch_id);
ALTER TABLE branch_payment_renewals ADD CONSTRAINT dual_renewal_previous_brand
 FOREIGN KEY(brand_id,previous_attempt_id) REFERENCES branch_payment_attempts(brand_id,id);
ALTER TABLE branch_payment_renewals ADD CONSTRAINT dual_renewal_next_brand
 FOREIGN KEY(brand_id,next_attempt_id) REFERENCES branch_payment_attempts(brand_id,id);
ALTER TABLE branch_refund_reconciliation ADD CONSTRAINT dual_reconciliation_refund_brand
 FOREIGN KEY(brand_id,refund_id) REFERENCES branch_online_refunds(brand_id,id);
-- The canonical order CTE inserts benefit state before the order. Preserve the
-- original deferred order-reference timing, but bind its customer and brand too.
ALTER TABLE benefit_order_states ADD CONSTRAINT dual_benefit_order_customer_brand
 FOREIGN KEY(brand_id,order_id,customer_id) REFERENCES orders(brand_id,id,customer_id)
 DEFERRABLE INITIALLY DEFERRED;

-- Existing renewal API copies the previous attempt's order into its successor.
-- The link table intentionally has no duplicated order column or backfill.
-- Let composite FKs reject missing/foreign parents; this trigger only rejects
-- two otherwise valid same-brand attempts belonging to different orders.
DO $$
BEGIN
 IF EXISTS(SELECT 1 FROM branch_payment_renewals r
   JOIN branch_payment_attempts a ON a.id=r.previous_attempt_id
   JOIN branch_payment_attempts b ON b.id=r.next_attempt_id
   WHERE a.order_id IS DISTINCT FROM b.order_id)
 THEN RAISE EXCEPTION 'Payment renewal history crosses orders'; END IF;
END; $$;
CREATE FUNCTION guard_dual_payment_renewal_order() RETURNS trigger AS $$
DECLARE previous_order text; next_order text;
BEGIN
 SELECT a.order_id,b.order_id INTO previous_order,next_order
 FROM branch_payment_attempts a JOIN branch_payment_attempts b ON b.id=NEW.next_attempt_id
 WHERE a.id=NEW.previous_attempt_id AND a.brand_id=NEW.brand_id AND b.brand_id=NEW.brand_id;
 IF FOUND AND previous_order IS DISTINCT FROM next_order THEN
  RAISE EXCEPTION 'Payment renewal order ownership mismatch';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER dual_payment_renewal_order BEFORE INSERT ON branch_payment_renewals
 FOR EACH ROW EXECUTE FUNCTION guard_dual_payment_renewal_order();
