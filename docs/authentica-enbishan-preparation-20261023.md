# ENBISHAN OTP: resubmission and integration checkpoint

Status: **PARTIAL — server transport prepared; runtime integration, provider approval and activation blocked**.
Checked 2026-10-23. This checkpoint does not alter identity routes, provider
selection, credentials, DNS, email forwarding, application state or production.

## Application record to submit after the release gates

| Field | Owner-approved value |
| --- | --- |
| Application / requested sender | ENBISHAN |
| Contact | otp@enbishan.com |
| Ordering website | https://order.enbishan.com |
| Existing rejection | Website inaccessible |

The sender is requested, not a claim that Authentica has approved a sender ID.
Do not edit GoDaddy MX, SPF or forwarding; forwarding is already proven.

Before resubmission, capture an unauthenticated HTTPS 200 on the correct
ENBISHAN website, including the brand's approved logo/menu/contact information.
Check mobile Arabic, no administrator-login wall, valid TLS, and redirects that
remain on the intended domain. A preview or an HTTP 200 with KPS branding does
not satisfy this gate. Production activation still requires the final owner go.

## Verified primary references and unresolved contract

Read directly on 2026-10-23:

- https://authentica.sa/en/
- https://github.com/AuthenticaSA/Authentica
- https://github.com/AuthenticaSA/Authentica/blob/main/docs/otp.md
- https://authenticasa.docs.apiary.io/ (returned a JavaScript-only shell)
- https://docs.authentica.sa/api-reference/otp-verification/send-otp
- https://docs.authentica.sa/api-reference/otp-verification/verify-otp

The current official API reference documents v2 JSON send and phone-based
verify. Send accepts `method: sms` and `phone`, with strict `success: true`
acceptance; verify accepts `phone` and `otp`, with strict `status: true`
success. OTP options belong to the application dashboard. The marketing site
still illustrates an SDK endpoint, while the official GitHub example uses a
different verification success field. The adapter uses the detailed current API
reference, not those examples.

`app/authentica_verification.py` implements this fixed HTTPS v2 transport with
a dedicated ENBISHAN secret, no global/KPS credential fallback, bounded timeout,
no redirects or automatic retry, strict booleans and sanitized errors. It is not
imported by any router or selectable in identity settings. It returns no raw
provider data and does not claim to consume a canonical application challenge.

The public reference does not establish the approved application's digit count,
expiry, resend invalidation, single-use or concurrent-request semantics. Because
verification is phone-based, confirm these with the application dashboard/provider
and certify local challenge/resend handling before runtime integration. Never
substitute the free-text SMS endpoint for verification.

## Required implementation after contract confirmation

1. Review the prepared server-only adapter against the approved application
   settings and response contract. Never return a provider body, key, code or
   full phone in logs or screenshots.
2. Use a dedicated ENBISHAN application credential in private service settings;
   never fall back to the KPS application's key or sender. Keep the existing
   KPS provider selection and delivery path intact.
3. Integrate with canonical brand-scoped `otp_challenges`, not a second identity
   database. Bind provider/brand/phone/purpose to server state. If provider
   verification is phone-only, explicitly design concurrent/resend and
   cross-purpose invalidation before accepting it as a challenge proof.
4. Retain local five-minute expiry cap, atomic three-attempt reservation,
   request/IP limits, provider-switch invalidation, single-use claim and owner
   test mode. Match the provider's approved digit length without weakening
   the current four-digit KPS contract.
5. Add an additive provider allow-list migration only with the reviewed
   integration, never a migration that selects/enables the provider. An API
   key alone must not activate a channel.
6. Prove send/verify on a consenting number in owner test mode, record received
   application/sender and expiry/failed attempts/resend/replay evidence without
   the code or key, then obtain explicit approval before public activation.

## Safe current state and evidence

The application currently selects only disabled, Tawked or Twilio Verify.
Authentica remains rejected by both the provider readiness boundary and owner
settings input validation, even if an Authentica key is accidentally present.
Existing KPS messaging remains unchanged. Retaining the current provider is the
safe fallback; automatically routing ENBISHAN through the KPS application's
provider credential is not an approved fallback.

Local synthetic regression:
`python -m pytest tests/test_enbishan_otp_provider_gate.py tests/test_tawked_otp.py -m 'not postgres' -q`
passed **74 tests** on 2026-10-23. The five PostgreSQL cases were deliberately
excluded from that command and are not claimed as passing. They cover single
use, attempts/expiry, owner test isolation, provider switching and channel
pausing; run them against the isolated local test database before release.

`tests/test_enbishan_otp_provider_gate.py` verifies that unavailable Authentica
selection is rejected, global credential presence cannot activate it, and a
brand-specific provider override does not change the KPS global fallback.

The new transport cases cover endpoint/header/payload shape, dedicated key
isolation, strict send/verify acceptance, no redirect/retry, malformed responses
and sanitized failures. They do not prove provider behavior.

These are synthetic/local checks. No Authentica message was sent, no approval
was obtained, and no live delivery/expiry/resend result is claimed.
