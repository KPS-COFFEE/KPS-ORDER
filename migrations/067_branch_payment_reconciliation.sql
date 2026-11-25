-- Distinguish locally scheduled verification from provider-reported events.
ALTER TABLE branch_payment_webhooks DROP CONSTRAINT branch_payment_webhooks_event_type_check;
ALTER TABLE branch_payment_webhooks ADD CONSTRAINT branch_payment_webhooks_event_type_check
 CHECK(event_type IN ('payment_paid','payment_captured','reconciliation_requested'));
