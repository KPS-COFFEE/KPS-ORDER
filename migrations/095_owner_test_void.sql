-- Test corrections are immutable reversals, not actual customer disbursements.
ALTER TABLE refunds ADD COLUMN record_kind TEXT NOT NULL DEFAULT 'customer_refund'
 CHECK(record_kind IN ('customer_refund','test_void'));
CREATE UNIQUE INDEX one_test_void_per_order ON refunds(order_id) WHERE record_kind='test_void';
