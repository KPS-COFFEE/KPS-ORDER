-- Additive identity proof belongs to the server session, never a supplied phone.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS customer_verified_at TIMESTAMPTZ;
-- Preserve a still-valid SMS login issued by the previous application. The
-- consumed challenge and session share their issuing transaction timestamp;
-- a verified phone on the user alone cannot authenticate an email/guest login.
-- Only the new proof column changes here, preserving old session snapshots.
UPDATE sessions s SET customer_verified_at=c.verified_at
FROM users u, otp_challenges c
WHERE s.user_id=u.id AND u.role='customer' AND u.phone_sms_verified_at IS NOT NULL
 AND c.brand_id=u.brand_id AND c.phone=u.phone AND c.delivery_channel='sms'
 AND c.verified_at=s.created_at AND c.verified_at>now()-interval '30 days'
 AND s.revoked_at IS NULL AND s.expires_at>now() AND s.customer_verified_at IS NULL;
CREATE TABLE IF NOT EXISTS customer_addresses (
 id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES users(id), brand_id TEXT NOT NULL REFERENCES brands(id),
 label TEXT NOT NULL, address TEXT NOT NULL, neighborhood TEXT NOT NULL DEFAULT '',
 lat DOUBLE PRECISION, lng DOUBLE PRECISION, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK ((lat IS NULL AND lng IS NULL) OR (lat BETWEEN -90 AND 90 AND lng BETWEEN -180 AND 180))
);
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS category TEXT NOT NULL DEFAULT 'help';
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS followup_no BIGSERIAL;
CREATE UNIQUE INDEX IF NOT EXISTS conversation_followup_unique ON conversations(followup_no);
ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_route_check;
ALTER TABLE conversations ADD CONSTRAINT conversations_route_check CHECK(route IN ('delivery','branch','privacy','review'));

ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_order_id_key;
ALTER TABLE conversations ALTER COLUMN branch_id DROP NOT NULL;
CREATE INDEX IF NOT EXISTS conversation_order_category ON conversations(order_id,category) WHERE order_id IS NOT NULL;

ALTER TABLE otp_challenges ADD COLUMN IF NOT EXISTS source_user_id TEXT REFERENCES users(id);

ALTER TABLE otp_challenges ADD COLUMN IF NOT EXISTS source_guest_id TEXT REFERENCES users(id);

CREATE TABLE IF NOT EXISTS customer_identity_links (
 source_id TEXT PRIMARY KEY REFERENCES users(id), customer_id TEXT NOT NULL REFERENCES users(id),
 brand_id TEXT NOT NULL REFERENCES brands(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK(source_id<>customer_id)
);
CREATE INDEX IF NOT EXISTS customer_identity_links_target ON customer_identity_links(customer_id,brand_id);
