-- Minimal durable notification inbox: never store webhook secrets or card data.
CREATE TABLE branch_payment_webhooks (
 id TEXT PRIMARY KEY,
 brand_id TEXT NOT NULL REFERENCES brands(id),
 attempt_id TEXT NOT NULL REFERENCES branch_payment_attempts(id),
 provider_event_id TEXT NOT NULL,
 event_type TEXT NOT NULL CHECK(event_type IN ('payment_paid','payment_captured')),
 received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 processed_at TIMESTAMPTZ,
 next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 lease_id TEXT,
 lease_until TIMESTAMPTZ,
 attempts INTEGER NOT NULL DEFAULT 0,
 UNIQUE(attempt_id,provider_event_id)
);
CREATE INDEX branch_payment_webhooks_pending ON branch_payment_webhooks(next_attempt_at)
 WHERE processed_at IS NULL;
CREATE FUNCTION protect_branch_payment_webhook() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Webhook history cannot be deleted'; END IF;
 IF NEW.id<>OLD.id OR NEW.brand_id<>OLD.brand_id OR NEW.attempt_id<>OLD.attempt_id OR
    NEW.provider_event_id<>OLD.provider_event_id OR NEW.event_type<>OLD.event_type OR
    NEW.received_at<>OLD.received_at OR OLD.processed_at IS NOT NULL
 THEN RAISE EXCEPTION 'Immutable webhook history'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER branch_payment_webhook_immutable BEFORE UPDATE OR DELETE ON branch_payment_webhooks
 FOR EACH ROW EXECUTE FUNCTION protect_branch_payment_webhook();
