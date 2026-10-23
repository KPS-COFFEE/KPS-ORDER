# KPS ORDER / ENBISHAN baseline evidence — 23 October 2026

This is a read-only baseline inventory, not a release certificate. Code reviewed at
`f8f38407bedaa4be5d4db6f87db5687fb0573de6`. No production data, environment,
DNS, email routing, service, or provider account was changed by this audit.
Subsequent implementation must receive its own exact-commit acceptance.

A later branch inventory found substantial existing ENBISHAN development at
`a8d6a7e3a1510b3a9b6345fc742982fe2671753b` on
`agent/enbishan-isolated-development-20261017`. The production-only manifest gap
below must not be interpreted as an absence of approved source or completed work
on that development branch. Reuse and reconcile that branch; do not invent a new
menu or duplicate backend.

## Production identity

Render read-only `list_deploys` identified `kps-order-emergency-v4`, service
`srv-db4s1v942hec73epmar0`, workspace `My Workspace`
(`tea-d9ttnf142hec7393049g`). Latest live deploy was
`dep-dbdc8hrncjis739mkdf0`, revision
`f8f38407bedaa4be5d4db6f87db5687fb0573de6`, finished
2026-10-23 02:21:04 UTC (05:21:04 Asia/Riyadh). Auto-deploy is off.
The production branch is `agent/kps-order-emergency-v4`; `main` is not the
production application baseline.

## Inventory

| Stream | Baseline status | Evidence and remaining boundary |
| --- | --- | --- |
| ENBISHAN storefront activation | PARTIAL | Existing APIs and operations use brand scope. No approved ENBISHAN products occur in the production baseline `data/catalog.json` or `data/final_menu_approval.json`; each contains 35 KPS products. Existing development at `a8d6a7e` retains 31 prepared cards (28 visible), source menu/asset provenance, the 22 October final review, dual-brand APIs and isolation tests. Integrate and validate this source and its approved changes before public activation; this is integration work, not a request to recreate the menu. Domain-host enforcement and HTTPS/public readiness require independent verification. |
| Advertising and gift engine | DONE core / PARTIAL brand activation | Existing banners support brand/branch/city scope, schedules, assets and previews; advertising enablement is separate from gifts and is owner-only, versioned, idempotent and audited. Gift claims are atomic per brand/canonical verified Saudi phone. PR16 certifies the core. PR51 merged the approved KPS steady orange shimmer (0.5 seconds active, 1.5 seconds rest). No ENBISHAN offer or creative approval is established by these PRs. |
| Menu and customer journey | PARTIAL | PR60 certifies manager WhatsApp paste/edit, repeated photo taps, plus/minus and required choices for KPS/ENBISHAN. PR54 (customer clarity, mandatory neighborhood, optional description) remains draft, unmerged, and had failing checks. PR55 (Google picker) remains draft with cancelled dedicated checks; Mapbox remains default. Physical-device Google acceptance/configuration is not established. |
| Delivery operations | DONE core / PARTIAL live grants | PR60 certifies preparation windows: morning 03:45/04:00, Friday 12:15/12:30, evening 15:45/16:00 Asia/Riyadh; morning manager scope remains approved KPS Branch2. Role/branch/brand authorization, fleet swaps and durable notices are present. Fawzi's actual account/printer grants were expressly not changed or verified by PR60/59. Customer tracking requires explicit `CUSTOMER_DELIVERY_TRACKING_ENABLED=true`; default is off. `closure_deadline` retains local 16:00 next-day and external 72-hour policies. |
| OTP / Authentica | MISSING Authentica / DONE existing adapter | Baseline has server-side Tawked Verify and existing Twilio paths, sanitized errors, Saudi canonicalization, request/verify limits, expiry and proof separation. No Authentica adapter exists in this baseline. No provider approval or real ENBISHAN send/verify is established. Do not replace the configured fallback before external approval and acceptance. |
| Reporting/control | DONE accounting core / MISSING campaign report | Existing daily/branch/item/driver/accounting settlement, closure and export controls exist. `first_order_gift_claims` is preserved in exports. A dedicated promotional-beneficiary/first-versus-repeat/campaign-closing report was not found in the production baseline accounting report groups. Existing development includes engagement wallets, consent, gifts and restore tests; the new campaign report must be integrated with those stable customer/phone and scope contracts. |
| Safe release | PARTIAL / BLOCKED | Historical core certification exists, but it does not certify new multi-brand activation. Current merge has an inherited Node harness failure described below. PR54/55 are not release-ready. Exact candidate image, rollback, host security, affected PostgreSQL/browser and brand-isolation checks remain required. No production activation is authorized by this document. |

## Exact evidence and limits

