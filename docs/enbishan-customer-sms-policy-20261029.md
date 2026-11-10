# Enbishan customer checkout requires SMS

The approved customer journey is public browsing followed by mobile-code
verification before placing an order. A guest contact, an email proof, or a
typed phone number cannot authorize checkout. Existing verified customer
sessions retain the current 30-day proof lifetime.

This correction makes the Enbishan requirement independent of the former
`customer_phone_required=false` setting and enforces it for fresh sessions,
existing guests, direct order API requests and pending checkout retries.
Migration 126 corrects the stored setting for Enbishan only. Guest contact
sessions remain separate from permission to order. Delivery-manager entry
continues through the existing authorized delivery-center workflow.

If SMS is unavailable, browsing remains available and checkout requires the
code; there is no automatic guest-order fallback. KPS policy, provider routing,
credentials, staff sign-in and shared operations remain unchanged.

## Release limitation

The live Enbishan public identity response on 29 October 2026 reported an
active SMS channel with historical delivery proof but `configured=false` and
`available=false`. This does not identify the failed provider/configuration
gate: the public response intentionally collapses these failures.

Do not claim a missing key, successful provider approval or real SMS delivery
from this result. Follow `enbishan-sms-activation.md` to establish the dedicated
sender and the accepted runtime revision. Those gates are not relaxed by this
change. Local synthetic tests cannot establish live SMS delivery.

No live settings change, real SMS, merge or production deployment is part of
this prepared correction. Checkout activation requires the existing owner
authorization and a real delivery/verification check against the accepted
release.
