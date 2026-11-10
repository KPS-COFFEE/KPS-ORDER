-- A bounded recent-order lookup shared by every checkout source, per brand.
CREATE INDEX IF NOT EXISTS orders_duplicate_contact_recent
 ON orders (brand_id, normalized_identity_phone(customer->>'phone'), created_at DESC)
 WHERE preparation_status NOT IN ('cancelled','rejected') AND delivery_status<>'cancelled';

CREATE FUNCTION protect_order_duplicate_decision_history() RETURNS trigger AS $$
BEGIN
 IF OLD.event_type='order.duplicate_decided' THEN
  RAISE EXCEPTION 'Duplicate order decisions are append-only';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER order_duplicate_decision_history_immutable BEFORE UPDATE OR DELETE ON order_events
 FOR EACH ROW EXECUTE FUNCTION protect_order_duplicate_decision_history();
