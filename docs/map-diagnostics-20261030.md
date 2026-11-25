# Map diagnostics — 30 October 2026

Read-only probes, approximately 08:50–08:55 UTC. No configuration, live order,
subscription, permission, service plan or deployment was changed.

## Confirmed

- Render service `srv-db4s1v942hec73epmar0` belongs to the already-approved workspace
  `tea-d9ttnf142hec7393049g`, uses KPS-COFFEE/KPS-ORDER protected branch
  `agent/kps-order-emergency-v4`, and has automatic deploy disabled.
- KPS public customer map configuration returns enabled=true with Mapbox provider,
  Streets v12 style and a public-token-shaped value. No token value is retained here.
- Enbishan public customer map configuration returns enabled=false. The public
  response alone does not distinguish the customer feature flag from a missing
  brand token. Do not silently copy another brand's token or enable the flag.
- Exact Mapbox GL JS v3.33.0 JavaScript and CSS URLs return HTTP 200.
- Using the KPS public configuration token and correct site Referer/Origin, the
  Streets v12 style returns HTTP 200. One test Directions request between two
  generic Hafar city coordinates returns HTTP 200, code=Ok and one route.
- Live /ops.html and customer home CSP permit the configured SDK and style,
  tile, font and Directions endpoints. Their cross-origin Referer policy is
  strict-origin-when-cross-origin.
- Recent service log history includes successful POST driver-location-batch and
  GET map-config requests. HTTP 200 is transport evidence only; it does not prove
  fresh positions for every driver or accepted points within every batch.

These findings rule out a globally unavailable KPS public token, SDK URL or
Directions service in this probe. They do not certify the authenticated staff
map, every tile, server Matrix token, every driver, or phone WebGL behavior.

## Source defects repaired in this batch

- A downloaded SDK script without the expected global previously left one
  permanently rejected shared promise. Future mounts inherited the same failure.
- A stalled SDK request previously remained pending indefinitely. A bounded
  fifteen-second deadline now clears the failed attempt for a future retry.
- Any runtime map error showed a generic failure banner indefinitely even after
  the map had recovered. A loaded style reaching idle now clears that banner;
  an unloaded style retains the warning.
- Shared requests, late callbacks and timers are covered by executable loader
  tests. No extra map instances, steady polling, provider calls, GPS tracking or
  financial/work-time mutations were introduced.

## Validation and remaining limits

- Five Node loader lifecycle tests passed; JavaScript syntax check passed.
- Three existing Python map-pin/customer-map tests passed.
- Chromium regression was extended for transient error/recovery without remount.
  Local browser installation was blocked by a truncated upstream download;
  Chromium execution remains pending in CI, not marked passed.
- Actual authenticated staff map inspection is blocked by the expired browser
  session. No OTP was requested and no owner security control was bypassed.
- Server `MAPBOX_ROUTING_TOKEN`/Matrix authorization was not available through a
  read-only environment listing tool and remains unverified.
