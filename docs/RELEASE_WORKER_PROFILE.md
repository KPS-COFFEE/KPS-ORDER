# Two-worker emergency release profile

The release Docker command now starts **two Uvicorn web processes in one
existing Render service**. This is not a second Render instance, a new paid
service, or a background job worker. PostgreSQL, durable idempotency keys,
branch receipt counters and existing transactional outboxes remain shared.
Each process owns its bounded pool (maximum 20 connections each).

Both Docker and the isolated harness keep idle HTTP connections for 15 seconds,
instead of closing them at the HTTP client's same five-second expiry boundary.
This changes **idle socket lifetime only**, not the latency acceptance limits,
request timeout, statement timeout, retry allowance or error accounting. A
real-socket regression verifies reuse across the old five-second boundary. It
does not, by itself, prove the cause of every observed transport reset; both
complete load gates still have to pass with all raw failures counted.

The HTTP security middleware now applies the same checks and response headers
directly at the ASGI boundary, without per-request relay tasks and body-stream
copies. Cookie multiplicity, streaming, Origin rejection, body-length checks,
sanitized errors, cache policies and response monitoring have explicit contract
tests. Frequently used order routes precede the disjoint catalog/auth routers;
their original route and authorization dependency objects are preserved.

## Acceptance remains unchanged

Both independent load jobs run the actual two-worker release topology against
isolated PostgreSQL 18: 50, 100, 250 and 500 simultaneous synthetic customers,
two genuine orders per customer, with concurrent duplicate transmissions.
Both must pass on the exact release HEAD. The limits remain P95 <= 5000 ms,
P99 <= 10000 ms, unexpected errors <= 1%, zero deadlocks and zero duplicate,
missing or orphaned business effects. Restart, database outage, exhausted
connections and post-failure database reconciliation remain mandatory.

The old single-worker 500-user latency failure is **not certified as fixed**.
Its artifacts remain available, and `--workers 1` retains that diagnostic
comparison. The intended deployed configuration is now two workers, so neither
release gate claims single-worker capacity. CPU profiling explicitly uses one
worker and is never release-performance evidence.

`tests/test_release_worker_profile.py` guards the Docker/harness topology match.
The full existing functional, browser, security, printing, migration, restore
and financial tests remain required. No acceptance thresholds were increased,
no protected business checks disabled and no customer data used.

## Deployment boundary

These are shared-runner engineering measurements, **not proof of 500-user
capacity on the current Render 0.5-CPU / 512-MiB plan**. Two processes do not
create additional CPU. Any paid capacity change needs separate owner approval
and a representative isolated load test. Keep automatic deployment off, leave
the official domain and customer ordering state unchanged, and keep customer
SMS disabled. Deploy the existing experimental service only after both load
runs and full CI pass on its exact candidate SHA.
