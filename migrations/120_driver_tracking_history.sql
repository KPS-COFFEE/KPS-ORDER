-- Captured time is immutable evidence, never receipt-time freshness.
CREATE TABLE driver_tracking_points (
 id text NOT NULL, driver_id text NOT NULL REFERENCES users(id),
 brand_id text NOT NULL REFERENCES brands(id), shift_id text NOT NULL REFERENCES shifts(id),
 session_id text NOT NULL REFERENCES sessions(id),
 lat double precision NOT NULL CHECK(lat BETWEEN -90 AND 90),
 lng double precision NOT NULL CHECK(lng BETWEEN -180 AND 180),
 accuracy_m double precision NOT NULL CHECK(accuracy_m BETWEEN 0 AND 10000),
 captured_at timestamptz NOT NULL, received_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(driver_id,id), CHECK(captured_at<=received_at+interval '30 seconds')
);
CREATE INDEX driver_tracking_points_shift_capture ON driver_tracking_points(shift_id,captured_at,id);
CREATE TABLE driver_trip_stops (
 id text PRIMARY KEY, trip_id text NOT NULL REFERENCES driver_work_trips(id),
 brand_id text NOT NULL REFERENCES brands(id), shift_id text NOT NULL REFERENCES shifts(id),
 kind text NOT NULL CHECK(kind IN ('pickup','delivery','return')),
 branch_id text REFERENCES branches(id), task_ids jsonb NOT NULL DEFAULT '[]',
 arrived_at timestamptz NOT NULL DEFAULT now(), departed_at timestamptz,
 actor_id text NOT NULL REFERENCES users(id),
 CHECK(departed_at IS NULL OR departed_at>=arrived_at)
);
CREATE UNIQUE INDEX driver_trip_one_open_stop ON driver_trip_stops(trip_id) WHERE departed_at IS NULL;
CREATE INDEX driver_trip_stops_trip ON driver_trip_stops(trip_id,arrived_at);
