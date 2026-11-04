# KPS ORDER memory incident — 18 October 2026

## Finding and confidence

Render records an **OOM kill at the 512 MiB service limit**, event
`evt-dbagopu417fc73bnjqfg`, timestamp `2026-10-18T18:13:59.578164Z`
(21:13:59 Riyadh). Production still serves commit
`26e1c4fedfe49355272b26d4ab1d1e6267577665`; no infrastructure or operational
data was changed during this investigation.

The event timestamp and process-start logs do not align: the retained logs show
the parent restarting at 17:50:46Z and two workers starting at 17:50:54Z;
no process-start line was returned around the event timestamp. Do not treat
the event timestamp as a verified request-level crash time. One-minute memory
sampling misses short peaks and does not establish which request caused the
kill. No completed access-log response identifies the killed request.

A reproducible amplification defect exists in delivery/fuel evidence processing:
up to 16 million pixels are decoded, copied for EXIF orientation, converted,
and only then resized. Several full-size native buffers can coexist, and
separate workers can do this simultaneously. A permitted 16MP still WebP
reproduces a **321.7 MiB single-process peak** before this change. This is a
credible OOM mechanism on the observed two-worker 512 MiB service, **not proof
that a particular production photo caused the recorded kill**.

No unbounded Python cache or increasing pool count was established. The process
pools remain min 2 / max 4 per worker, with 64 prepared statements per connection
and bounded waiting. PostgreSQL 18 metrics show approximately 8 connections and
92–165 MiB sampled database memory against a 1 GiB limit. A direct pg_stat_activity
query was blocked by the database's existing empty external allowlist; that
protection was not changed. Background task lifecycles and their fixed polling,
bounded batches and pool cleanup were reviewed; there is no measured per-task
memory attribution from production. A production heap leak remains unproven.

## Review candidate

Based on the latest existing review HEAD
`39d36719c986aecbebd166d90239f836fda25e6a`, without modifying its other changes:

- A shared Linux file lock serializes evidence normalization across both workers.
  It holds no database connection and releases on normal return, error or process
  exit. Workers and database pool limits are unchanged.
- Resize before orientation copies; release full-size converted buffers promptly.
  JPEG uses decoder thumbnail support. Alpha/palette images avoid a full-size
  alpha-resampling copy.
- Still WebP uses the stable libwebp `WebPGetInfo` / `WebPDecodeRGBInto` C API
  exported by the installed Pillow extension, with prevalidated dimensions and a
  bounded RGB buffer. No system codec, added package or provider is required.
  Native symbols must also pass the exact release-image CI gate before deployment.
- Preserve accepted formats, 5 MiB input / 16MP limits, JPEG output quality 88,
  maximum 2000×2000 dimensions, EXIF orientation and metadata removal. Animated
  WebP retains first-frame behavior using Pillow; the 16MP still-image memory
  measurements below do **not** certify worst-case animated-image memory.
- Task ownership, branch/brand authorization, idempotency, evidence retention,
  collection and delivery transitions remain in their original code paths.
  No migration or existing stored-image rewrite is included. Normalized bytes
  of large images can differ because of decoder downsampling; an in-flight
  retry spanning versions may return the existing idempotency conflict rather
  than replay when its old normalized-image fingerprint differs. It cannot
  silently create a second effect under the same key.

## Local validation

**126 unique tests PASS**, zero unresolved failures/errors/skips across the
retained final executions. Earlier environment/fixture failures are preserved in
scratch evidence and are not counted as passes. The two final driver browser
cases replace their earlier failed attempts in the unique-case summary.

Real PostgreSQL **18.6**, Python **3.12.14**, Pillow **12.3.0**, actual Chromium
headless shell **153.0.8010.12**, with synthetic isolated schemas and identities:

- Order transaction rollback/replay, connection-pool/fork cleanup, HTTP boundary
  limits, cash/card/split collection, refund/custody and stored report evidence.
- 16MP JPEG/PNG/WebP upload, same-key replay, scoped reads, JPEG dimensions,
  pickup and delivery photos, persisted transitions and exactly one collection.
- Real Arabic/English driver camera/gallery, preview, pickup, cash collection,
  delivery and manager evidence read; real Arabic/English split-payment UI.
  Staff sessions use actual four-digit SMS request/verify with only external
  provider I/O synthetic. Password denial is retained.
- Cross-process decoder locking, format validation, EXIF rotation/stripping,
  original pixel limit and animated first-frame compatibility.

| 16MP fixture, isolated decoder process | Before peak | After peak | Reduction |
| --- | ---: | ---: | ---: |
| RGB JPEG | 196.9 MiB | 120.6 MiB | 38.7% |
| RGBA PNG | 196.4 MiB | 135.4 MiB | 31.1% |
| RGB still WebP | 321.7 MiB | 119.5 MiB | 62.8% |

Peak measurement uses Linux `/proc/self/status` VmHWM, excluding pre-exec parent
encoder peaks that can contaminate ru_maxrss. The original pipeline exceeds the
unchanged 160 MiB decoder witness threshold for all three fixtures. After 24
mixed large-image conversions, post-batch RSS was 15752 / 15780 / 15780 / 15780
KiB: a 28 KiB change after warm-up, not evidence of a continuing leak in this
isolated workload. This is not a full production load/capacity certificate.

The existing operational CI list and browser step include the new cases. Full
exact-image/security certification on the new commit remains a release gate;
older green SHAs do not certify this candidate.

## Current production observation

At the final observation around 21:38 Riyadh, Render samples show approximately
**286 MiB / 512 MiB**; a preceding short sample reached approximately 344 MiB
and returned. No new server_failed/server_restarted event was returned after
18:14Z. Live `/health` and `/ready` succeed on the unchanged production SHA,
the database is connected, and the readiness endpoint reports its existing
65-table preservation verification. This is point-in-time availability, not a
guarantee of sustained memory stability or a new independent data-integrity audit.

**No merge, deployment, restart, paid upgrade, provider activation, production
load test or customer/order/invoice write was performed.** The fix is for review;
deployment still requires passing release gates and owner approval.

Machine-readable local case/measurement summary: [evidence.json](memory-20261018/evidence.json).
