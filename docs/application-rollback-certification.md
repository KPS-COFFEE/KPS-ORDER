# Prior-application rollback certification

The Dockerfile `rollback` target is a launcher fallback using the same
application source. The distinct application fallback is assembled from the
last verified production baseline
`b528f14c5ea93e32a15a5c37f43f7e93477136e8`, plus the explicit SHA-256 inventory in
`tools/rollback_application_overlay/manifest.json`. Its dependency and runtime
foundation is pinned to the candidate's secure build inputs.

## Why the prior baseline changes

The earlier `80a1d727f6695c4ce3685e726ab5b34674e1cfa0` fallback predates the
shared cash-bag, clarification, bank and duplicate/partial-return contracts. It
correctly refuses a database after those contracts are used. That refusal is
preserved as a hash-bound historical test fixture under
`tests/fixtures/rollback_80a/`; it is not evidence that the old writer can operate
on current business data.

The b528 baseline already implements those production contracts. The current
fallback preserves its prior UI and order-search implementation, with narrow
compatibility changes for existing preparation routes, collected-order authority,
and returned intervention workflows. An existing route continues to use its
actual preparation and pickup branch. The fallback refuses new route selection.
Original selling branch, invoice amounts, source collections, financial cases,
print history and audit records remain intact. The assembler still requires
`app/orders.py` and `public/ops.js` to differ from the candidate; it cannot copy the
candidate application wholesale and call it a prior release.

## Isolated evidence

`application-rollback-certification.yml` records the final source SHA, baseline,
overlay and foundation hashes, immutable Docker image ID, build metadata, retained
image archive checksum, actual `/health` and `/ready`, SBOM, HIGH/CRITICAL scan,
and secrets scan. Mandatory cases must execute and pass without skips.

Contracts run inside that exact prior image for SMS and authorization, collections
and refunds, printing, driver trips and shifts, shared physical declarations and
cash bags, financial clarification and treatment, manual bank matching, tracking,
duplicate orders and partial returns. Source-packaging checks bind the unchanged
accounting and established operational modules to the exact b528 baseline.

The candidate-to-prior-to-candidate PostgreSQL 18.6 drill creates synthetic orders,
a pending trip, a printer/device key and queued print jobs on the candidate schema.
The raw b528 baseline correctly refuses unfinished-trip closure but cannot export
an unknown preparation-route table. Raw probes are transactional and rolled back.
The compatibility probe runs inside the immutable prior image with only a probe
script mounted, validates source hashes, records the real trip return, checks
preserved rows and re-upgrades the same database without restoring an archive.
Additional preparation-route witnesses cover both unprinted routed orders and a
route already accepted by a driver, including actual pickup, output destination,
immutable invoice origin and source timestamps.

A passing source-only drill does not certify an image. Synthetic tests do not
certify a production backup, physical paper output or real SMS delivery. These
workflows do not deploy, and this document does not claim that a particular
candidate has passed before its exact-SHA evidence is available.

## Production gates still required

Before production deployment, retain a fresh production backup and restoration
proof in a separately authorized isolated database, preserve the current
deployment/configuration, certify the exact candidate and distinct prior image,
verify branch protections and confirm the authorized deployment window. Do not
change production network access to work around a denied connection.

At rollback, stop expansion and select the certified compatible application
image while retaining the current operational database. Never restore an older
database over new orders or invoices. Verify runtime SHA, health/readiness, SMS,
authorization, collections, shifts, printers and unique order/invoice identifiers.
If backup/restore or application compatibility cannot be proved, deployment is
blocked regardless of other passing checks.
