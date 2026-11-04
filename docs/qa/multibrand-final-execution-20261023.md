# KPS ORDER / ENBISHAN execution review — 23 October 2026

**Release state: DRAFT / NOT ACTIVATED.** This is an implementation inventory and review handoff, not a production certificate.

## Continuation after the owner asked to finish

This section supersedes the earlier implementation inventory where noted.
Exact final-head CI evidence belongs in PR #65; the earlier `44e224be` source
passed all thirteen required workflows and 3345 unique V5 tests, but that is
not a certificate for the additional source below.

| Deliverable | Current review state | Evidence / remaining gate |
| --- | --- | --- |
| Inventory and code | Updated in the same draft PR; no fork or duplicate backend | PR #65 and this document; no merge/deploy |
| Website | PARTIAL, official hostname not activated | New read-only DNS/HTTP evidence and domain activation plan linked below |
| KPS regression | Prior exact source passed; revised source requires its own CI | Retained prior evidence plus mandatory revised exact-head workflows |
| Advertising | Existing engine/approved KPS creative preserved | Existing screenshots and browser tests; no ENBISHAN offer invented or activated |
| Customer journey | Existing approved menu simulation and combined technical journeys retained | Source-approved menu import and operating configuration still require live readiness |
| OTP | Canonical server integration now implemented behind closed release/contract gates | Per-phone serialization, durable retirement/attempts, strict provider contract, expiry/resend/single-use and revised source tests; real provider approval/settings and consented delivery remain unverified |
| Delivery | Source policies preserved | Live Fawzi grants remain unverified; private read-only collector prepared |
| Reports | Complete explicit owner-finalized period report implemented | All beneficiaries, consistent daily totals, immutable audited snapshot, scoped reopening and bilingual full CSV; no offer or accounting mutation |
| Recovery | Private metadata-only preflight prepared | Actual private registry/backup/restore/locking evidence remains unverified |
| Owner gate | Required after technical and operating checks | No production activation approval is requested or inferred from this continuation |

Report finalization generates the full report when the owner confirms the
selected period. It does not assume gift policy revisions are campaign IDs,
invent exposure attribution or shut down an advertisement. A separately
scheduled automatic campaign-end snapshot is not implemented or claimed.

The new additive normal migrations are `096` (empty append-only period report
store) and `097` (dormant Authentica canonical challenge/configuration metadata).
There are now eleven pending normal migrations relative to production source,
all subject to the same production migration/recovery gate. No existing normal
migration checksum was edited.

Fresh evidence and prepared operational steps:

- [DNS and HTTP read-only evidence](enbishan-domain-preflight-20261023.json):
  public resolver NXDOMAIN for ENBISHAN hostname, KPS CNAME present; KPS ready200
  on the retained production source, ENBISHAN upstream503; origin TLS unverified.
- [Concrete domain activation plan](enbishan-domain-activation-plan-20261023.md),
  held until brand routing, recovery and final owner release gates pass.
- [Private read-only production collector](private-production-preflight-20261023.md),
  which never starts the application or runs migrations and always leaves
  backup/restore/activation approval false.
- [Revised gated OTP implementation and actual provider requirements](../authentica-enbishan-preparation-20261023.md).
- [Full period-report behavior and definitions](../campaign-reporting.md).

Authenticated control-plane continuation: the owner completed Render sign-in.
The existing service has only the KPS custom hostname. A fresh provider logical
export was requested and appeared ready at `2026-10-23T11:08Z`, alongside two
18 October exports. [Sanitized control-plane evidence](render-control-plane-20261023.json)
and [provider backup status screenshot](render-backup-status-20261023.jpg) retain
that observation without signed access tokens. Download paths did not materialize
in the documented shared filesystem, so no archive checksum/content integrity,
actual registry/grant readback or isolated restoration is claimed. No network
allowlist was changed. The distroless production image has no supported Shell/SSH
route; no SSH key, API credential or extra backend was created.

