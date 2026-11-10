-- Preserve existing immutable collection rows; allow one leg per method.
ALTER TABLE collections DROP CONSTRAINT IF EXISTS collections_order_id_key;
CREATE UNIQUE INDEX IF NOT EXISTS collections_order_method_unique ON collections(order_id,method);
ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_payment_method_check;
ALTER TABLE orders ADD CONSTRAINT orders_payment_method_check CHECK(payment_method IN ('cash','card','split'));
