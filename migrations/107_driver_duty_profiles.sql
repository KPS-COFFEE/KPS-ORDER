CREATE TABLE IF NOT EXISTS driver_duty_profiles (
 driver_id text PRIMARY KEY REFERENCES users(id),
 minutes integer NOT NULL CHECK(minutes BETWEEN 30 AND 1440),
 updated_by text NOT NULL REFERENCES users(id),
 updated_at timestamptz NOT NULL DEFAULT now()
);
