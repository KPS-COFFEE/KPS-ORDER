-- One decision per canonical order; revisions are retained independently.
CREATE TABLE IF NOT EXISTS driver_incentive_decisions (
 order_id TEXT PRIMARY KEY REFERENCES orders(id),
 task_id TEXT NOT NULL REFERENCES driver_tasks(id),
 brand_id TEXT NOT NULL REFERENCES brands(id),
 responsibility TEXT NOT NULL CHECK(responsibility IN ('driver','customer','branch')),
 reason TEXT NOT NULL CHECK(length(trim(reason))>=3),
 incident_key TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 decided_by TEXT NOT NULL REFERENCES users(id),
 decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS driver_incentive_decision_events (
 id TEXT PRIMARY KEY,
 order_id TEXT NOT NULL REFERENCES orders(id),
 version INTEGER NOT NULL,
 actor_id TEXT NOT NULL REFERENCES users(id),
 decision JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(order_id,version)
);
