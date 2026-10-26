-- Staff-only routing cache. Every read rechecks current driver/branch permissions.
CREATE TABLE IF NOT EXISTS driver_route_eta_state (
 driver_id TEXT NOT NULL REFERENCES users(id),
 manager_id TEXT NOT NULL REFERENCES users(id),
 fingerprint TEXT NOT NULL,
 matrix JSONB,
 calculated_at TIMESTAMPTZ,
 origin_updated_at TIMESTAMPTZ,
 ordered_stop_ids JSONB NOT NULL DEFAULT '[]',
 return_branch_id TEXT,
 published_at TIMESTAMPTZ,
 published_stop_ids JSONB NOT NULL DEFAULT '[]',
 published_stops JSONB NOT NULL DEFAULT '[]',
 published_matrix JSONB,
 published_calculated_at TIMESTAMPTZ,
 published_shift_ids JSONB NOT NULL DEFAULT '[]',
 source_session_id TEXT REFERENCES sessions(id),
 manager_brand_id TEXT REFERENCES brands(id),
 central_scope BOOLEAN NOT NULL DEFAULT false,
 morning_shift_id TEXT REFERENCES shifts(id),
 PRIMARY KEY(driver_id,manager_id)
);
CREATE TABLE IF NOT EXISTS driver_route_eta_expectations (
 id TEXT PRIMARY KEY,
 driver_id TEXT NOT NULL REFERENCES users(id),
 manager_id TEXT NOT NULL REFERENCES users(id),
 calculated_at TIMESTAMPTZ NOT NULL,
 stops JSONB NOT NULL,
 duration_basis TEXT NOT NULL CHECK(duration_basis='road_time_plus_explicit_stop_estimates')
);
CREATE INDEX IF NOT EXISTS driver_route_eta_expected_driver ON driver_route_eta_expectations(driver_id,calculated_at);

-- Optional staff-only reference corridor; retired with the route cache on restore.
ALTER TABLE driver_route_eta_state ADD COLUMN IF NOT EXISTS road_corridor JSONB;
