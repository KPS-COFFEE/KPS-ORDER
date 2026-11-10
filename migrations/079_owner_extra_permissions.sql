-- Additive only: no existing role, branch, approval, denial or grant is changed.
ALTER TABLE users ADD COLUMN IF NOT EXISTS extra_permissions jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE users ADD CONSTRAINT users_extra_permissions_array
    CHECK (jsonb_typeof(extra_permissions) = 'array');
