# KPS ORDER / ENBISHAN execution review — 23 October 2026

**Release state: DRAFT / NOT ACTIVATED.** This is an implementation inventory and review handoff, not a production certificate.

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
- Both HTTP and HTTPS requests to `order.enbishan.com` returned **503 from the upstream/proxy**. They do not prove a working ENBISHAN origin or a valid origin TLS certificate. Public activation and Authentica resubmission readiness are **not verified**.
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

The corrected combined source passed **217 focused local tests**, with 15 PostgreSQL cases explicitly excluded locally. New public default-brand and verified-work projection cases are included in the mandatory isolated PostgreSQL gate. Initial browser results also identified missing consent-read mocks and a closed mobile navigation fixture; their corrections retain all business assertions.

Final exact-head CI links and results belong in PR #65 after the corrective source is published. Historical successful suites remain evidence for their own source only.

## Reviewable previews

- [Approved ENBISHAN customer menu simulation](https://enbishan-customer-preview-ali.bushyangel64.chatgpt.site). Existing Site, version 10. Its ordering, fees, OTP and operating events are browser simulations; it is not the combined backend or production.
- [Actual approved KPS creative screenshot](multibrand-visuals/kps-approved-gift-390-initial-head.png) and [Arabic report screenshot with synthetic data](multibrand-visuals/campaign-report-ar-1366-initial-head.png) are initial-head visual evidence; see [provenance](multibrand-visuals/README.md).
- Current approved KPS creative is in `public/assets/kps-gift-creative.js` and `.css`, with source and visual checks in `tests/test_gift_creative_browser.py`. The older KPS offer-choice demo is not presented as the final approved advertisement.
- New operational report route: `/campaign-report`, available after deployment from the authorized operations menu. The current draft adds no live route.

## Final owner activation gate

Do not merge/deploy/activate this candidate while required checks or operating inputs remain unresolved. Nine normal startup migrations inherited from the existing ENBISHAN branch (064–071 and 079) run even with feature flags off; inspect their compatibility and backup plan before approving any production deployment. Two separate multi-brand migrations have their own activation guard. No migration ran against production during this execution.

Keep the existing KPS provider and orders intact. No live order/customer/menu/permission data, GoDaddy MX/SPF/email forwarding, TILQA service, domain activation or production environment was changed by this execution. Final owner approval remains required after the concrete release evidence passes.
