# Enbishan isolated development — 2026-10-17

Branch: `agent/enbishan-isolated-development-20261017`.
Baseline: `8b7fdb8d48dc38c413c79507c9097e3b43ceac32`.
First implementation: `192ebf9639df2ab344c96a4ebe9c0e09392de602`.

## Production boundary

No production deployment, merge, DNS change, Render configuration change,
production database connection, customer-data import or live payment activation
is part of this stream. The isolated feature defaults OFF. Enabling it requires
`APP_ENV=testing` and a local database named `test_enbishan_*`; migration rejects
a remote/runtime DSN before connecting. Provider credentials are rejected.
Optional migrations run only behind this guard. Existing production migrations
and their checksums are unchanged.

## Implemented

- Server-owned Enbishan host/default binding for the proposed
  `order.enbishan.com`, tested without DNS or production configuration changes.
- Brand-specific storefront catalog, branches, prices, basket, customer identity,
  order history, delivery coverage and fees, and separate numbering. KPS keeps
  its existing reference format; Enbishan uses an `ENB-` prefix.
- One staff identity/session, explicit owner-granted additional brand access,
  immediate grant revocation, role/permission preservation and a mobile brand
  selector. Cross-brand authority is never inferred from being an owner.
- Shared driver pool, cross-brand task notifications, and day/shift/custom-period
  dedication using explicit timezone-aware start/end values. Assignment and
  dedication share a driver lock; conflicting periods and active other-brand
  tasks are rejected.
- Brand-specific financial shift subledgers for the same employee; collections,
  carryovers, reports and cash transfers stay scoped. Cross-brand cash transfer,
  order/branch and fiscal-policy/document links have database safeguards.
- Enbishan tax documents require an explicit Enbishan seller and address. They
  never inherit KPS's legal/VAT identity. Immutable document/QR and credit-note
  paths retain the existing issuance controls. No Phase 2 integration is claimed.

The test data uses **SYNTHETIC / TEST ONLY** customers, products, seller identity,
locations and two branches labelled Rabwah/Muhammadiyah. These are not approved
operational records. Qaisumah is not activated. Real Enbishan products/prices,
branding assets, verified branch locations/coverage, legal seller/VAT profile,
and customer messaging configuration must be supplied/approved before launch.

## Execution evidence and baseline reconciliation

The untouched baseline's focused 99-test selection produced **94 PASS / 5 FAIL**.
The expanded KPS selection on the first implementation produced **180 PASS /
5 FAIL** locally and in GitHub. The five failing identities were identical:

| Existing test | Observed prerequisite/policy mismatch | Isolated test repair |
|---|---|---|
| `test_closing_freezes_assignment_and_pending_tasks_gate` | Early closing requires manager approval | Assert 409 first, request and approve, then retain pending-task/assignment assertions |
| `test_discrepancy_partial_handover_retained_carryover` | Same early-closing approval prerequisite | Add explicit approval; retain all difference, carryover and adjustment assertions |
| `test_task_transfer_across_manager_reports_does_not_duplicate_completed_sale` | Same prerequisite for the receiving driver | Add explicit approval; retain both reports and non-duplication assertions |
| `test_dashboard_setting_midnight_refund_and_card_split` | Delivery-manager ordinary refund correctly returns 403 | Retain the 403 check, then use the authorized owner; retain financial assertions |
| `test_duplicate_product_lines_refund_subtracted_once` | Same refund role restriction | Retain the 403 check, then authorized owner and unchanged quantity/net assertions |

All five repaired scenarios executed successfully locally. No application
authorization was widened to make these tests pass. No existing test was
removed, skipped, xfailed, or had a financial/performance threshold lowered.

First GitHub run:
[39000332326](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39000332326),
exact SHA `192ebf9`: **17 dual-brand/mobile PASS**, **180 KPS PASS / 5 inherited
FAIL**. This run remains FAIL; it is not a release certificate. Local Chromium
could not launch because the execution sandbox prohibits its singleton socket;
actual mobile browser execution was therefore performed in GitHub.

Later local dual-brand execution added independent closures/reports and
pre-connection isolation rejection: **14 PASS**. Additional final denial,
concurrency and driver-mobile scenarios require the final exact-SHA run.

## Reproduce

Optional disposable local PostgreSQL:

```sh
docker compose -f compose.enbishan-test.yml up -d
export TEST_DATABASE_URL=postgresql://enbishan_test:synthetic-local-test-only@127.0.0.1:55640/test_enbishan_sandbox
python tools/enbishan_test.py tests/test_enbishan_dual_brand.py tests/test_enbishan_mobile_browser.py
```

Install the repository's pinned requirements and Playwright Chromium first.
Each database test creates and removes only its own UUID schema. The test
runner clears runtime DSNs and provider secrets, and never falls back to them.
The dedicated GitHub workflow also executes the unchanged KPS regression
selection, saves JUnit/screenshots and records the exact revision. No pressure
test or expensive large-scale certification was added.

## Completion rule

**Enbishan PASS + KPS regression PASS + shared operations PASS + security PASS**
on the same final revision, with actual execution evidence, is required for
development review. A partial pass, a blocked browser, stale SHA evidence or
unresolved critical/high regression cannot satisfy the gate. Production
integration remains a separately authorized action.
