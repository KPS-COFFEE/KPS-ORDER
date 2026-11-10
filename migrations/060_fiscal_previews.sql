-- Local validation artifacts only; not production fiscal numbering.
CREATE SEQUENCE fiscal_preview_number_seq;
CREATE TABLE fiscal_previews (
    number TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(id),
    kind TEXT NOT NULL CHECK(kind IN ('invoice','credit_note')),
    refund_id TEXT UNIQUE REFERENCES refunds(id),
    document JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((kind='credit_note') = (refund_id IS NOT NULL)),
    CHECK(document->>'status'='local_preview_only'),
    CHECK(document->>'number'=number),
    CHECK(document->>'order_id'=order_id),
    CHECK(document->>'kind'=kind)
);
CREATE UNIQUE INDEX fiscal_preview_original_sale ON fiscal_previews(order_id) WHERE kind='invoice';
CREATE FUNCTION reject_fiscal_preview_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Saved fiscal previews are immutable';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER fiscal_previews_immutable BEFORE UPDATE OR DELETE ON fiscal_previews
FOR EACH ROW EXECUTE FUNCTION reject_fiscal_preview_mutation();
