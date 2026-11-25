-- Preserve immutable receipts and attempt retirement. An exceptional second
-- payment must be recorded, not discarded by the old per-order uniqueness.
ALTER TABLE branch_online_receipts DROP CONSTRAINT branch_online_receipts_order_id_key;
CREATE UNIQUE INDEX branch_online_receipt_applied_order
 ON branch_online_receipts(order_id) WHERE disposition='applied';
