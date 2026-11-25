CREATE TABLE driver_break_policies (
 manager_id text PRIMARY KEY REFERENCES users(id),brand_id text NOT NULL REFERENCES brands(id),
 policy jsonb NOT NULL,updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE driver_breaks (
 id text PRIMARY KEY,brand_id text NOT NULL REFERENCES brands(id),shift_id text NOT NULL REFERENCES shifts(id),
 driver_id text NOT NULL REFERENCES users(id),reason text NOT NULL,notes text NOT NULL DEFAULT '',
 requested_minutes integer NOT NULL CHECK(requested_minutes>0),approved_minutes integer,
 status text NOT NULL CHECK(status IN ('requested','active','ended','rejected')),
 previous_available boolean NOT NULL,requested_at timestamptz NOT NULL DEFAULT now(),
 started_at timestamptz,ends_at timestamptz,ended_at timestamptz
);
CREATE UNIQUE INDEX driver_break_one_unfinished ON driver_breaks(shift_id) WHERE status IN ('requested','active');
CREATE TABLE driver_break_events (
 id text PRIMARY KEY,brand_id text NOT NULL REFERENCES brands(id),break_id text NOT NULL REFERENCES driver_breaks(id),
 actor_id text NOT NULL REFERENCES users(id),action text NOT NULL,details jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER driver_break_history BEFORE UPDATE OR DELETE ON driver_break_events FOR EACH ROW EXECUTE FUNCTION prevent_financial_mutation();
