-- Optional staff-only reference corridor; retired with route cache on restore.
ALTER TABLE driver_route_eta_state ADD COLUMN IF NOT EXISTS road_corridor JSONB;
