# Two-worker emergency release profile

The release Docker command now starts **two Uvicorn web processes in one
existing Render service**. This is not a second Render instance, a new paid
service, or a background job worker. PostgreSQL, durable idempotency keys,
branch receipt counters and existing transactional outboxes remain shared.
Each process owns its bounded pool (maximum 20 connections each).

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
