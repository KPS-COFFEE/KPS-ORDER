-- Preserve every prior attempt; only a safely retired attempt releases its slot.
ALTER TABLE branch_payment_attempts DROP CONSTRAINT branch_payment_attempts_order_id_key;
ALTER TABLE branch_payment_attempts DROP CONSTRAINT branch_payment_attempts_status_check;
ALTER TABLE branch_payment_attempts ADD CHECK(status IN ('pending','verified','review_required','superseded'));
CREATE UNIQUE INDEX branch_payment_active_order ON branch_payment_attempts(order_id) WHERE status<>'superseded';
CREATE OR REPLACE FUNCTION protect_branch_payment_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Payment attempts cannot be deleted'; END IF;
 IF (to_jsonb(NEW)-'status'-'verified_at') IS DISTINCT FROM
    (to_jsonb(OLD)-'status'-'verified_at') OR OLD.status<>'pending' OR
    NOT ((NEW.status IN ('verified','review_required') AND NEW.verified_at IS NOT NULL) OR
         (NEW.status='superseded' AND NEW.verified_at IS NULL))
 THEN RAISE EXCEPTION 'Immutable payment attempt'; END IF;
 RETURN NEW;
END $$;
CREATE TABLE branch_payment_renewals (
 previous_attempt_id TEXT PRIMARY KEY REFERENCES branch_payment_attempts(id),
 next_attempt_id TEXT NOT NULL UNIQUE REFERENCES branch_payment_attempts(id),
 brand_id TEXT NOT NULL REFERENCES brands(id),
 reason TEXT NOT NULL CHECK(reason IN ('never_exposed','provider_expired','provider_canceled')),
 actor_id TEXT NOT NULL REFERENCES users(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER branch_payment_renewal_immutable BEFORE UPDATE OR DELETE ON branch_payment_renewals
 FOR EACH ROW EXECUTE FUNCTION protect_branch_online_receipt();
