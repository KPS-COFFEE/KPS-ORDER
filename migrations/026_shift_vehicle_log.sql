-- Historical shifts remain unchanged; missing readings are never invented.
ALTER TABLE shifts ADD COLUMN vehicle_log jsonb NOT NULL DEFAULT '{}'::jsonb;
