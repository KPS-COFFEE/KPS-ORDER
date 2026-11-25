-- Empty additive tables. No mode, operational row or counter is changed.
CREATE TABLE dispatch_offers (
 id text PRIMARY KEY,
 brand_id text NOT NULL REFERENCES brands(id),
 order_id text NOT NULL REFERENCES orders(id),
 driver_id text NOT NULL REFERENCES users(id),
 shift_id text NOT NULL REFERENCES shifts(id),
 offered_by text NOT NULL REFERENCES users(id),
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','declined','expired','superseded')),
 expires_at timestamptz NOT NULL,
 task_id text REFERENCES driver_tasks(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX dispatch_offers_inbox ON dispatch_offers(driver_id,status,expires_at);
CREATE TABLE dispatch_offer_events (
 id text PRIMARY KEY,
 brand_id text NOT NULL REFERENCES brands(id),
 offer_id text NOT NULL REFERENCES dispatch_offers(id),
 actor_id text NOT NULL REFERENCES users(id),
 action text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER dispatch_offer_events_immutable BEFORE UPDATE OR DELETE ON dispatch_offer_events
 FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
