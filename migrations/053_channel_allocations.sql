-- Additive only: no backfill, operational UPDATE or inferred legacy ownership.
CREATE TABLE IF NOT EXISTS channel_settlement_allocations (
    id text PRIMARY KEY,
    order_id text NOT NULL REFERENCES orders(id),
    refund_id text REFERENCES refunds(id),
    kind text NOT NULL CHECK (kind IN ('refund','compensation')),
    cause text NOT NULL CHECK (cause IN ('branch','delivery_channel')),
    beneficiary text NOT NULL CHECK (beneficiary IN ('reem','sami')),
    amount_halalas bigint NOT NULL CHECK (amount_halalas > 0),
    reason text NOT NULL CHECK (length(trim(reason)) >= 3),
    authorized_by text NOT NULL REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((kind='refund') = (refund_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS channel_allocations_order_idx ON channel_settlement_allocations(order_id);
CREATE OR REPLACE FUNCTION reject_channel_allocation_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Approved channel allocations are append-only';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER channel_allocations_immutable BEFORE UPDATE OR DELETE ON channel_settlement_allocations
FOR EACH ROW EXECUTE FUNCTION reject_channel_allocation_mutation();
