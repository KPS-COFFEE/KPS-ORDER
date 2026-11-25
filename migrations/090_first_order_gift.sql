-- No claim is deleted or reset by campaign restarts or policy version changes.
CREATE TABLE IF NOT EXISTS first_order_gift_claims (
    brand_id TEXT NOT NULL REFERENCES brands(id),
    phone TEXT NOT NULL CHECK(phone ~ '^\+9665[0-9]{8}$'),
    order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),
    customer_id TEXT NOT NULL REFERENCES users(id),
    policy_version INTEGER NOT NULL CHECK(policy_version > 0),
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(brand_id, phone)
);
