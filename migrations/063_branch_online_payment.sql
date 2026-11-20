-- Additive sandbox foundation. No flags, merchant assignments or existing
-- orders are changed. Live payments remain unavailable.
CREATE TABLE branch_payment_attempts (
 id TEXT PRIMARY KEY,
 order_id TEXT NOT NULL REFERENCES orders(id),
 brand_id TEXT NOT NULL REFERENCES brands(id),
 branch_id TEXT NOT NULL REFERENCES branches(id),
 seller_id TEXT NOT NULL,
 provider TEXT NOT NULL,
 provider_payment_id UUID NOT NULL,
 mode TEXT NOT NULL CHECK(mode='test'),
 amount_halalas BIGINT NOT NULL CHECK(amount_halalas>=100),
 currency TEXT NOT NULL CHECK(currency='SAR'),
 order_revision INTEGER NOT NULL,
 accepted_by TEXT NOT NULL REFERENCES users(id),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','verified','review_required')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+interval '15 minutes',
 verified_at TIMESTAMPTZ,
 UNIQUE(provider,provider_payment_id),
 UNIQUE(order_id)
);
CREATE TABLE branch_online_receipts (
 id TEXT PRIMARY KEY,
 attempt_id TEXT NOT NULL UNIQUE REFERENCES branch_payment_attempts(id),
 order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),
 brand_id TEXT NOT NULL REFERENCES brands(id),
 branch_id TEXT NOT NULL REFERENCES branches(id),
 seller_id TEXT NOT NULL,
 provider TEXT NOT NULL,
 provider_payment_id UUID NOT NULL,
 mode TEXT NOT NULL CHECK(mode='test'),
 amount_halalas BIGINT NOT NULL CHECK(amount_halalas>=100),
 currency TEXT NOT NULL CHECK(currency='SAR'),
 disposition TEXT NOT NULL CHECK(disposition IN ('applied','review_required')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(provider,provider_payment_id)
);
CREATE FUNCTION protect_branch_payment_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Payment attempts cannot be deleted'; END IF;
 IF (to_jsonb(NEW)-'status'-'verified_at') IS DISTINCT FROM
    (to_jsonb(OLD)-'status'-'verified_at') OR OLD.status<>'pending' OR
    NEW.status NOT IN ('verified','review_required') OR NEW.verified_at IS NULL
 THEN RAISE EXCEPTION 'Immutable payment attempt'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER branch_payment_attempt_immutable BEFORE UPDATE OR DELETE ON branch_payment_attempts
 FOR EACH ROW EXECUTE FUNCTION protect_branch_payment_attempt();
CREATE FUNCTION protect_branch_online_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Online payment receipts are immutable'; END $$;
CREATE TRIGGER branch_online_receipt_immutable BEFORE UPDATE OR DELETE ON branch_online_receipts
 FOR EACH ROW EXECUTE FUNCTION protect_branch_online_receipt();
