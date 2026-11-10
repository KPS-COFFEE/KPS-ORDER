-- Timings and accuracy only. No route coordinate history or out-of-shift samples.
CREATE TABLE IF NOT EXISTS driver_location_health (
 brand_id TEXT NOT NULL REFERENCES brands(id),
 driver_id TEXT NOT NULL REFERENCES users(id),
 shift_id TEXT NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
 minute TIMESTAMPTZ NOT NULL,
 received_count INTEGER NOT NULL CHECK(received_count>0),
 max_gap_seconds INTEGER NOT NULL CHECK(max_gap_seconds>=0),
 worst_accuracy_m DOUBLE PRECISION NOT NULL CHECK(worst_accuracy_m>=0),
 PRIMARY KEY(shift_id,minute)
);
CREATE INDEX IF NOT EXISTS driver_location_health_brand ON driver_location_health(brand_id,minute);
