# Delivery operational candidate — approval required, no deployment

Starting remote HEAD: `015e907a596e69c40f5b22807b858db41f156337`.
Observed Render live revision: `ca67811e94eb5d0605355bc3253640209a8a6248`.
Service auto deploy is OFF. PR #1 remains draft and unmerged.

This follow-up preserves all earlier payment and storefront development. Its
review scope is driver closure authorization/withdrawal, fuel readiness and
unique operational controls, employee SMS proof/wording, owner customer read
models, and read-only recovery evidence. It is not authorization to deploy the
mixed branch or activate ordering.

## Separate gates and retained history

* Actual source inventory, full encrypted durable backup, a PostgreSQL 18 restore
  of that SAME source, attachment/document reconciliation and atomic cleanup
  rehearsal remain mandatory before source cleanup. The private source could
  not be queried using the available connector; its empty external allowlist
  was preserved. No DELETE, reset, operational period or identity change was
  executed on the source. No invoice count or backup success is inferred.
* Preserve all fiscal documents, credit notes, source snapshots, UUIDs, QR,
  attachments, audit history and invoice/order counters. Preserve every required
  dependency, including historical customer, order, collection and shift rows.
  Unknown tables remain preserved. A table retention graph is conservative
  planning evidence, not row-scoped deletion authorization.
* After a successful source recovery and certified cleanup rehearsal, the ONE
  authorized executor may set `brands.settings.operational_period_started_at`
  to the recorded new period start and
  `brands.settings.historical_test_customer_ids` to reviewed canonical IDs and
  aliases. These read-model settings do not close old shifts, discharge custody,
  suppress queues, migrate data or remove operational blockers. Those still
  require the separately rehearsed source procedure. Invalid period values fail
  closed in CRM and are surfaced for review.
* The first real shift must use the vehicle's actual odometer reading. A legacy
  manually paused shift remains paused after fuel confirmation; an authorized
  manager must explicitly resume it after checking readiness. Fuel approval
  does not overwrite the fuel reading with FULL or end a break.

## Narrow release preparation

Do not deploy PR #1 wholesale to resolve a setting. Before presenting a specific
deployment for owner approval, produce a dependency-reviewed patch against the
observed live revision and test that exact assembled revision. This follow-up's
commit range alone depends on pre-existing branch modules and is not a standalone
patch against `ca67811`. Narrow live-based assembly remains PENDING; no independent
release certificate is claimed for it.

Keep real payment activation, pickup, car delivery, gift orders, discounts,
cashback and uncertified integrations in test. Preserve their code. Keep
`EMPLOYEE_SMS_LOGIN_ENABLED` and `KPS_PORTAL_HOST_ROUTING_ENABLED` OFF until their
separate approved rollout. Verify actual per-brand service/payment policy in the
private source before any rollout; code defaults are not proof of live settings.

Employee SMS needs an approved existing four-digit provider configuration and a
specifically authorized test recipient for real receipt/verification. Synthetic
provider tests do not establish delivery. Do not alter the existing customer
provider contract or silently fall back to email in SMS mode.

Portal code uses exact `order.kps.sa`, `admin.kps.sa`, `staff.kps.sa` hosts,
host-only secure sessions and same-host Origin enforcement. DNS/certificates and
live role journeys are separate unverified gates. Do not change DNS or Render
environment settings as part of this candidate.

## Verification evidence policy

Run disjoint focused suites against ONE final code SHA using independent local
PostgreSQL 18 databases and temporary directories. Record exact commands, engine
version, unique test counts and failures. Do not add overlapping earlier counts.
Four original AR/EN ambiguity cases need actual Chromium acceptance after the
last UI change. Do not use forced clicks, arbitrary first matches or weaker
assertions. A synthetic restore proves only the synthetic fixture, never current
source recovery. No expensive 500-user certification is part of this follow-up.

The historical current-budget capacity failure remains open, separately from
payment functionality. Fiscal applicability, missing actual privacy/transfer
evidence, recovery monitoring causes, final release approval and authorized live
delivery acceptance also remain open unless independently evidenced.

IMPLEMENTED, TESTED, DEPLOYED and LIVE READY are separate states.
