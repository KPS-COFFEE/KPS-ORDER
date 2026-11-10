-- Forward-only multibrand constraints. Applied only behind the existing
-- isolated or exact-release runtime guard; never rewrite customer history.
-- A legacy mismatch must fail migration and be reviewed, not silently repaired.
ALTER TABLE customer_addresses ADD CONSTRAINT dual_address_customer_brand
 FOREIGN KEY(brand_id,customer_id) REFERENCES users(brand_id,id);
ALTER TABLE customer_identity_links ADD CONSTRAINT dual_alias_source_brand
 FOREIGN KEY(brand_id,source_id) REFERENCES users(brand_id,id);
ALTER TABLE customer_identity_links ADD CONSTRAINT dual_alias_customer_brand
 FOREIGN KEY(brand_id,customer_id) REFERENCES users(brand_id,id);
ALTER TABLE orders ADD CONSTRAINT dual_order_customer_brand
 FOREIGN KEY(brand_id,customer_id) REFERENCES users(brand_id,id);

-- Existing verified guest aliases are exclusively within one brand. They are
-- not the future TILQA account-linking mechanism and cannot import ownership.
-- Staff memberships intentionally do not use these constraints: an explicit
-- shared employee grant references the original employee identity.