Revised source b5c234 passed 146 real Chromium/WebKit browser tests, including
the four new final-report journeys, and all 18 new PostgreSQL OTP witnesses.
The standalone report group exposed missing fixture registration (seven setup
errors); its explicit import was repaired. A test-only owner-denial expectation
was corrected to real role revocation under the retained owner policy. The
accounting browser test now waits for acknowledged initial/language/filter
renders after a deterministic reproduction of its overlapping-load race.
Khaliya's Azure apt downloads exhausted the existing 12-minute job deadline
even though its 129 tests passed; the existing bounded official Ubuntu mirror
setup was applied without changing tests or that deadline. These corrective
files require revised exact-head execution before final certification.

The original inventory and chronological evidence are retained below for their
own source versions.

Supplemental continuation verification: **1684 source tests and 205 subtests
passed**, 1729 database/browser/conditional cases explicitly deselected, with no
failures in that selection. Five Node report UI checks, shipped customer and
owner-probe OTP input-length witnesses, Python compilation, JavaScript syntax
and diff checks passed. The initially outdated nine-file startup inventory
assertion was corrected to the explicit eleven-file inventory while retaining
all prior checksums; its populated PostgreSQL witness now also preserves legacy
OTP/channel rows. These are source checks, not actual PostgreSQL, browser,
provider or production acceptance. Revised exact-head workflow results must be
recorded in PR #65.

