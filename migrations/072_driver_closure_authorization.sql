-- Additive: no historical shift, mileage or invoice data is rewritten.
ALTER TABLE shifts ADD COLUMN scheduled_end_at timestamptz;
ALTER TABLE shifts ADD COLUMN closure_authorization jsonb;
ALTER TABLE shifts ADD CONSTRAINT driver_scheduled_end_valid CHECK (scheduled_end_at IS NULL OR scheduled_end_at > opened_at);
