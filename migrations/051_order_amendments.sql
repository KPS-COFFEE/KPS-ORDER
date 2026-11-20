-- Accepted revisions are append-only. The original survives in revision 1's before fields.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS amendment_revision integer NOT NULL DEFAULT 0;
CREATE TABLE order_amendments (
 id text PRIMARY KEY, order_id text NOT NULL REFERENCES orders(id),
 requested_by text NOT NULL REFERENCES users(id),
 base_revision integer NOT NULL, proposal jsonb NOT NULL,
 status text NOT NULL CHECK(status IN ('requested','branch_confirmed','offered','applied','rejected','withdrawn')),
 reason text NOT NULL, reviewed_by text REFERENCES users(id), branch_confirmed_by text REFERENCES users(id),
 consent_by text REFERENCES users(id), consent_channel text CHECK(consent_channel IN ('customer','phone','whatsapp')),
 consent_note text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL DEFAULT now()+interval '15 minutes', updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX order_amendments_order ON order_amendments(order_id,created_at);
CREATE TABLE order_revisions (
 order_id text NOT NULL REFERENCES orders(id), revision integer NOT NULL CHECK(revision>0),
 amendment_id text NOT NULL UNIQUE REFERENCES order_amendments(id),
 before_snapshot jsonb NOT NULL, before_customer jsonb NOT NULL,
 after_snapshot jsonb NOT NULL, after_customer jsonb NOT NULL,
 before_total bigint NOT NULL, after_total bigint NOT NULL,
 approved_by text NOT NULL REFERENCES users(id), consent_by text NOT NULL REFERENCES users(id),
 consent_channel text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(order_id,revision)
);
CREATE TABLE order_revision_receipts (
 order_id text NOT NULL, revision integer NOT NULL, actor_id text NOT NULL REFERENCES users(id),
 kind text NOT NULL CHECK(kind IN ('branch','driver')), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(order_id,revision,kind,actor_id),
 FOREIGN KEY(order_id,revision) REFERENCES order_revisions(order_id,revision)
);
CREATE OR REPLACE FUNCTION protect_amendment_history() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'Order revision history is append-only'; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER immutable_order_revisions BEFORE UPDATE OR DELETE ON order_revisions FOR EACH ROW EXECUTE FUNCTION protect_amendment_history();
CREATE TRIGGER immutable_revision_receipts BEFORE UPDATE OR DELETE ON order_revision_receipts FOR EACH ROW EXECUTE FUNCTION protect_amendment_history();
CREATE OR REPLACE FUNCTION protect_order_snapshot() RETURNS trigger AS $$
BEGIN
 IF NEW.brand_id<>OLD.brand_id OR NEW.branch_id<>OLD.branch_id OR NEW.customer_id<>OLD.customer_id OR NEW.service<>OLD.service THEN
   RAISE EXCEPTION 'Confirmed order ownership and fulfillment are immutable';
 END IF;
 IF NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.customer IS DISTINCT FROM OLD.customer
    OR NEW.subtotal_halalas<>OLD.subtotal_halalas OR NEW.delivery_fee_halalas<>OLD.delivery_fee_halalas
    OR NEW.total_halalas<>OLD.total_halalas OR NEW.amendment_revision<>OLD.amendment_revision THEN
   IF NEW.amendment_revision<>OLD.amendment_revision+1 OR NOT EXISTS (
     SELECT 1 FROM order_revisions r JOIN order_amendments a ON a.id=r.amendment_id
     WHERE r.order_id=OLD.id AND r.revision=NEW.amendment_revision AND a.status='applied'
       AND a.base_revision=OLD.amendment_revision AND a.reviewed_by=r.approved_by
       AND a.consent_by=r.consent_by AND a.consent_channel=r.consent_channel
       AND r.before_snapshot=OLD.snapshot AND r.before_customer=OLD.customer AND r.before_total=OLD.total_halalas
       AND r.after_snapshot=NEW.snapshot AND r.after_customer=NEW.customer AND r.after_total=NEW.total_halalas
       AND (NEW.snapshot->>'subtotal_halalas')::bigint=NEW.subtotal_halalas
       AND (NEW.snapshot->>'delivery_fee_halalas')::bigint=NEW.delivery_fee_halalas
       AND (NEW.snapshot->>'total_halalas')::bigint=NEW.total_halalas
   ) THEN RAISE EXCEPTION 'Confirmed order snapshot requires an approved immutable revision'; END IF;
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
ALTER TABLE collections ADD COLUMN IF NOT EXISTS amendment_revision integer NOT NULL DEFAULT 0;
DROP INDEX IF EXISTS collections_order_method_unique;
CREATE UNIQUE INDEX collections_order_revision_method_unique ON collections(order_id,amendment_revision,method);
ALTER TABLE refunds ADD COLUMN IF NOT EXISTS amendment_id text REFERENCES order_amendments(id);
