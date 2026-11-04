-- Dedicated, bounded anonymous-map safety state. No operational data changes.
CREATE TABLE map_resolution_state (
 id integer PRIMARY KEY CHECK(id=1),
 signing_key text NOT NULL CHECK(length(signing_key)=64),
 bucket timestamptz NOT NULL DEFAULT '2000-01-01T00:00:00Z',
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0)
);
CREATE TABLE map_resolution_clients (
 id text PRIMARY KEY CHECK(length(id)=64), bucket timestamptz NOT NULL,
 attempts integer NOT NULL CHECK(attempts>=0), expires_at timestamptz NOT NULL
);
CREATE INDEX map_resolution_clients_expiry ON map_resolution_clients(expires_at);
CREATE TABLE map_resolution_leases (
 id text PRIMARY KEY, expires_at timestamptz NOT NULL
);
CREATE TABLE map_resolution_cache (
 url_hash text PRIMARY KEY CHECK(length(url_hash)=64),
 result jsonb NOT NULL CHECK(jsonb_typeof(result)='object'), expires_at timestamptz NOT NULL
);
CREATE INDEX map_resolution_cache_expiry ON map_resolution_cache(expires_at);
