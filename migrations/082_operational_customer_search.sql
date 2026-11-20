-- Branch-scoped exact mobile search; no destructive data migration or identity linking.
CREATE INDEX IF NOT EXISTS orders_operational_phone_lookup ON orders(brand_id,branch_id,normalized_identity_phone(customer->>'phone'),created_at DESC);
