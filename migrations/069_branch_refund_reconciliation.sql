-- Operational retry metadata only. Financial authorization remains immutable.
CREATE TABLE branch_refund_reconciliation (
 refund_id TEXT PRIMARY KEY REFERENCES branch_online_refunds(id),
 brand_id TEXT NOT NULL REFERENCES brands(id),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 lease_id TEXT,
 lease_until TIMESTAMPTZ,
 processed_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX branch_refund_reconciliation_due
 ON branch_refund_reconciliation(next_attempt_at) WHERE processed_at IS NULL;
