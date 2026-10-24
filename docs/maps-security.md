# Anonymous map URL resolution (SEC-01)

`POST /api/location/parse-map-url` remains public. Full coordinate links are parsed
without HTTP. Short links use a dedicated async resolver: the existing Google
allowlist, path rules, four-redirect ceiling, TLS-only URLs, no proxy-env trust,
and validation before each next fetch remain in force. Responses are streamed
for headers only; no remote body is consumed. `asyncio.timeout(3)` bounds the
complete outbound operation across DNS/network awaits and all redirects. It is
not a load or availability guarantee for a stopped event loop or unhealthy DB.

Every network miss first commits PostgreSQL admission, shared across application
processes. Per-browser budget: 10 requests/minute (including cached results).
Global outbound budget: 120 misses/minute, global concurrency: 4 leases. Rejection
is HTTP 429 with Retry-After and happens before external HTTP. No DB connection or
transaction is retained during HTTP. Leases are released in `finally` and expire
in 15 seconds if the worker dies. An expired lease cannot cache a late result.

Accepted coordinates only are cached for five minutes, with a hard 256-row bound.
Failures and unresolved Place IDs are not cached. Browser counters expire and
have a hard 4096-row bound. Counter windows use the shared PostgreSQL clock.
Coordination is one short transaction under a service-specific advisory lock;
it does not lock operational orders, users, sessions or shifts.

Browser identity is a random, server-issued, HMAC authenticated HttpOnly,
SameSite=Strict cookie with a database-shared signing key; it has no authentication
or account meaning. The cookie is scoped to `/api/location`, lasts one day and
uses the existing secure-cookie deployment setting. No IP address, forwarded
header, phone number or account identifier is stored in these guard tables. The
short-lived cache contains accepted coordinates keyed by a one-way URL digest;
it must be treated as location data and remains database-internal. Raw proxy
IP buckets would incorrectly throttle every customer behind Render as one user.
This implementation never trusts caller-supplied `Forwarded`/`X-Forwarded-For`.
Uvicorn's deployment proxy settings are unchanged.

Anonymous callers can clear cookies or bootstrap several identities, as they
could rotate IPs. The per-browser limit is therefore a convenience/fairness
limit, not proof of a human identity. Shared global miss/concurrency limits still
bound outbound resource use across new cookies, forged headers and all workers.
A sustained distributed attacker could exhaust that explicit global safety
budget and cause temporary 429s; verified edge-layer client-IP controls can be
reviewed separately after the deployment's trusted proxy chain is established.
No login requirement, production proxy changes or guessed header trust is added.

Migration `078_map_resolution_guards.sql` adds only four dedicated tables and
indexes. The signing key is generated using Python `secrets` upon first use,
never checked into source or exposed in responses. Reverting application code
does not require dropping those tables; retaining them is the safe rollback.

Business exports exclude all four map-runtime tables: they contain unscoped
anonymous identifiers, the HMAC key and transient location data. Existing private
full-database archives include them for complete snapshot/count verification,
using the existing private backup access/storage policy. After an isolated
restore validates every snapshot row/count, these four runtime tables are cleared
and the action is audited; no outstanding lease, location cache or prior signing
key is reactivated. The next map request creates a fresh signing key. Operational
records are retained and their existing restore checks remain unchanged.
