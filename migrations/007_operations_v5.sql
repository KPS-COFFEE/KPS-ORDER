-- Additive V5 operation metadata. Existing order IDs and financial snapshots stay intact.
CREATE SEQUENCE IF NOT EXISTS order_human_number_seq;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS order_no BIGINT;
WITH historical AS (
 SELECT id,row_number() OVER (ORDER BY created_at,id) AS n FROM orders WHERE order_no IS NULL
), base AS (SELECT coalesce(max(order_no),0) n FROM orders)
UPDATE orders o SET order_no=h.n+b.n FROM historical h,base b WHERE o.id=h.id;
SELECT setval('order_human_number_seq',greatest(coalesce((SELECT max(order_no) FROM orders),0),1),
 coalesce((SELECT max(order_no) FROM orders),0)>0);
ALTER TABLE orders ALTER COLUMN order_no SET DEFAULT nextval('order_human_number_seq');
ALTER TABLE orders ALTER COLUMN order_no SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS orders_human_number ON orders(order_no);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS performance_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS orders_neighborhood ON orders(brand_id,(customer->>'neighborhood'),created_at);
CREATE INDEX IF NOT EXISTS orders_active_queue ON orders(brand_id,created_at,order_no)
 WHERE preparation_status NOT IN ('completed','cancelled') AND delivery_status NOT IN ('delivered','cancelled');
CREATE TABLE IF NOT EXISTS performance_provinces (
 brand_id TEXT NOT NULL REFERENCES brands(id),id TEXT NOT NULL,name TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(brand_id,id)
);
CREATE TABLE IF NOT EXISTS performance_province_branches (
 brand_id TEXT NOT NULL,province_id TEXT NOT NULL,branch_id TEXT PRIMARY KEY REFERENCES branches(id),
 FOREIGN KEY(brand_id,province_id) REFERENCES performance_provinces(brand_id,id)
);
CREATE TABLE IF NOT EXISTS province_performance_policies (
 id TEXT PRIMARY KEY,brand_id TEXT NOT NULL,province_id TEXT NOT NULL,
 metric TEXT NOT NULL CHECK(metric IN ('delivery','total')),version INTEGER NOT NULL CHECK(version>0),
 t1_seconds INTEGER NOT NULL CHECK(t1_seconds>0),t2_seconds INTEGER NOT NULL CHECK(t2_seconds>t1_seconds AND t2_seconds<=86400),
 effective_at TIMESTAMPTZ NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(brand_id,province_id) REFERENCES performance_provinces(brand_id,id),
 UNIQUE(brand_id,province_id,metric,version)
);
CREATE INDEX IF NOT EXISTS performance_policy_effective ON province_performance_policies(brand_id,province_id,metric,effective_at DESC,version DESC);
CREATE OR REPLACE FUNCTION protect_v5_order_metadata() RETURNS trigger AS $$ BEGIN
 IF NEW.order_no IS DISTINCT FROM OLD.order_no OR NEW.performance_snapshot IS DISTINCT FROM OLD.performance_snapshot THEN
  RAISE EXCEPTION 'Order number and confirmed performance policy are immutable';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_v5_order_metadata' AND tgrelid='orders'::regclass) THEN
  CREATE TRIGGER immutable_v5_order_metadata BEFORE UPDATE ON orders FOR EACH ROW EXECUTE FUNCTION protect_v5_order_metadata();
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_performance_policy' AND tgrelid='province_performance_policies'::regclass) THEN
  CREATE TRIGGER immutable_performance_policy BEFORE UPDATE OR DELETE ON province_performance_policies FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS closure_drafts (
 shift_id TEXT PRIMARY KEY REFERENCES shifts(id), user_id TEXT NOT NULL REFERENCES users(id),
 draft JSONB NOT NULL DEFAULT '{}'::jsonb, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>0),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE shifts ADD COLUMN IF NOT EXISTS available BOOLEAN NOT NULL DEFAULT true;
