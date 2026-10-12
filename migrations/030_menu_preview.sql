-- Additive, first-party analytics. Preview journeys are isolated from live metrics.
CREATE TABLE menu_sessions (
 id text PRIMARY KEY, brand_id text NOT NULL REFERENCES brands(id),
 preview boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL DEFAULT now()+interval '30 days'
);
CREATE TABLE menu_events (
 id text PRIMARY KEY, session_id text NOT NULL REFERENCES menu_sessions(id),
 event_type text NOT NULL CHECK(event_type IN ('visit','category_view','product_view','order_click','order_start','branches_open','branch_view','maps_click','review_click','social_click','contact_click')),
 target text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX menu_events_session ON menu_events(session_id,created_at);
CREATE INDEX menu_events_range ON menu_events(created_at,event_type);
CREATE TABLE menu_handoffs (
 id text PRIMARY KEY, session_id text NOT NULL REFERENCES menu_sessions(id),
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL DEFAULT now()+interval '24 hours',
 started_at timestamptz
);
CREATE TABLE menu_order_attribution (
 order_id text PRIMARY KEY REFERENCES orders(id), session_id text NOT NULL REFERENCES menu_sessions(id),
 handoff_id text NOT NULL REFERENCES menu_handoffs(id), created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE menu_link_imports (brand_id text PRIMARY KEY REFERENCES brands(id), imported_at timestamptz NOT NULL DEFAULT now());
