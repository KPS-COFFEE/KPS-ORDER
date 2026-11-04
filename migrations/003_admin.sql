CREATE TABLE IF NOT EXISTS banners (
 id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brands(id),
 title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', button_label TEXT NOT NULL DEFAULT '',
 destination JSONB NOT NULL DEFAULT '{}'::jsonb,
 desktop_asset_id TEXT REFERENCES assets(id), mobile_asset_id TEXT REFERENCES assets(id),
 branch_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(branch_ids)='array'),
 active BOOLEAN NOT NULL DEFAULT false, sort_order INTEGER NOT NULL DEFAULT 0,
 starts_at TIMESTAMPTZ, ends_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CHECK(ends_at IS NULL OR starts_at IS NULL OR ends_at>starts_at)
);
CREATE INDEX IF NOT EXISTS banners_brand_schedule ON banners(brand_id,active,starts_at,ends_at);
