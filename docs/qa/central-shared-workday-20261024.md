# Shared central operations — current integration checkpoint

## Contract

One operational workspace and physical workday serve both KPS and Enbishan,
subject to current explicit branch grants. Customer-order buttons select the
appropriate menu. Each order, cash ledger, approval and report retains its brand.
Accounting and owner settings may select a brand locally. Public storefronts
remain separate.

## Release state

Draft PR77 targets the deployed dormant foundation 74d1fbd. Production central
operations remains disabled. This checkpoint does not certify activation.

The shared workday uses canonical per-brand financial shifts with one physical
vehicle anchor, one inspection and one coordinated closure. Opening funds are
not copied between ledgers. Existing single shifts may be adopted without moving
cash or tasks; multiple existing physical shifts are rejected rather than merged.
Grouped actions revalidate all current branch grants and commit atomically.

Parallel review is addressing grouped manager handover, physical vehicle identity,
flag-off drain behavior, operational overview, fleet, driver destinations and
incentives. These additions need the next PostgreSQL/browser certification.

## Evidence already obtained

- Focused PostgreSQL18 acceptance at b350288: 65 passed.
- AR/EN Chromium acceptance at b350288: 4 passed.
- Local existing HTTP security, transaction boundary, retry compatibility,
  production preflight and multibrand boundary suites: 59 passed, 10 deselected.
- Later parallel changes are not covered by those prior successful runs.

Full protected release workflows are explicitly enabled for the shared-workday
branch, without dropping their existing checks. Final activation requires their
successful results on the final commit and live runtime verification.

## Production preflight limitation

The confirmed Render production PostgreSQL instance is available, but the hosted
read-only SQL connector cannot reach its private network: its IP allowlist is
empty. No network access settings were changed. A service-internal preflight is
required to inspect existing open-shift adoption eligibility before activation.
No production shifts, cash, grants or vehicle records were modified here.
