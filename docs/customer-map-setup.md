# Customer map picker

The storefront now offers an optional Mapbox pin picker. The customer opens the
map, taps or drags the pin (or uses device location), then explicitly confirms.
Only confirmation calls the existing location/eligible-branch flow. Cancellation
keeps the previous saved location. The SDK and one reusable map are loaded only
when requested, not on each page update or pin movement.

Activation requires an actual Mapbox account and public URL-restricted read token.
Set `MAPBOX_ENABLED=true`, `MAPBOX_CUSTOMER_ENABLED=true`, and
`MAPBOX_PUBLIC_TOKEN_KPS` for KPS. The customer switch defaults off independently
of the existing staff map. A separate
`MAPBOX_PUBLIC_TOKEN_ENBISHAN` controls Enbishan. Secret `sk.` tokens are refused.
No token is configured by this implementation; the existing location controls
remain available when disabled, missing a token, unsupported or network-blocked.
Use account URL restrictions for approved preview and production origins.

The fixed SDK is Mapbox GL JS 3.33.0 with Streets v12. Attribution is retained.
The customer switch permits the versioned CDN, blob worker and required Mapbox
read/session endpoints on storefront documents. Staff pages retain their own
authenticated map configuration and independent CSP gate. Storefront referrer
policy becomes strict-origin-when-cross-origin so URL-restricted tokens work.
No geocoding or turn-by-turn navigation APIs are added.

Tests use a synthetic SDK and do not contact Mapbox or create billed sessions.
Actual map rendering, mobile WebGL, token restrictions and map labels require
verification with the configured account before activation approval.

Reference: https://docs.mapbox.com/mapbox-gl-js/guides/security-and-testing/
