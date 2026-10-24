# KPS ORDER channel/legal closure candidate — 15 October 2026

Pre-change branch HEAD: `1012d31b1753e54ab9fb72505b1d0b06e8f3f3e4`.
Existing PR #1 only. No merge, official-domain change, public activation, SMS or payments.

## Behavior and limits

- Concise bilingual policies; compact controller card with 15-10-2026 visible date; colored local social SVG paths; existing KPS artwork bytes unchanged.
- Explicit disclosure of processing outside Saudi Arabia retained under PDPL Article 13(4). Vendor/city and audit narrative remain in the internal evidence register. Transfer/legal clearance remains OPEN.
- New KPS ORDER orders freeze Reem channel seller/issuer/operator independently of the executing branch owner. A conflicting persisted Reem VAT/identity configuration blocks the new order for review. Unknown branch ownership blocks new channel snapshots.
- Gross item value excluding delivery is the branch settlement review basis, not an approved payout or inter-entity VAT invoice. Calculated channel VAT is included in prices. Inter-entity accounting treatment still requires actual-registration review. Sami VAT is not invented.
- Owner-only, idempotent, append-only allocation ledger associates actual refund records with branch or delivery/channel responsibility. It supports internal compensation review, never pays/refunds money and never changes an existing order/refund. Unallocated refunds remain visible. Existing authorized financial refund workflow is unchanged.
- Existing order amendment approval/revision path recalculates the new monetary basis while retaining frozen ownership/seller and original revision history. Historical orders without the channel snapshot require explicit review; no backfill or retrofit exists.
- Private owner document endpoint: `/api/internal-documents/kps-operating-agreement`; owner Settings links to it. Non-KPS owners, customers and staff cannot access it. Clean final version for re-signature, both dates 15-10-2026, no transferred signatures. Source is not publicly mounted.
- Fiscal issuance remains unconditionally disabled by the existing receipt implementation. There is no approved fiscal path, phase readiness or authorized ZATCA QR generator to enable. Therefore **no simplified-tax-invoice preview is represented as a valid fiscal document**, even under a fabricated snapshot readiness flag. The safe thermal receipt remains visually unchanged. Fiscal integration and validated readiness are a release dependency for official tax invoices.

## Data preservation

No live database connection or operational write is used during implementation/testing. New migration 053 creates only a separate ledger, index and immutable-row trigger; it has no UPDATE/DELETE/backfill. Tests use isolated UUID schemas in an explicitly local test database. Provider credentials are stripped by existing fixtures. Regression checks compare all table contents before/after public-page requests and compare full order JSON around allocation writes. Runtime activation/resource/DNS files are not modified.

## Rollback

For this isolated candidate, revert its commit(s) in reverse chronological order on the existing branch, run the same tests, and deploy the resulting exact tested commit only with authorization. Do not reset the branch or force-push. The pre-change tree is available at the SHA above; use `git diff 1012d31b1753e54ab9fb72505b1d0b06e8f3f3e4 -- <path>` to review each affected path. For a presentation-only rollback restore only `app/customer_policy_content.py`, `public/customer.css` and the four `public/assets/social/*.svg` files from that SHA, commit and test; retain new private API protection and financial snapshots. Do NOT drop migration 053, erase allocation/audit rows, rewrite orders, or restore an old operational database. Additive tables can remain unused by the prior application. No rollback action was executed.

## Evidence

Local source regression initially: 625 PASS, 197 passing subtests, 9 browser/environment skips, 630 database tests deselected; this is not a PostgreSQL or UI certificate. GitHub PostgreSQL 18/browser acceptance and complete regression must pass on the final exact candidate before deployment. Local browser installation and local PostgreSQL provisioning were unavailable; no results are fabricated. See exact-commit GitHub Actions for authoritative remaining evidence.

Full customer copy: [Arabic](policies-ar.md) and [English](policies-en.md).
