CREATE TABLE branch_print_devices (
 id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brands(id),
 branch_id TEXT NOT NULL REFERENCES branches(id), label TEXT NOT NULL,
 token_hash TEXT UNIQUE, pair_hash TEXT UNIQUE, pair_expires_at TIMESTAMPTZ,
 printer_name TEXT, paper_mm INTEGER NOT NULL DEFAULT 80 CHECK(paper_mm IN (58,80)),
 created_by TEXT NOT NULL REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 last_seen_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ
);
CREATE TABLE branch_print_jobs (
 id TEXT PRIMARY KEY, brand_id TEXT NOT NULL REFERENCES brands(id),
 branch_id TEXT NOT NULL REFERENCES branches(id), device_id TEXT NOT NULL REFERENCES branch_print_devices(id),
 order_id TEXT NOT NULL REFERENCES orders(id), kind TEXT NOT NULL CHECK(kind IN ('order','preparation')),
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','claimed','submitted','uncertain','cancelled')),
 payload JSONB NOT NULL, requested_by TEXT NOT NULL REFERENCES users(id),
 reprint_reason TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 claimed_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
 CHECK ((status='queued') OR claimed_at IS NOT NULL OR status='cancelled')
);
CREATE INDEX branch_print_queue ON branch_print_jobs(device_id,created_at) WHERE status='queued';