[Review PR #65](https://github.com/KPS-COFFEE/KPS-ORDER/pull/65) targets the existing `agent/kps-order-emergency-v4` branch. The initial published source was `ac3ab03d1f4f769590e3b6ce24f29b6596a91309`, tree `d39a5370b00a3840276c62e5d41294e92ac3ec05`. Subsequent corrective commits require their own final exact-head results.

## Implementation inventory

| Workstream | Status | Implemented and verified source | Remaining gate |
| --- | --- | --- | --- |
| ENBISHAN ordering | PARTIAL | Existing development integrated into the current KPS architecture; independent identity, menu variants, customer sessions, grants, delivery and financial scope; approved domain `order.enbishan.com` | Exact final backend/browser results; approved draft import and operating configuration; public domain/HTTPS verification; final activation |
| Advertising | DONE engine / PARTIAL activation | Existing brand-specific scheduled/manual audited banners; KPS approved creative files preserved byte-for-byte; no KPS campaign executable sent in ENBISHAN HTML | ENBISHAN creative/offer only with its own approval; actual enabled campaigns require authenticated readback |
| Approved menu | DONE preparation / PARTIAL operating data | Source-bound 22 October review: 31 retained cards, 28 visible candidates; hidden Pepsi Diet/7UP Diet/Mirinda orange; original prices/options; exact mixed 6/12/24 compositions; original 36 variant preserved; five approved image originals retained by hash | Explicit reviewed draft import, source/commercial verification and operating enablement; no live catalog edit performed |
| Customer interactions | DONE code / PARTIAL acceptance | Synchronous repeated image/card quantity taps; +/-; required size/flavor/color choices; selected-variant image enlargement; optional notes/address description; required delivery neighborhood; Saudi phone paste/normalization; committed replay compatibility | Final real browser and PostgreSQL result on combined source |
| Delivery | DONE core / PARTIAL live grants | Morning/evening manager controls, approved preparation windows, Friday exception, scoped reassignment, geofencing, GPS, default-off customer tracking, settlements/deadlines, conversations and durable alerts preserved | Fawzi's actual grants/account and real scoped operational journeys remain unverified; no account permission changed |
| Maps | PARTIAL | Existing Mapbox/location-link behavior preserved | Google Maps PR #55 remains unmerged and unconfigured; no provider activation claimed |
| Authentica OTP | PARTIAL | Dedicated server-only ENBISHAN v2 transport with fixed HTTPS endpoint, strict responses, dedicated key, sanitized errors; resubmission checklist uses `ENBISHAN`, `otp@enbishan.com`, correct ordering domain | Website reachability, provider approval, application digit/expiry/resend/concurrency contract, canonical challenge integration and real consented send/verify; adapter intentionally not selectable |
| Reports/control | DONE manual report / PARTIAL automatic closing | Brand/branch-scoped daily online orders, canonical phone deduplication, first/repeat customer definitions, gift beneficiaries, policy revision filter, masked phones, audit reads, Arabic/English mobile UI and printable displayed page; existing accounting/driver closure reports preserved | Automatic immutable campaign-closing snapshot/notification is MISSING; no invented scheduler or recipient |
| Release | PARTIAL / BLOCKED | Separate exact-source PostgreSQL 18.6 and Chromium/WebKit jobs plus retained protected regression/image/security/distinct rollback checks | All final-head mandatory checks, reviewed pending migrations/backup, live configuration evidence, final owner approval |

## Production and access evidence

- Intended Render workspace: **My Workspace** (`tea-d9ttnf142hec7393049g`). Existing service: **kps-order-emergency-v4** (`srv-db4s1v942hec73epmar0`). Auto-deploy is off. No duplicate backend/service/database was created.
- At initial inspection, `/` and `/ready` on `https://order.kps.sa` returned **200**, with revision `f8f38407bedaa4be5d4db6f87db5687fb0573de6` and connected database.
- While this review was in progress, the target independently advanced through PR #64 to `4e4d781b4fa82b7459410694ff5c4ac06842e6e7`; Render reported live deployment `dep-dbdckefb9u1s7397qt50`. That source has been preserved in this candidate. This execution did not trigger that deployment.
- Read-only checks at **03:10 UTC** confirmed KPS `/` and `/ready` returned **200** on revision `4e4d781b4fa82b7459410694ff5c4ac06842e6e7`, database connected. [Exact HTTP evidence](domain-http-evidence-20261023.json) records requests and responses. Both HTTP and HTTPS requests to `order.enbishan.com` returned **503 from the upstream/proxy**. They do not prove a working ENBISHAN origin or a valid origin TLS certificate. Public activation and Authentica resubmission readiness are **not verified**.
- GitHub and Render connector access are available. Render's browser session requires sign-in. The read-only database connector is blocked by the existing empty external IP allowlist. No network isolation was changed to obtain access.

## Validation and concrete repairs

The initial tree passed **171 focused source/model/security tests**, with five PostgreSQL cases excluded locally and not counted as passes. Four report UI Node checks, actual driver harnesses, Python/JavaScript syntax and YAML/diff checks passed. Local Chromium download returned a truncated archive; browser success is claimed only from actual remote execution.

The first remote combined run is [39891433585](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39891433585). Its failing evidence is retained; it is not a final-head certificate. Review exposed and corrected:

- A distinct rollback build context missing the workbook tool required by its Dockerfile; correction retains the explicit hashed runtime foundation and distinct prior application.
- Legacy browser detail selectors and grouped-product accessible labels after image quick-add behavior changed.
- Verified-customer benefit pricing losing the authenticated principal in the gift quote route.
- A legacy fingerprint test expecting absent optional consent fields rather than the actual production serialization contract; explicit consent/version distinctions remain asserted.
- A real four-thread operational-contact insertion race exposed by the additional brand/id unique constraint; a namespaced transaction lock serializes the same opaque contact identifier, preserving brand/identity checks and the original PostgreSQL concurrency witness.
- Selected ENBISHAN variant thumbnails enlarging base artwork instead of the selected image.
- A staff home-brand login being incorrectly evaluated as a customer storefront operation; home-brand proof is now separate from fresh operational brand projection.
- A synthetic staff-order SQL fixture omitting the new contact lock; it now asserts the exact namespaced lock before insertion while retaining actor, phone and server-price assertions.
- KPS staff retries losing their historical browser key signature and server operation name on multi-brand activation; both legacy KPS identities are retained, while ENBISHAN remains namespaced, with a committed-response replay/cross-brand witness in the mandatory PostgreSQL run.
- Lost optional customer car details in the combined UI, including the empty-plate information in customer confirmation and staff order details; restored the shipped escaped bilingual presentation without changing stored plate values or making plates mandatory.
- Rollback evidence comparing the candidate to GitHub's synthetic PR merge SHA; candidate commit, tree and runtime are now checked against the actual checkout, with stale-source rejection cases.

The corrected combined source passed **217 focused local tests**, with 15 PostgreSQL cases explicitly excluded locally. New public default-brand and verified-work projection cases are included in the mandatory isolated PostgreSQL gate. Initial browser results also identified missing consent-read mocks and a closed mobile navigation fixture; their corrections retain all business assertions.

On intermediate source `e8b2e102c0303208f94c57a1b33aaf7712063625`, the isolated customer/campaign group passed **139 tests** (two browser-only cases executed separately), OTP/identity passed **198** (two browser-only cases executed separately), and KPS delivery/accounting passed **256**. The unchanged four-thread PostgreSQL contact-concurrency witness passed. The dual-brand browser journey group and **17 delivery browser tests** passed; the second browser group passed **86** with two Chromium guest-checkout fixture races corrected by awaiting acknowledged cart addition and modal closure before editing the neighborhood. Its first backend group retained two known scope assertions corrected in the next source. These are overlapping suite counts, not a unique-test total or final release certificate.

The full V5 run also exposed an isolated-database name mismatch, optional-car display regression and mobile roster fixture navigation; these are corrected without relaxing the isolation guard or business assertions. New guarded financial ownership constraints and a genuine populated `4e4d781` upgrade witness are included in the next mandatory exact-head PostgreSQL run.

On source `113041e7e716c13cf47d22c0f5bf3a6f2a8222f8`, [run 39894859054](https://github.com/KPS-COFFEE/KPS-ORDER/actions/runs/39894859054) passed all **131 Chromium/WebKit browser tests**. Its backend passed 244 first-group tests including all 22 new financial ownership witnesses, 143 customer/campaign, 200 OTP/identity and 256 KPS delivery/accounting tests. Four browser-only cases skipped in backend were executed in browser acceptance. The only backend failure occurred after the genuine populated upgrade and both preservation/repeated-startup loops passed: the final synthetic followup cash collection collided with the existing per-order/revision/method uniqueness constraint. The next source uses a fresh ordinary order without changing any application constraint. Counts overlap other suites and must not be summed as a unique total.

A broad local source-only selection passed 1643 tests plus 197 subtests with 1697 database/browser cases deselected; its one outdated menu-render fixture failed, was corrected and passed its targeted rerun. This is not a PostgreSQL/browser release gate.

Final exact-head CI links and results belong in PR #65 after the corrective source is published. Historical successful suites remain evidence for their own source only.

## Reviewable previews

- [Approved ENBISHAN customer menu simulation](https://enbishan-customer-preview-ali.bushyangel64.chatgpt.site). Existing Site, version 10. Its ordering, fees, OTP and operating events are browser simulations; it is not the combined backend or production.
- [New passing-browser KPS creative](multibrand-visuals/kps-approved-gift-390-113041e7.png) and [Arabic report with synthetic data](multibrand-visuals/campaign-report-ar-1366-113041e7.png) come from the 131-pass Chromium/WebKit job on 113041e7; [provenance](multibrand-visuals/provenance-113041e7.json).
- [Actual approved KPS creative screenshot](multibrand-visuals/kps-approved-gift-390-initial-head.png) and [Arabic report screenshot with synthetic data](multibrand-visuals/campaign-report-ar-1366-initial-head.png) are initial-head visual evidence; see [provenance](multibrand-visuals/README.md).
- Current approved KPS creative is in `public/assets/kps-gift-creative.js` and `.css`, with source and visual checks in `tests/test_gift_creative_browser.py`. The older KPS offer-choice demo is not presented as the final approved advertisement.
- New operational report route: `/campaign-report`, available after deployment from the authorized operations menu. The current draft adds no live route.

## Final owner activation gate

Do not merge/deploy/activate this candidate while required checks or operating inputs remain unresolved. Nine normal startup migrations inherited from the existing ENBISHAN branch (064–071 and 079) run even with feature flags off; inspect their compatibility and backup plan before approving any production deployment. The [pending migration source review](pending-migrations-review-20261023.md) records exact hashes, startup locks, financial constraint changes, rollback limits and evidence still needed; it is not a production migration certificate. Three separate multi-brand migrations have their own activation guard; new `003` adds financial brand/order/customer constraints, with real PostgreSQL negative-insert acceptance still required. A genuine populated production-source-to-candidate startup upgrade witness is now part of the mandatory isolated gate; it does not certify production downtime, a backup or unrestricted older-application rollback. No migration ran against production during this execution.

No current Authentica approval notice was found in the connected email account in a narrow read-only search. The earlier rejection is user-reported; no approval, resubmission or actual SMS test is claimed.

Keep the existing KPS provider and orders intact. No live order/customer/menu/permission data, GoDaddy MX/SPF/email forwarding, TILQA service, domain activation or production environment was changed by this execution. Final owner approval remains required after the concrete release evidence passes.