- [PR16 — customer/gift/advertising foundation](https://github.com/KPS-COFFEE/KPS-ORDER/pull/16): source `391236094e5c7264449d3a5b4cb7fa7f6437c0bf`, merged `a141ab10c3b6fc125b45ea41bc7575e096beb631`. Historical full V5: 2,257 unique tests; [run39481942818](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39481942818). Morning/owner acceptance: 209 cases, [run39481936378](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39481936378).
- [PR51 — approved orange caption](https://github.com/KPS-COFFEE/KPS-ORDER/pull/51): source `e494d56dc92de2dba8dd713011af9d6208c29f43`, merged `a07e1d35fdfa17b06ebf47f68b774c88972c8203`. This preserves the approved gift phrase, logo, footer, schedule and entitlement.
- [PR60 — integrated operational changes](https://github.com/KPS-COFFEE/KPS-ORDER/pull/60): source `dd9b5812bc49f9f17908e299f4b2ba29701f0b7b`, merged `f34da9c3c7c5bad39b57725c8caa86843e64dee8`. [Full V5 run39873360676](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39873360676): PR evidence records 2,759 unique tests / 2,956 JUnit cases including 197 subtests; zero failures/errors/skips. This is historical evidence, not a rerun on today's new candidate.
- [PR63 — unpaid test classification](https://github.com/KPS-COFFEE/KPS-ORDER/pull/63): source `4462197421ffa6ae32a2d2b27ef1ccba25b7f0cd`, merged baseline `f8f38407bedaa4be5d4db6f87db5687fb0573de6`. Connector confirmed source V5 [39887318691](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39887318691), image/security [39887318652](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39887318652), distinct rollback [39887318716](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39887318716), accounting [39887318707](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39887318707) all SUCCESS. Skipped unrelated workflows are not counted as passes.
- Current merged baseline Driver float/privacy [run39888943522](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39888943522), job120203759865: PostgreSQL/browser step **145 PASS + 7 subtests PASS**, then Node step fails `ReferenceError: currentOdometer is not defined` in `test_blind_driver_closure_ui.cjs`. The shipped app defines that helper; the VM test omitted it. The accompanying narrow harness repair executes the actual helper and asserts the inspection minimum without changing application code, assertions or deadlines.
- Current baseline V5 [39888943254](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39888943254) remained in progress at refresh. Fleet-swap [39888943348](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39888943348) subsequently returned SUCCESS. Hardening [39888943262](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39888943262) subsequently returned FAILURE only in both `representative-render-budget` repetitions; dependency security, load/recovery, two-worker and both proposed-capacity repetitions returned SUCCESS. The baseline YAML still sizes those representative jobs at the legacy app0.5CPU/512MiB and DB0.1CPU/256MiB despite the `current` label: their failures are NOT certification of the current live plan. At500 users legacy P95 was26,789.088ms and10,380.698ms; both retained1000 orders with zero errors. PR41 separately documents actual-current-plan capacity evidence. Manager return [39888943195](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39888943195), map paste [39888943238](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39888943238), amendments [39888943233](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39888943233), fleet log [39888943562](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39888943562), routing [39888943186](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39888943186) returned SUCCESS. These statuses must be refreshed before release.
- [PR54](https://github.com/KPS-COFFEE/KPS-ORDER/pull/54), source `8ad3945a8ddb332a9e8e8a6955d2451e2baa3cd8`: [security run39828470322](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39828470322) reports two legacy tests expecting optional blank neighborhoods (1,197 passed, five skipped); [map/browser39828470151](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39828470151) fails guest checkout enabled assertion after filling neighborhood (26 prior cases passed); [V539828470217](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39828470217) failed. These cannot be waived as passes.
- [PR55](https://github.com/KPS-COFFEE/KPS-ORDER/pull/55), source `d5207c29e82bcce9abd6399af7591f83004dd7db`: [Google acceptance39829146751](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39829146751) and [map acceptance39829146711](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39829146711) CANCELLED; V5 skipped. Google is preparation only.
- [PR41](https://github.com/KPS-COFFEE/KPS-ORDER/pull/41), source `333ce0429a1e52dfd76b54826a43b88ce81807c1`: current 1CPU/2GiB app strict500-user P95 gate FAILED. CPU-only2CPU/4GiB proposal passed two repetitions (P95 2,572.745ms and4,559.855ms), [run39706622162](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39706622162). This does not certify the smaller live service or authorize a paid plan change. No expensive capacity rerun was performed by this audit.

## Local harness verification

Executed the same three Node files used by the failed workflow:
`test_driver_simple_ui.cjs`, `test_blind_driver_closure_ui.cjs`,
`test_driver_alerts_ui.cjs`; all passed. The blind closure harness now includes
the actual shipped `currentOdometer` function and checks that the actual maximum
vehicle reading reaches inspection rendering in Arabic/English with all three
permanent-float cases. `git diff --check` passed. No expensive certification rerun.

## Additional focused CI gate prepared in this change

`.github/workflows/multibrand-final.yml` runs only on pull requests to the
production branch from `agent/multibrand-safe-release-20261023`. Both parallel
jobs check out and record the exact PR head SHA and use isolated local
PostgreSQL18.6, pinned existing requirements and no production secrets. The
browser job installs actual Chromium and WebKit. Explicit required files cover
retained ENBISHAN menu/isolation/shared settlement/customer engagement, the new
customer touch/district/campaign report/AuthentiCA preparation, existing OTP,
KPS order/delivery/permission/accounting regression and actual browser journeys.
No existence-based skip, `continue-on-error`, costly load run or deployment is
included. Existing protected image/security/rollback/full-regression gates are
unchanged. Local YAML/step-shell validation passed; this is prepared CI, not a
claimed remote execution result.

## Final activation gates

Keep ENBISHAN public ordering, new campaign activation, Authentica selection and
production deployment off until exact final-code gates pass, source-approved
menu/assets/configuration and domain/HTTPS are verified, operational/live account
grant requirements are resolved, and the owner gives the requested final
production activation approval. Preserve the configured OTP fallback and all
KPS live orders. Do not alter GoDaddy MX/SPF/routing or any TILQA service.
