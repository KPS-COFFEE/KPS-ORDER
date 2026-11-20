-- Interval HTTP counters are published by each process, never on each request.
-- Canonical monitoring state can therefore include both workers without
-- changing the existing >=3 server-error threshold or provider backup truth.
CREATE TABLE recovery_worker_observations (
  brand_id TEXT NOT NULL REFERENCES brands(id),
  database_id TEXT NOT NULL,
  worker_id TEXT NOT NULL CHECK(worker_id ~ '^[A-Za-z0-9_:-]{1,80}$'),
  window_start TIMESTAMPTZ NOT NULL,
  window_seconds INTEGER NOT NULL CHECK(window_seconds BETWEEN 60 AND 3600),
  checked_at TIMESTAMPTZ NOT NULL,
  requests BIGINT NOT NULL DEFAULT 0 CHECK(requests >= 0),
  server_errors BIGINT NOT NULL DEFAULT 0 CHECK(server_errors >= 0),
  PRIMARY KEY(brand_id,database_id,worker_id,window_start,window_seconds)
);
