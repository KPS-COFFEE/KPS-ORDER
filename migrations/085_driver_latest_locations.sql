-- A bounded latest observation only; never accumulates driver route history.
CREATE TABLE IF NOT EXISTS driver_latest_locations (
    driver_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    brand_id TEXT NOT NULL REFERENCES brands(id),
    shift_id TEXT NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
    lat DOUBLE PRECISION NOT NULL CHECK (lat BETWEEN -90 AND 90),
    lng DOUBLE PRECISION NOT NULL CHECK (lng BETWEEN -180 AND 180),
    accuracy_m DOUBLE PRECISION NOT NULL CHECK (accuracy_m BETWEEN 0 AND 10000),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS driver_latest_locations_brand ON driver_latest_locations(brand_id);
