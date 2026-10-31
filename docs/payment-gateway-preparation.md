# Payment gateway preparation — disabled release

Owner instruction, 14 October 2026: prepare online payments without enabling
them, and keep the foundation usable with providers other than Moyasar.

## Included now

- Owner Settings → Payment gateway preparation, in Arabic and English.
- A planned provider for each canonical seller, inherited from existing seller
  profiles and branch assignments. This avoids assuming that the Reem and Sami
  entities can settle into one merchant account. Other brands use their own
  profiles and credential scope.
- Named suggestions: Moyasar, PayTabs, HyperPay and Tap; another provider can be
  saved by its identifier. A suggestion or saved plan is **not an integration**.
- A provider-neutral trusted payment context, attempt reference, normalized
  verification result and adapter registry. Adding an adapter does not change
  the current order/collection API contract.
- Moyasar's read-only sandbox verification adapter. It fetches payment evidence
  from the fixed official API using seller-scoped **test** credentials and
  verifies payment ID, paid status, integer amount, SAR currency, no refund,
  and exact brand/seller/order/attempt metadata. Raw card/token responses are
  excluded from the normalized result.
- Owner-only, CSRF-protected, idempotent and audited preparation saves. A
  changed seller/branch mapping invalidates an open form's revision.

## Disabled boundaries

There is no customer online-payment option, charge-creation endpoint, active
webhook, live payment request, or setting that enables real charging in this
release. The owner screen explicitly displays **Disabled**. Credentials alone,
provider selection, or a stored `enabled` flag cannot enable payment.

No existing order, collection, refund, driver custody, branch payment method,
tax profile or historical snapshot is modified by saving a plan. No schema,
production secret, paid resource or customer domain change is needed now.

The provider registry currently contains only Moyasar's sandbox verification
adapter. Other selections correctly report that an adapter is still required.
The preparation response also retains the live checkout/reconciliation/refund
acceptance requirements; it must never present them as passed.

## Safe testing contract

Automated tests use `httpx.MockTransport` with synthetic fixtures. They do not
contact a gateway, charge a card or establish merchant activation.

For a later separately approved sandbox verification, the operator can derive
the credential slot with
`payment_gateways.credential_prefix(provider, brand_id, entity_id)` and place a
test secret in `<prefix>_TEST_SECRET_KEY` using private Render environment
settings. The prefix hashes the exact provider/brand/seller identity to prevent
normalization collisions. The sandbox verifier also requires the explicit
`KPS_PAYMENTS_SANDBOX_REQUESTS_ENABLED=true` gate; live/public keys are rejected.
There is no browser route that invokes the verifier in this disabled release.

## Later activation work

1. Approve the gateway merchant account for each selling entity and agree its
   rates, settlement account, enabled payment methods and refund procedures.
2. Complete that provider's checkout and refund adapter using its official
   contract. Lock prices/currency to the server quote, persist a unique payment
   attempt and request key before external I/O, and reconcile ambiguous outcomes
   instead of creating a fresh charge on a timeout.
3. Bind fetched provider payment IDs uniquely to their stored attempt. Never
   trust redirect parameters or an unsigned callback; independently verify
   provider status, amount, currency, seller and order/attempt scope.
4. Release paid orders through the canonical order pipeline once only, and
   distinguish gateway receipts from driver cash/card collection. Test retry,
   cancellation, late success, refunds, settlement and export/restore behavior.
5. Register the actual customer subdomain for Apple Pay where the selected
   gateway requires it, then pass isolated sandbox and owner-approved real
   payment/refund journeys on the exact release before customer activation.

This is deliberate preparation, not a promise of one-click live activation or
support for an arbitrary provider without its adapter and acceptance tests.

## Official references

- https://docs.moyasar.com/guides/card-payments/min-integration
- https://docs.moyasar.com/api/metadata
- https://docs.moyasar.com/api/other/webhooks/webhook-reference
- https://docs.moyasar.com/guides/apple-pay/web-registration
