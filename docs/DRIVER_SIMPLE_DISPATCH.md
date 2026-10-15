# Driver simplicity and dormant dispatch foundation

Owner instruction: 15 October 2026. Continue PR #1; no public launch.

Driver home shows shift guidance, availability, active tasks and canonical eligible
counts for the operational day/month. Existing eligibility remains approval-based:
open-shift work is not prematurely certified. Vehicle configuration, detailed
incentive tables and financial summaries are not added to the home screen.
Closure, photos, collection, completed-task privacy and counters retain existing rules.

## Rollout stays OFF

`DISPATCH_OFFER_ACCEPT_ENABLED=false` and `DISPATCH_AUTO_ASSIGN_ENABLED=false`
are defaults. Brand `app_settings.settings.dispatch.mode` defaults to `MANUAL`.
Both a release flag and the matching brand policy are necessary. No environment
or stored operating configuration is changed by this patch. No scheduler starts.
No GPS collection is introduced; authorized branches, current shifts, availability,
capacity and optional configured delivery-zone restrictions determine eligibility.

Policy contract: offer timeout 15–600 seconds (default 60), capacity 1–20 (default
3), least-active or oldest-assignment fairness, manager fallback. A later rollout
must approve zone configuration and any consent-based GPS ranking separately.

Dormant routes: scoped policy/inbox, manager offer, driver accept/decline,
manager automatic assignment and expiry sweep. Offer preview exposes only order
number, branch, estimated preparation and delivery area. Personal details follow
existing authorized active-task access after acceptance.

Acceptance uses the same per-brand transaction lock and canonical assignment
routine as manual assignment. Offers and events are durable; retries use existing
idempotency storage. A successful acceptance supersedes other offers atomically.
Expiry/decline never cancels an order. Manager fallback permits explicit reoffer;
there is intentionally no background reoffer/automatic assignment worker enabled.
Future scheduling must invoke these guarded routes and preserve the same flags.

The six empty additive dispatch, break and evidence tables join existing scoped
backup/restore and preservation inventories. Vehicle reports gain optional links
to shifts, orders and evidence. There are no operational data updates or counter resets.

## Review gates

Focused PostgreSQL tests: default-off/manual behavior, simultaneous acceptance,
reconnect replay, preview privacy, expiry/decline/reoffer, scope revocation and
capacity. Existing operations/float/accounting/incentive and camera tests remain.
Mobile Chromium tests capture synthetic AR/EN screens under test-artifacts.
These are isolated CI images, not deployed-service screenshots.

Deployment remains conditional on final SHA checks and proof that the existing
Render service cannot accept public orders or affect operating data. Prior ready
output reported ordering configured and SMS active; that is not sufficient proof
of an isolated visual-only environment. Never test this by submitting real orders
or sending SMS. Do not merge, change DNS, upgrade or activate smart modes.

## Added driver operations

The mobile home links to active tasks, breaks, fuel/vehicle, conversations and
reports. Ending a shift requires an explicit red confirmation before the existing
closure workflow. Blind cash/card entry, float approvals and manager review remain
unchanged. The existing camera/gallery and task-scoped chat flows are reused.

Break reasons and duration/approval limits are configured by the delivery manager;
there is no assumed business-duration policy. Active breaks block assignment and
closure server-side. Expiry remains overdue until an authorized explicit end;
countdown warnings and manager overdue alerts are in-app polling, not SMS.

Fuel recording adds station, amount, timestamp and a durable receipt attachment.
Legacy API records remain compatible. New reports route delivery categories to
delivery management and vehicle categories to maintenance, with department and
branch checks on reports and attached evidence. Drivers cannot resolve reports.

## Acceptance reconciliation

The first run on 0b05b86 had 84 passing and four failing focused cases: three
maintenance assertions still described the old categories/role contract, and the
concurrency fixture supplied a six-character idempotency key rejected by the
existing eight-character minimum. The fix retains that validation and tests two
simultaneous valid keys, exactly one acceptance and replay without duplication.
Maintenance tests retain the original twelve categories, assert six new categories,
and explicitly deny delivery management access to maintenance-department reports.
Browser regressions now navigate to the preserved vehicle report form and check
the new home action locations through two actual polling cycles.

Full restore coverage seeds breaks/events/policy, evidence and linked reports,
and an accepted dispatch offer/task in isolated PostgreSQL, then checks them in a
separate restoration schema. This does not access operational databases.

## Still required before claiming complete owner acceptance

- FULL fuel intake/return and the three owner-approved Aldrees exceptions are implemented below; certification follows the dedicated PostgreSQL/browser acceptance.
- Certify the complete requested mobile journey and every requested screenshot;
  current synthetic home screenshots are not deployed or physical-camera evidence.
- Prove the existing Render environment is safe for a limited visual deployment,
  including public ordering disabled and no live customer messaging.
- Keep smart dispatch disabled; scheduler/reoffer automation and GPS-based ranking
  require a future approved rollout.


## FULL fuel policy — owner decision, 15 October 2026

Opening and submitting a driver shift now require a 100% actual fuel reading, including delegated closure. Lower readings require one single-use approval by the responsible delivery manager, with current branch authorization rechecked. Exactly three reasons are accepted: Aldrees unavailable, balance exhausted, or refuelling limit exhausted before FULL. The request binds the driver, assigned vehicle snapshot, manager shift, phase, actual odometer and fuel reading. Closing approval additionally binds the exact driver shift. Approval and consumption are audited; transactions roll back together with financial/inspection validation. Existing historical readings and approvals remain untouched.

UI: “Is the tank full? Yes / No”, three translated reasons, request approval and refresh status. Manager inbox shows driver, vehicle, phase and actual readings with approve/reject. No financial changes; no live deployment or public ordering activation. New PostgreSQL acceptance is in tests/test_fuel_exceptions.py. Successful legacy journey fixtures now use FULL; historical fixture snapshots remain unchanged.
