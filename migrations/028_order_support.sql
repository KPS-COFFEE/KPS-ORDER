CREATE TABLE support_tickets (
 id text PRIMARY KEY, ticket_no bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 order_id text NOT NULL REFERENCES orders(id), conversation_id text NOT NULL REFERENCES conversations(id),
 category text NOT NULL, status text NOT NULL DEFAULT 'received', note text NOT NULL,
 resolution text NOT NULL DEFAULT '', created_by text NOT NULL REFERENCES users(id),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), closed_at timestamptz,
 CHECK (status IN ('received','in_progress','awaiting_customer','resolved','closed'))
);
CREATE INDEX support_tickets_order ON support_tickets(order_id,status);
CREATE TABLE support_ticket_events (
 id text PRIMARY KEY, ticket_id text NOT NULL REFERENCES support_tickets(id), actor_id text NOT NULL REFERENCES users(id),
 status text NOT NULL, note text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE delivery_photos (
 id text PRIMARY KEY, task_id text NOT NULL REFERENCES driver_tasks(id), order_id text NOT NULL REFERENCES orders(id),
 uploader_id text NOT NULL REFERENCES users(id), stage text NOT NULL CHECK(stage IN ('picked_up','delivered')),
 data bytea, sha256 text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
 used_at timestamptz, deleted_at timestamptz
);
CREATE INDEX delivery_photos_expiry ON delivery_photos(expires_at) WHERE data IS NOT NULL;
