-- Additive identity proof belongs to the server session, never a supplied phone.
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS customer_verified_at TIMESTAMPTZ;
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
