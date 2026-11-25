-- Preserve the actual provider channel, never infer it from changed runtime settings.
-- Old uncompleted challenges have no reliable channel proof and must be requested again.
-- Existing identities, sessions, orders and their verified contact ownership are untouched.
ALTER TABLE otp_challenges ADD COLUMN IF NOT EXISTS delivery_channel TEXT
 CHECK (delivery_channel IN ('sms','whatsapp'));
