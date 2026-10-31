# Central branch operations — implementation checkpoint

Owner contract: employees and drivers enter one operational workspace. Their
current branch grants can include KPS and Enbishan. Orders, conversations, driver
work and notifications share their screens. A manager chooses “KPS customer” or
“Enbishan customer” only when opening an order menu. Accounting and owner settings
may select a brand locally. Public customer storefront URLs remain independent.

## Implemented, not released

- Separate branch `agent/central-branch-operations-20261023`, based on the merged
  PR65 runtime commit `3e14bb9`.
- Revocable server-derived branch metadata without widening the canonical
  authenticated principal or granting any new memberships.
- Central operational collection adapter reuses canonical APIs for each current
  grant. A failed constituent fails the inbox rather than presenting partial data
  as complete. Page streams are not globally truncated.
- Record actions resolve their brand automatically; canonical APIs still enforce
  permission, branch scope, task ownership, CSRF, idempotency and financial rules.
- Both explicit customer order menu buttons, with per-dialog API scope and retry
  namespaces. No global brand switch in central mode.
- Branch-manager projection uses an explicit membership just like other staff;
  no default cross-brand access.

## Validation to date

- Node adapter suite: 11 passed.
- Existing multibrand final boundary suite: 16 passed.
- JavaScript syntax, Python compilation and `git diff --check`: passed.
- PostgreSQL18 focused acceptance succeeded in GitHub Actions run 40024772086;
  no local PostgreSQL success is claimed. This runtime maps only UID0, preventing an
  ordinary unprivileged PostgreSQL test server.

## Central feature activation blockers

`KPS_CENTRAL_OPERATIONS_ENABLED` defaults off. It has not been set on production.
This is an implementation checkpoint, not a completed central-operations release.
The owner subsequently instructed publishing this checkpoint. Ship the dormant
foundation only after all release gates pass, and keep central operations off;
that deployment does not make the central experience available.

1. Shared operational workday for manager/driver, including assignment of both
   brands from one duty and immediate branch revocation. Existing shift rows are
   still brand-specific and must not be mistaken for a shared workday.
2. One driver closure experience with explicit brand ledger attribution. Preserve
   actual cash, card, variance, retained custody, prior closures, approvals and
   blocking unresolved tasks. Never duplicate opening floats, vehicle mileage,
   inspections or fuel by opening two physical shifts for one employee.
3. Consolidated employee branch editor and directory, including existing accounts;
   no automatic all-branch grants or changes to employee roles/permissions.
4. Central printing setup/alerts, protected image/download reads, recovery,
   dispatch map, notifications and operational reporting. Every record retains
   its own brand, branch and printer/receipt identity.
5. Brand selector only inside accounting/owner settings, without filtering the
   operational workspace or changing another open order dialog.
6. Restricted morning-duty parity; existing morning contexts deliberately keep
   their canonical scope until this journey is implemented and certified.
7. PostgreSQL18 and AR/EN browser acceptance: both-brand manager ordering, shared
   driver receipt/delivery, conversations, real printer routing, closure,
   membership revocation, ID tampering and preservation of existing KPS data.

No production data, membership, activation flag or deployment was changed by this
checkpoint. Do not remove existing protected release checks to ship it.
