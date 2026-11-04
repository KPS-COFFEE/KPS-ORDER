-- A shared manager succession has one atomic acceptance and canonical brand legs.
CREATE TABLE central_manager_handovers (
 id text PRIMARY KEY,
 from_workday_id text NOT NULL REFERENCES central_workdays(id),
 to_workday_id text NOT NULL REFERENCES central_workdays(id),
 created_by text NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK (from_workday_id <> to_workday_id)
);
CREATE TABLE central_manager_handover_legs (
 handover_group_id text NOT NULL REFERENCES central_manager_handovers(id),
 brand_id text NOT NULL REFERENCES brands(id),
 handover_id text NOT NULL UNIQUE REFERENCES manager_handovers(id),
 PRIMARY KEY (handover_group_id,brand_id)
);
CREATE INDEX central_manager_handovers_source ON central_manager_handovers(from_workday_id,created_at DESC);
CREATE INDEX central_manager_handovers_target ON central_manager_handovers(to_workday_id,created_at DESC);
