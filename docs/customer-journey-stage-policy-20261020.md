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

## Still required for the full approved demo scope

- Configure and locality-test Mapbox, then implement the interactive in-page pin
  picker. This checkpoint retains the existing geolocation/manual-pin controls.
- Implement and certify gift redemption, owner start time, campaign eligibility
  and once-per-verified-phone database constraints; no gift is granted here.
- Complete owner advertisement effects and the three device preview workflow.
- Complete morning execution routing, selected-branch preparer controls and
  morning user scopes without exposing the execution branch to customers.
- Implement the approved unified manager control room against the real APIs.
- Finish browser and PostgreSQL certification, review, then request customer
  production publication approval. There is no production deployment in this PR.

## Validation

- Local: 36 policy, phone normalization, role-denial and customer status tests passed.
- Local JavaScript syntax and Python compilation passed.
- Local PostgreSQL integration not run: no local server; installing packages failed
  on environment permissions. No production DSN was used.
- Local browser checks not run: Chromium is absent. Six browser cases are prepared
  for AR/EN at 390/768/1366 px, with synthetic APIs and no SMS or payments.
- Dedicated branch CI runs PostgreSQL 18, pickup progress integration and the six
  browser cases. Results must be read before calling this checkpoint certified.
