CREATE TABLE IF NOT EXISTS driver_duty_cities (
  driver_id TEXT PRIMARY KEY REFERENCES users(id),
  city TEXT NOT NULL CHECK (length(city) BETWEEN 1 AND 100),
  approved_by TEXT NOT NULL REFERENCES users(id),
  reason TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
