-- Sensitive SMS proofs are bound to the requesting owner session.
ALTER TABLE otp_challenges ADD COLUMN IF NOT EXISTS source_session_id TEXT REFERENCES sessions(id);
