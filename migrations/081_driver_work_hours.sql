-- Explicit server-timed trips. Historical orders are never backfilled as trips.
CREATE TABLE driver_work_trips (
 id text PRIMARY KEY, brand_id text NOT NULL REFERENCES brands(id),
 shift_id text NOT NULL REFERENCES shifts(id), driver_id text NOT NULL REFERENCES users(id),
 departure_branch_id text NOT NULL REFERENCES branches(id), return_branch_id text REFERENCES branches(id),
 departed_at timestamptz NOT NULL DEFAULT now(), returned_at timestamptz,
 departure_evidence jsonb NOT NULL, return_evidence jsonb,
 CHECK ((returned_at IS NULL AND return_branch_id IS NULL) OR (returned_at>=departed_at AND return_branch_id IS NOT NULL))
);
CREATE UNIQUE INDEX driver_work_one_open_trip ON driver_work_trips(driver_id) WHERE returned_at IS NULL;
CREATE TABLE driver_work_trip_orders (
 trip_id text NOT NULL REFERENCES driver_work_trips(id), order_id text NOT NULL REFERENCES orders(id),
 PRIMARY KEY(trip_id,order_id)
);
CREATE TABLE driver_work_trip_events (
 id text PRIMARY KEY, trip_id text NOT NULL REFERENCES driver_work_trips(id),
 actor_id text NOT NULL REFERENCES users(id), action text NOT NULL CHECK(action IN ('depart','return')),
 branch_id text NOT NULL REFERENCES branches(id), evidence jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER driver_work_trip_events_immutable BEFORE UPDATE OR DELETE ON driver_work_trip_events FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
