# Enbishan candidate security recheck — 2026-10-22

The functional PostgreSQL 18 / real-browser workflow for candidate
`1807f7e4372e384442e19fb8bc06b7222f071e73` completed successfully:
<https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39716866989>.
Both the dual-brand and operational-regression jobs passed.

The same candidate's security workflow did **not** pass:
<https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39716867059>.
The host job passed, as did focused suites, mandatory browser cases and runtime
smoke checks in both immutable images, but the required image scan correctly
blocked release. Candidate artifact `12399468603` records 20 HIGH/CRITICAL
findings: certifi `2026.7.22` (one CRITICAL) and Pillow `12.3.0`
(three CRITICAL and sixteen HIGH). No OS HIGH/CRITICAL finding or secret was
reported in that artifact. This is not a passing release certificate.

Reused the exact dependency correction already implemented in KPS commit
`497d725f4b17afce2ef5d9ba59ac44bb5d141fff`: certifi `2026.10.19` and Pillow
`13.0.0`, including the same pinned wheel hashes. The requirement files match
that upstream revision byte-for-byte. No scanner, severity, suppression,
ignore, security policy or business logic was changed. No production stream
or Render resource was updated.

Local source validation with those installed versions: **1249 passed,
15 skipped, 1152 deselected, 197 subtests passed**. The skips/deselections do
not certify database/browser/container behavior. The patched candidate must
obtain its own native functional and immutable-image security results before
READY TO ACTIVATE can be claimed. Historical October 20 certification is not
evidence for this revision. Capacity testing is not re-certified here.

The separate customer-preview Site remains browser-only simulation: no live
API, real SMS, real payment, customer import, operating branch activation or
KPS/TILQA database connection. Updating that demo is not deployment of the
application candidate to production. Full hosted backend UAT still needs an
independently provisioned test database/service; existing production and TILQA
databases must not be repurposed for it.
