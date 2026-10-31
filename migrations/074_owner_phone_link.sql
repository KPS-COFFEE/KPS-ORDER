CREATE TABLE IF NOT EXISTS owner_phone_challenges (
 id TEXT PRIMARY KEY,
 user_id TEXT NOT NULL REFERENCES users(id),
 session_id TEXT NOT NULL REFERENCES sessions(id),
 brand_id TEXT NOT NULL REFERENCES brands(id),
 phone TEXT NOT NULL,
 old_phone TEXT,
 account_updated_at TIMESTAMPTZ NOT NULL,
 provider TEXT NOT NULL CHECK (provider IN ('tawked','twilio_verify')),
 provider_reference TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0,
 verified_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS owner_phone_challenges_owner ON owner_phone_challenges(user_id,created_at);
