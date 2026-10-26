# Two-worker release profile — verified resource target 2026-10-26

The release Docker command now starts **two Uvicorn web processes in one
existing Render service**. This is not a second Render instance, a new paid
service, or a background job worker. PostgreSQL, durable idempotency keys,
branch receipt counters and existing transactional outboxes remain shared.
Each process owns its bounded pool (maximum 4 connections each, 8 total).
The previous 40-backend ceiling saturated the 256-MiB database envelope in
resource-isolated tests. This bounds database execution concurrency, not HTTP
users, order count, retries or the unchanged five-second checkout deadline.

The pool retains at most 64 parameterized SQL plans per connection, not rows,
prices, authorization results or sessions. Every request still reads current
database state. Reset closes cursors, restores authorization and GUCs, removes
listeners, releases advisory locks, and discards temporary and sequence state.
The full DSN/schema and worker PID remain part of pool identity. The direct
PostgreSQL connection must not be replaced with an incompatible transaction
pooling proxy without separately validating prepared-statement support.

Migration 052 adds a selective recent-contact index; exact basket equality,
15-minute age, brand and branch checks remain unchanged. The index does not
make a phone number proof of customer ownership.

## Current production resource target and retained historical failures

Read-only Render inspection on **2026-10-26** verified the existing application
service uses **1 CPU / 2 GiB** (`1c-2g`) and its PostgreSQL 18 database uses
**0.5 CPU / 1 GiB**. The representative release gate selects the existing
`economy` profile and runs **two independent repetitions** against those kernel
budgets, with two application processes and four pooled database connections
per process. This documents the resources already present; no paid plan was
changed or proposed resources counted as current capacity.

The legacy `current` tool profile remains 0.5 CPU / 512 MiB application and
0.1 CPU / 256 MiB database for reproducible historical comparisons. It is not
the verified production target. On candidate
`7e3e0d72173ed8fb211c1b17f999c97383773265`, its two 500-user P95 results were
18178.443 / 24494.021 ms: **FAIL**, despite preserved orders and no HTTP errors
or OOM. Unconstrained results 3520.141 / 4390.617 ms do not certify that small
resource envelope. Candidate `90043355` also failed the historical small
profile; its original measurements and artifact IDs remain in
[the historical evidence record](qa/historical-small-render-budget-90043355.json).
The [current target record](qa/current-render-capacity-target-20261026.md)
distinguishes those historical failures from the verified target.

## Larger resource proposal remains unapproved

Separate CI jobs explicitly select `KPS_LOAD_RESOURCE_PROFILE=proposed`:
app 2 CPU/4 GiB, PostgreSQL 1 CPU/2 GiB, the same two application workers,
eight maximum pooled connections, workload, timeouts and acceptance limits.
Every sample still verifies actual kernel budgets and process ownership.
The result is named `PASS_PROPOSED_RESOURCE_GATE`, never a current-plan PASS.
This tests the Render `2c-4g` app / `1c-2g` database resource envelope only;
provider I/O, network and operating traffic remain separate validation.
No Render plan, Blueprint, domain, SMS setting or deployment is changed.
Owner approval of paid capacity and the remaining launch gates is mandatory.
Plan specifications: https://render.com/docs/compute-plans (checked 2026-10-15).

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
Both must pass on the exact release HEAD. The owner requires P95 < 5000 ms,
P99 <= 10000 ms, unexpected errors <= 1%, zero deadlocks and zero duplicate,
missing or orphaned business effects. Restart, database outage, exhausted
connections and post-failure database reconciliation remain mandatory. The two
representative current-resource repetitions additionally require **zero**
unexpected HTTP errors (`--max-error-rate 0`); they retain the same strict
latency limits, workload and complete data/recovery checks.

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

These are isolated shared-runner engineering measurements, not a certificate
for every condition of the live service or its real financial balances. Both
representative repetitions must pass on the exact intended release source,
alongside the required functional, browser, security, migration, preservation
and restore gates. Provider I/O, network and other operating traffic remain
separate observations. Any future paid resource change requires separate
owner authorization; passing the larger proposed profile does not approve it.

Keep automatic deployment off and preserve existing production settings. An
authorized release must be pinned to the tested commit, then checked on both
official domains for the actual source revision, readiness and role access.
Historical proposed security instructions do not require creating a new live
owner OTP or sending SMS merely for release verification. Do not claim live
SMS delivery or image certification beyond the evidence actually obtained.
The manual bank, source-correction and universal administrative workflow
preparations remain development-only and unavailable in production.
