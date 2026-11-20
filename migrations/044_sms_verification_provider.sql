-- Additive provider binding. Existing challenges belong to the original adapter.
ALTER TABLE otp_challenges ADD COLUMN IF NOT EXISTS provider_name TEXT NOT NULL DEFAULT 'twilio_verify'
 CHECK (provider_name IN ('twilio_verify','tawked'));
ALTER TABLE identity_channels ADD COLUMN IF NOT EXISTS sms_provider TEXT
 CHECK (sms_provider IN ('disabled','twilio_verify','tawked'));
-- No channel activation, secret, identity or session changes.
