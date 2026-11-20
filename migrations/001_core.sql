CREATE TABLE IF NOT EXISTS brands (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT true,
  settings JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS branches (
  id TEXT PRIMARY KEY, branch_no INTEGER NOT NULL CHECK(branch_no > 0),
  source_id TEXT NOT NULL, brand_id TEXT NOT NULL REFERENCES brands(id), name TEXT NOT NULL,
  city TEXT NOT NULL, settings JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(brand_id,branch_no), UNIQUE(brand_id,source_id)
);
CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brands(id), name TEXT NOT NULL,
  category TEXT NOT NULL, price_halalas BIGINT NOT NULL CHECK(price_halalas>=0),
  settings JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, username TEXT UNIQUE, display_name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL CHECK(role IN ('owner','delivery_manager','delivery_staff','branch_manager','driver','customer')),
  brand_id TEXT NOT NULL REFERENCES brands(id), branch_ids JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK(jsonb_typeof(branch_ids)='array'),
  phone TEXT, password_hash TEXT, active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS users_customer_phone ON users(brand_id,phone) WHERE role='customer';
CREATE UNIQUE INDEX IF NOT EXISTS users_username_casefold ON users(lower(username)) WHERE username IS NOT NULL;
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, csrf_hash TEXT NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(id), expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS otp_challenges (
  id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brands(id), phone TEXT NOT NULL,
  provider_reference TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0), verified_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS rate_limits (
  bucket TEXT NOT NULL, window_start BIGINT NOT NULL, hits INTEGER NOT NULL,
  PRIMARY KEY(bucket,window_start)
);
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brands(id),
  branch_id TEXT NOT NULL REFERENCES branches(id), customer_id TEXT NOT NULL REFERENCES users(id),
  service TEXT NOT NULL CHECK(service IN ('delivery','pickup','car')),
  preparation_status TEXT NOT NULL DEFAULT 'new', delivery_status TEXT NOT NULL,
  payment_status TEXT NOT NULL DEFAULT 'unpaid', payment_method TEXT NOT NULL CHECK(payment_method IN ('cash','card')),
  subtotal_halalas BIGINT NOT NULL CHECK(subtotal_halalas>=0),
  delivery_fee_halalas BIGINT NOT NULL CHECK(delivery_fee_halalas>=0),
  total_halalas BIGINT NOT NULL CHECK(total_halalas=subtotal_halalas+delivery_fee_halalas),
  snapshot JSONB NOT NULL, customer JSONB NOT NULL, arrival_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), completed_at TIMESTAMPTZ,
  version INTEGER NOT NULL DEFAULT 1 CHECK(version>0)
);
CREATE INDEX IF NOT EXISTS orders_branch_created ON orders(branch_id,created_at DESC);
CREATE INDEX IF NOT EXISTS orders_brand_service_created ON orders(brand_id,service,created_at DESC);
CREATE INDEX IF NOT EXISTS orders_customer_created ON orders(customer_id,created_at DESC);
CREATE TABLE IF NOT EXISTS order_events (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), actor_id TEXT REFERENCES users(id),
  event_type TEXT NOT NULL, details JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS order_events_order ON order_events(order_id,created_at);
CREATE TABLE IF NOT EXISTS idempotency_keys (
  user_id TEXT NOT NULL REFERENCES users(id), operation TEXT NOT NULL, key TEXT NOT NULL,
  request_digest TEXT NOT NULL, response JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(user_id,operation,key)
);
CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY, actor_id TEXT REFERENCES users(id), action TEXT NOT NULL,
  entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, details JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_entity ON audit_events(entity_type,entity_id,created_at);
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brands(id), owner_id TEXT REFERENCES users(id),
  purpose TEXT NOT NULL, mime_type TEXT NOT NULL, filename TEXT NOT NULL,
  contents BYTEA NOT NULL, sha256 TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app_settings (
  brand_id TEXT PRIMARY KEY REFERENCES brands(id), settings JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION protect_order_snapshot() RETURNS trigger AS $$
BEGIN
 IF NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.customer IS DISTINCT FROM OLD.customer
    OR NEW.subtotal_halalas<>OLD.subtotal_halalas OR NEW.delivery_fee_halalas<>OLD.delivery_fee_halalas
    OR NEW.total_halalas<>OLD.total_halalas OR NEW.branch_id<>OLD.branch_id
    OR NEW.brand_id<>OLD.brand_id OR NEW.customer_id<>OLD.customer_id THEN
   RAISE EXCEPTION 'Confirmed order snapshot is immutable';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgname='immutable_order_snapshot' AND tgrelid='orders'::regclass) THEN
   CREATE TRIGGER immutable_order_snapshot BEFORE UPDATE ON orders FOR EACH ROW EXECUTE FUNCTION protect_order_snapshot();
 END IF;
END $$;
