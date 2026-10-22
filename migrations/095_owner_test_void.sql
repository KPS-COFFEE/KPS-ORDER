-- Test corrections are immutable reversals, not actual customer disbursements.
ALTER TABLE refunds ADD COLUMN record_kind TEXT NOT NULL DEFAULT 'customer_refund'
 CHECK(record_kind IN ('customer_refund','test_void'));
CREATE UNIQUE INDEX one_test_void_per_order ON refunds(order_id) WHERE record_kind='test_void';
-- Keep the originally confirmed commercial snapshot entirely unchanged.
ALTER TABLE orders ADD COLUMN test_void JSONB CHECK(test_void IS NULL OR jsonb_typeof(test_void)='object');
CREATE FUNCTION protect_owner_test_void_marker() RETURNS trigger AS $$
BEGIN
 IF OLD.test_void IS NOT NULL AND NEW.test_void IS DISTINCT FROM OLD.test_void THEN
  RAISE EXCEPTION 'Owner test cancellation evidence is immutable';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
CREATE TRIGGER immutable_owner_test_void_marker BEFORE UPDATE OF test_void ON orders
 FOR EACH ROW EXECUTE FUNCTION protect_owner_test_void_marker();
