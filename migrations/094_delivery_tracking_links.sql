CREATE TABLE IF NOT EXISTS delivery_tracking_links (
 order_id TEXT PRIMARY KEY REFERENCES orders(id) ON DELETE CASCADE,
 brand_id TEXT NOT NULL REFERENCES brands(id),
 token_hash TEXT UNIQUE NOT NULL CHECK(length(token_hash)=64),
 expires_at TIMESTAMPTZ NOT NULL
);
