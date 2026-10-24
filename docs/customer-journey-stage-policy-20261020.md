# Customer journey and owner stage policy — implementation checkpoint

Base: KPS-COFFEE/KPS-ORDER `797cdc1771ef4bc15fa9673c83dee5be0a8da24b`.
Customer production has not been changed. Both new feature switches default off.

## Implemented in this checkpoint

- Real owner-only, brand-scoped policy API with existing session/CSRF checks,
  owner step-up, audit, durable idempotency and optimistic version conflict detection.
- Orange/red thresholds per stage, total Hafar/outside limits, selectable
  assignment clock start, and a draft tester in the owner interface.
- New orders capture targets in the canonical immutable performance snapshot.
  Later edits apply to new orders. Existing order/report targets are preserved.
- Staff order summaries show the stage alert and total duration. Timing derives
  from recorded events and driver task timestamps; reads never reset clocks.
- Customer entry behind the owner switch: name/mobile first, existing OTP flow,
  then location and server-qualified branches before the menu. Saved basket is
  retained; location is stored by brand and authenticated customer ID.
- Arabic/English copy and responsive entry CSS. No demo OTP exists in this code.
- Saudi mobile inputs 5/05/966/+966 normalize to one canonical identity.
- Customer on-the-way status starts at actual order-specific driver pickup,
  independently of the separate manual trip departure action.
- Gift policy is independently owner-controlled, with a Riyadh start time,
  catalog-backed selections and branch paid-subtotal minimums. Quoting never
  consumes eligibility. The order and brand/canonical-phone claim commit
  atomically; concurrency, retries, failure rollback and backup restore are tested.
  Changing campaigns or cancelling an order does not reset the lifetime claim.
- Owner advertisement preview uses real 390/768/1366 px iframe viewports with
  storefront CSS. Images remain uncropped. Preview does not upload or publish.
  Advertisement activation remains separate from gift eligibility.

## Still required for the full approved demo scope

- Configure and locality-test Mapbox, then implement the interactive in-page pin
  picker. This checkpoint retains the existing geolocation/manual-pin controls.
- Choose and implement the requested image-specific advertisement effect once
  its image and effect are approved; no blanket flashing effect is imposed.
- Complete morning execution routing, selected-branch preparer controls and
  morning user scopes without exposing the execution branch to customers.
- Implement the approved unified manager control room against the real APIs.
- Finish whole-journey browser and PostgreSQL certification, review, then request customer
  production publication approval. There is no production deployment in this PR.

## Validation

- Local: 75 unit/source tests passed; 16 PostgreSQL cases deselected because no
  isolated local database is available. Their integration run is in CI instead.
- Local JavaScript syntax and Python compilation passed.
- Local PostgreSQL integration not run: no local server; installing packages failed
  on environment permissions. No production DSN was used.
- Local browser checks not run: Chromium is absent. CI runs real Chromium with
  synthetic customer APIs and PostgreSQL 18; no real SMS or payments are sent.
- CI run 39400396735 (90c20bc) passed 106 tests, including gift rollback and
  backup restoration. CI run 39402035791 (7a8bff0) passed 108 tests, including
  the three-device advertisement preview in Arabic and English.
- Additional inherited production CSP coverage and preview screenshots are
  being added; a subsequent run must certify that revision before review.
