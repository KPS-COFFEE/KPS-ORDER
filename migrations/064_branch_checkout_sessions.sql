-- Durable sandbox checkout claims. No service flags or existing orders change.
CREATE TABLE branch_checkout_sessions (
 attempt_id TEXT PRIMARY KEY REFERENCES branch_payment_attempts(id),
 brand_id TEXT NOT NULL REFERENCES brands(id),
 state TEXT NOT NULL DEFAULT 'creating' CHECK(state IN ('creating','ready','uncertain')),
 provider_intent_id UUID UNIQUE,
 encrypted_secret BYTEA,
 callback_url TEXT,
 expires_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK ((state='ready') = (provider_intent_id IS NOT NULL AND encrypted_secret IS NOT NULL AND callback_url IS NOT NULL AND expires_at IS NOT NULL))
);
CREATE FUNCTION protect_branch_checkout_session() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Checkout history cannot be deleted'; END IF;
 IF OLD.state<>'creating' OR NEW.state NOT IN ('ready','uncertain') OR
    NEW.attempt_id<>OLD.attempt_id OR NEW.brand_id<>OLD.brand_id OR NEW.created_at<>OLD.created_at
 THEN RAISE EXCEPTION 'Immutable checkout session'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER branch_checkout_session_immutable BEFORE UPDATE OR DELETE ON branch_checkout_sessions
 FOR EACH ROW EXECUTE FUNCTION protect_branch_checkout_session();
