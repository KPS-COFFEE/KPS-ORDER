-- Additive sandbox refund authorization and durable single-submit claims.
CREATE TABLE branch_online_refunds (
 id TEXT PRIMARY KEY,
 attempt_id TEXT NOT NULL REFERENCES branch_payment_attempts(id),
 order_id TEXT NOT NULL REFERENCES orders(id),
 brand_id TEXT NOT NULL REFERENCES brands(id),
 branch_id TEXT NOT NULL REFERENCES branches(id),
 amount_halalas BIGINT NOT NULL CHECK(amount_halalas>0),
 previous_refunded_halalas BIGINT NOT NULL CHECK(previous_refunded_halalas>=0),
 reason TEXT NOT NULL,
 authorized_by TEXT NOT NULL REFERENCES users(id),
 status TEXT NOT NULL DEFAULT 'requested' CHECK(status IN ('requested','submitting','uncertain','verified')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 verified_at TIMESTAMPTZ,
 CHECK ((status='verified') = (verified_at IS NOT NULL))
);
CREATE UNIQUE INDEX branch_online_refund_inflight ON branch_online_refunds(order_id) WHERE status<>'verified';
CREATE FUNCTION protect_branch_online_refund() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Refund history cannot be deleted'; END IF;
 IF (to_jsonb(NEW)-'status'-'verified_at') IS DISTINCT FROM (to_jsonb(OLD)-'status'-'verified_at') OR
    NOT ((OLD.status='requested' AND NEW.status='submitting') OR
         (OLD.status='submitting' AND NEW.status IN ('uncertain','verified')) OR
         (OLD.status='uncertain' AND NEW.status='verified'))
 THEN RAISE EXCEPTION 'Immutable refund authorization'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER branch_online_refund_immutable BEFORE UPDATE OR DELETE ON branch_online_refunds
 FOR EACH ROW EXECUTE FUNCTION protect_branch_online_refund();
