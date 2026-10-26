-- Audio bytes are isolated from frequently read/public app_settings payloads.
CREATE TABLE driver_order_audio (
    brand_id TEXT PRIMARY KEY REFERENCES brands(id),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
    sha256 TEXT NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
    audio BYTEA NOT NULL CHECK(octet_length(audio) BETWEEN 46 AND 1000000),
    updated_by TEXT NOT NULL REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
