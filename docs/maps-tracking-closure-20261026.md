# KPS ORDER — maps and tracking review candidate, 26 October 2026

This is a development handoff, not a release certificate. No production data was mutated and customer tracking was not enabled.

## Source and deployment witness

- Repository: KPS-COFFEE/KPS-ORDER.
- Server development branch: agent/maps-tracking-closure-20261026.
- Initial production branch source: 88abed7a9cbf6096ebdd40a65778c1e23f8f359f.
- Observed live Render service: srv-db4s1v942hec73epmar0 in the owner-selected My Workspace.
- Live deploy witness: dep-dbfkga1srm7s73faob20, status live, source 00ca968fddf4574f130c1e079ddd4666ab856fd0. This candidate includes that existing driver-destination fix.
- Android branch: agent/maps-android-closure-20261026, based on pilot 1635863. Native audio implementation is preserved.
- Other active PRs were inspected; their orders, accounting, reporting and storefront changes were not imported or overwritten. Migration identity uses complete filenames; the other open checkout migration 120_order_duplicate_contact_index.sql differs from our 120_driver_tracking_history.sql.

## Changes prepared

- Full authorized cross-brand route context, pickup prerequisites, no silent omission of missing coordinates or hidden tasks.
- Directed road-duration ordering, explicit manager publication and remaining-stop progression; driver-specific next destination and navigation.
- Staff-only ETA for stops, delivery completion and selected return branch. Stale/unavailable estimates are explicit. Stop dwell is an estimate (pickup180s, delivery120s), not measured waiting or traffic.
- One shared request budget; Matrix consumes squared coordinate elements. At most one automatic selected-driver refresh per minute; visible alert panel at most every30s. No quota increase or paid configuration activation.
- Session/shift-bound immutable GPS point batches, capture and receipt clocks, duplicate acknowledgement, monotonic live position, offline history that cannot revive stale markers.
- Encrypted atomic Android offline queue, retention24h/cap4320/batch100, shift/session binding, recovery and explicit service status. Android force-stop/reboot requires opening the app; no unsupported silent restart guarantee.
- Explicit multi-branch station arrival/departure within existing trips, mixed-brand order links, alternate return, pending-task and open-stop guards.
- Replay, measured GPS segment distance and coverage, original forecast versus actual arrival, unioned station timing. Unknown intervals remain unclassified.
- Long-stop, expected-arrival delay and progress-deviation alerts with reason/action audit. Progress deviation means increasing distance to the validated next stop; it is not detection against a road corridor.
- Private backup/preservation inventories and rollback guards include new tracking tables; runtime ETA cache retires on restore.

## Local evidence

- 132 focused Python unit/authorization/regression checks passed; 18 database cases deliberately deselected in that run.
- 47 Node interactions/regressions passed (central adapter, selected-driver ETA, publication, side-brand station actions, existing driver trip behavior and the deployed destination regression).
- Java policy checks:17 freshness,12 queue policy,29 bell,19 sound,23 brand bell. Queue replay/partial+duplicate ack/restore/expiry/overflow suite passed.
- Four local signing-helper tests passed using ephemeral test material; no production signing key was created or changed.
- Modified JavaScript syntax and git whitespace checks passed.
- Python JUnit: docs/evidence/maps-tracking-20261026/python-local.xml.
- Node transcript: docs/evidence/maps-tracking-20261026/node-local.txt.
- PostgreSQL acceptance files collect, including1+1/2+1 mixed-brand cases, cross-brand second pickup/customer stations/alternate return, provider failure/cache/budget, revocation and archive isolation. Collection is not execution.

## Required before production closure

1. Upload the prepared server and Android branches, then run exact-candidate PostgreSQL18, browser, backup/rollback, security/image and full regression CI. Local PostgreSQL could not start under the available UID restrictions; production databases were not substituted.
2. Review any failures and rebase against changes merged by other workstreams. No release gates or thresholds were weakened.
3. Build/lint/audit APK in Android CI. There is currently no built or installable new APK. Debug artifact is inspection-only; release signing follows existing owner key custody.
4. Verify dedicated MAPBOX_ROUTING_TOKEN configuration, origin/API restrictions and actual provider response before enabling live road estimates. Browser-restricted keys are not reused on the server. Current cap may make estimates unavailable after exhaustion; real fleet load/cost measurement remains necessary.
5. Authorized deployment, workload checks and live endpoint verification. This candidate has not been deployed.
6. Physical Android acceptance: open shift, screen locked, WhatsApp foreground, network lost/recovered, unchanged point IDs/capture timestamps, permissions/GPS denied/restored, process eviction, logout/session replacement and shift closure. Full checklist is android-driver/OFFLINE-FIELD-ACCEPTANCE.md on the native branch. No physical-device success is claimed.

Initial branch waiting before the existing recorded trip departure remains unknown. Only explicit recorded station intervals can prove waiting. GPS coverage/distance exclude gaps/inaccurate/implausible segments; they are neither complete odometer distance nor road distance.

## Upload blocker

An automatic approval review rejected Android branch upload because the GitHub destination was considered unverified and the upload lacked explicit export approval. No connector or indirect path was used to bypass the rejection. Both candidates remain local until upload is authorized.

Native review candidate: aa140f786e8537f47bbc77336f65043fe831007b. Server includes deployed hotfix via merge5ca34d5; post-merge Python132/Node47 and YAML parsing passed. Detailed evidence remains local; no remote upload was retried after rejection.
