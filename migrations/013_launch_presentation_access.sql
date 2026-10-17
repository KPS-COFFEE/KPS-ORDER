-- Additive launch controls; no operating records are removed or rewritten.
ALTER TABLE users DROP CONSTRAINT users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK(role IN ('owner','admin','delivery_manager','delivery_staff','branch_manager','driver','customer'));
ALTER TABLE users ADD COLUMN permissions JSONB NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(permissions)='array');
ALTER TABLE banners ADD COLUMN cities JSONB NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(cities)='array');
ALTER TABLE banners ADD COLUMN rotation_ms INTEGER NOT NULL DEFAULT 6500 CHECK(rotation_ms BETWEEN 4000 AND 90000);
-- Upgrade only the former default phrase; retain owner's custom presentation.
UPDATE app_settings SET settings=jsonb_set(settings,'{homepage_presentation}',
  coalesce(settings->'homepage_presentation','{}'::jsonb) || '{"ticker_lines":["الأماكن والأوقات تجمّلها القهوة دائمًا","Speciality Coffee"]}'::jsonb)
WHERE brand_id='kps' AND (settings->'homepage_presentation'->'ticker_lines' IS NULL
  OR settings->'homepage_presentation'->'ticker_lines'='["طلبك، على طريقتك"]'::jsonb);
