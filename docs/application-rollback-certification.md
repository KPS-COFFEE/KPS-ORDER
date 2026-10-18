# Prior-application rollback certification

The Dockerfile `rollback` target is only a **launcher fallback** using the same
application source. It is not evidence of rollback to a prior application.

The distinct application fallback is assembled from exact baseline
`80a1d727f6695c4ce3685e726ab5b34674e1cfa0`, plus the explicit hashed inventory in
`tools/rollback_application_overlay/manifest.json`. The assembler uses the
candidate's pinned secure runtime/dependency foundation. It preserves the prior
operational UI and narrowly carries the pending-trip return panel, financial and
shift safety guards, contact-reuse authorization, portable exports, and trip /
printer migration preservation. It does not copy the candidate application
wholesale. New customer-search/location-edit surfaces and new driver report
exports are intentionally absent from this temporary fallback.

## Isolated evidence

`application-rollback-certification.yml` records the final source SHA, baseline,
overlay and foundation hashes, immutable Docker image ID, build metadata, retained
image archive checksum, actual `/health` and `/ready`, SBOM, HIGH/CRITICAL scan,
secrets scan, and targeted SMS/payment/printing/trip contracts.

The dedicated PostgreSQL 18.6 test creates synthetic orders, a pending multi-order
trip, a printer/device key and queued print job using the candidate schema. It
demonstrates why the raw baseline alone is unsuitable (unrecognized trip export
tables and unsafe pending-trip shift closure); those probes are transactional and
rolled back. The compatibility probe then runs inside the immutable prior image,
with only a probe script mounted, checks source hashes, refuses premature shift
closure, records a real trip return, verifies preserved rows, and re-upgrades the
same database to the candidate without restoring any database archive.

No successful source-only drill certifies an image. No synthetic drill certifies
a production backup, physical paper output, or real SMS provider delivery. The
workflow fails on skipped or failed mandatory cases and does not deploy anything.

## Production gates still required

Before any production deployment, obtain and retain a fresh production backup,
prove restoration in a separately authorized isolated database, preserve current
deployment/configuration, certify the exact candidate and distinct prior image,
verify branch protections, and confirm an authorized deployment window. Do not
change production network access to work around a denied connection.

At rollback, stop expansion and select the certified compatible **application
image**, retaining the current operational database. Never restore an older
database over new orders/invoices. Verify runtime SHA, health/readiness, SMS,
authorization, collections, shifts, printers and unique order/invoice identifiers.
If backup/restore or application compatibility cannot be proved, deployment is
BLOCKED regardless of other passing checks.
