# Optional Google Maps customer picker

This is an additional customer picker provider. Installing this code does not
activate Google, create a Google Cloud account, enable billing, replace the
existing Mapbox configuration, or alter the staff map.

## Account and activation prerequisites

1. In https://console.cloud.google.com/, use the business's intended project,
   connect its billing account, and enable **Maps JavaScript API**. A demo key is
   unsuitable for production. The account owner completes any required terms or
   billing steps.
2. Create a dedicated **browser** API key. Set application restrictions to
   **Websites** and allow `https://order.kps.sa/*`. Add only the exact additional
   preview/brand origins that are approved for testing. Restrict the key's API
   access to **Maps JavaScript API**. Use a separate restricted browser key for
   another brand. No Places, Geocoding, Routes, Street View or Map Tiles web-service
   API needs enabling for this pin picker.
3. Keep the key out of source code, screenshots, logs and chat. Set it using the
   existing private server environment settings. This is a browser credential:
   after activation it necessarily appears in the brand's public config and the
   SDK request, so website/API restrictions must already be applied. Never use a
   service-account private key or a server web-service key here.
4. Review the website's existing public privacy and terms notices against the
   actual Google Maps use before activation. Google-provided attribution and
   controls must remain visible. This change does not claim legal approval or
   update the existing legal disclosures.
5. With the prepared candidate deployed, separately enable the customer provider
   and verify real Google rendering with the configured account on iPhone/Safari
   and Android/Chrome, both languages and the exact production origin. That check
   may create a billable map load and requires the approved account/setup. It has
   **not** been performed by the synthetic tests in this change.

No Map ID is required. The picker uses a standard raster `google.maps.Map` and a
centered HTML/CSS pin. It does not use the deprecated `google.maps.Marker` class
or the Advanced Marker library. Google supplies its native map attribution.

## Explicit configuration

For KPS, keep the current configuration while preparing the account/key. These
new settings are independent of `MAPBOX_ENABLED` and staff maps:

| Setting | Value for activation |
| --- | --- |
| `GOOGLE_MAPS_BROWSER_KEY_KPS` | Dedicated restricted browser key |
| `GOOGLE_MAPS_CUSTOMER_ENABLED` | `true` |
| `CUSTOMER_MAP_PROVIDER_KPS` | `google` |

The provider selection defaults to `mapbox`. A Google key or the Google switch
alone never changes that default. If explicit Google setup is incomplete or the
browser key is malformed, the existing configured Mapbox picker is retained;
otherwise the normal GPS/link controls remain available. The public config's
`provider` field reports the provider actually selected, not a claim of successful
billing or live rendering.

Another brand uses `GOOGLE_MAPS_BROWSER_KEY_<BRAND>` and
`CUSTOMER_MAP_PROVIDER_<BRAND>` with its own key; for example `_ENBISHAN`. Existing
server storefront/brand binding continues to apply. Switch the brand selection
back to `mapbox` to restore its configured customer Mapbox picker. No database
migration or staff token change is needed.

## Behavior and limits

- Google SDK loads once, only after the customer opens the picker. The default
  map is reused across closes/reopens; pin movement does not create new maps.
- The customer taps a location or moves the map beneath the center pin. The
  initial city center cannot be confirmed as a new location. The pin's tip is
  the center coordinate, and confirmation calls the existing `saveLocation`
  flow. The server still validates coverage, eligible branches, fees and orders.
- The GPS button requests a fresh, high-accuracy device reading with the existing
  15-second timeout, reports accuracy in metres and warns above 50 metres.
  Manual movement/closing invalidates late GPS callbacks. GPS never commits a
  location without the customer's explicit confirmation.
- Closing/cancelling discards the picker draft and retains the saved location.
  Network/SDK timeout or Google authentication/billing/key failure disables map
  confirmation and explains the existing GPS/Google-link fallback.
- Standard roadmap only, with no Street View, satellite picker, Places search,
  geocoding or routing request. No Google place names, tiles or POI content are
  copied into application storage. The confirmed customer coordinate follows
  the existing location handling; no new server coordinate collection is added.
- Google is loaded in the language selected when it is first opened, with region
  `SA` and the stable `quarterly` channel. Our dialog labels/messages follow the
  live AR/EN toggle; Google's native map labels follow its initial SDK language
  until the page is reloaded.
- Google-specific CSP is limited to the selected customer document. Staff
  Mapbox CSP and unrelated pages retain their previous policy. Script execution
  is allowed only on the exact Google Maps bootstrap/static script paths; no
  `unsafe-inline` or `unsafe-eval` script permission is added. The real configured
  account rendering check must also verify CSP in the browser. Provider domains
  can change and should be reviewed against Google's domain reference.

## Validation

All credentials/SDK objects in the automated tests are synthetic. No Google
request or billed live map session is required by these tests.

```bash
python -m pytest -q tests/test_customer_google_map.py tests/test_customer_map.py tests/test_dispatch_map_config.py
node tests/test_customer_google_map.cjs
python -m playwright install --with-deps chromium webkit
TEST_BROWSER=1 python -m pytest -q tests/test_customer_google_map_browser.py tests/test_customer_map_browser.py
```

The Node suite executes the actual shipped adapter, including lazy load callback,
precision/races, explicit confirmation, cancellation/reuse, bilingual states,
availability recheck, SDK/network/authentication failure and timeout. The browser
suite checks the provider router, mobile dialog controls and unmodified synthetic
attribution on Chromium/WebKit, plus intercepted loader failures. Synthetic DOM
success is not certification of Google billing, real tiles, website restrictions
or physical-device GPS precision.

## Official implementation references

Reviewed 2026-10-22:

- https://developers.google.com/maps/documentation/javascript/get-api-key
- https://developers.google.com/maps/api-security-best-practices
- https://developers.google.com/maps/documentation/javascript/load-maps-js-api
- https://developers.google.com/maps/documentation/javascript/map-rendering-type
- https://developers.google.com/maps/documentation/javascript/reference/map
- https://developers.google.com/maps/documentation/javascript/content-security-policy
- https://developers.google.com/maps/domains
- https://developers.google.com/maps/documentation/javascript/error-messages
- https://developers.google.com/maps/documentation/javascript/policies
