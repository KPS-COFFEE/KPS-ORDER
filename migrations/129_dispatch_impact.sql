-- Short-lived private quotes. Durable decision evidence lives in immutable events.
CREATE TABLE dispatch_impact_quotes (
 id text PRIMARY KEY, actor_id text NOT NULL REFERENCES users(id),
 order_id text NOT NULL REFERENCES orders(id), driver_id text NOT NULL REFERENCES users(id),
 shift_id text NOT NULL REFERENCES shifts(id), fingerprint text NOT NULL,
 result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL
);
CREATE INDEX dispatch_impact_quotes_expiry ON dispatch_impact_quotes(expires_at);
