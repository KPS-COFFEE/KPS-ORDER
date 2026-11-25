-- Additive overrides: existing staff retain their approved role access.
ALTER TABLE users ADD COLUMN IF NOT EXISTS permission_denials jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE users ADD CONSTRAINT users_permission_denials_array
    CHECK (jsonb_typeof(permission_denials) = 'array');
