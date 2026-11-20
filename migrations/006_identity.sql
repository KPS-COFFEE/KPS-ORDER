-- Additive identity upgrade: existing principal IDs, sessions and historical orders are preserved.
ALTER TABLE users ADD COLUMN IF NOT EXISTS identity_kind TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS contact_email TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_verified_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_sms_verified_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone_whatsapp_verified_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS invitation_status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE users ADD COLUMN IF NOT EXISTS employee_ref TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS vehicle JSONB NOT NULL DEFAULT '{}'::jsonb;
-- Prior customer creation was provider-verified, but its channel was not recorded.
-- Preserve verified ownership without retrospectively asserting SMS/WhatsApp proof.
UPDATE users SET phone_verified_at=created_at WHERE role='customer' AND phone IS NOT NULL AND identity_kind='legacy';
DROP INDEX IF EXISTS users_customer_phone;
CREATE UNIQUE INDEX IF NOT EXISTS users_verified_customer_phone ON users(brand_id,phone)
 WHERE role='customer' AND phone_verified_at IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS users_identity_email ON users(brand_id,lower(email)) WHERE email IS NOT NULL;
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS step_up_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS identity_tokens (
 id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brands(id), user_id TEXT REFERENCES users(id),
 purpose TEXT NOT NULL CHECK(purpose IN ('customer_link','email_login','staff_login','owner_link','recovery','invitation','add_owner')),
 email TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
 session_id TEXT REFERENCES sessions(id), created_by TEXT REFERENCES users(id),
 expires_at TIMESTAMPTZ NOT NULL, consumed_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ,
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0), created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS identity_tokens_user ON identity_tokens(user_id,purpose);
CREATE TABLE IF NOT EXISTS identity_channels (
 brand_id TEXT NOT NULL REFERENCES brands(id), channel TEXT NOT NULL CHECK(channel IN ('guest','email','sms','whatsapp')),
 state TEXT NOT NULL CHECK(state IN ('unconfigured','testing','active','paused','fault')),
 enabled BOOLEAN NOT NULL DEFAULT false, delivery_proven_at TIMESTAMPTZ,
 updated_by TEXT REFERENCES users(id), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(brand_id,channel)
);
INSERT INTO identity_channels(brand_id,channel,state,enabled)
 SELECT id,'guest','active',true FROM brands ON CONFLICT DO NOTHING;
INSERT INTO identity_channels(brand_id,channel,state,enabled)
 SELECT b.id,c.channel,'unconfigured',false FROM brands b CROSS JOIN
 (VALUES('email'),('sms'),('whatsapp')) AS c(channel) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS email_outbox (
 id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brands(id), recipient TEXT NOT NULL,
 kind TEXT NOT NULL, dedupe_key TEXT NOT NULL, encrypted_payload BYTEA NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','provider_accepted','delivered','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0, provider_reference TEXT, last_error TEXT,
 next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(), lease_until TIMESTAMPTZ,
 delivery_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(brand_id,dedupe_key)
);
CREATE INDEX IF NOT EXISTS email_outbox_pending ON email_outbox(status,next_attempt_at);
CREATE TABLE IF NOT EXISTS email_webhook_events (
 id TEXT PRIMARY KEY, event_type TEXT NOT NULL, provider_reference TEXT NOT NULL,
 brand_id TEXT REFERENCES brands(id), received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS email_webhook_provider ON email_webhook_events(provider_reference,event_type);
