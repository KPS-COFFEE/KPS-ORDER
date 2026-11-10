# ENBISHAN OTP: gated canonical integration and resubmission

Status: **implementation prepared behind closed gates; provider approval, verified
application settings and consented live delivery remain unverified**.
Checked 2026-10-23. No provider was enabled, no message was sent, no production
setting or DNS/email-routing record was changed by this implementation.

## Application record

| Field | Owner-approved value |
| --- | --- |
| Application / requested sender | ENBISHAN |
| Contact | otp@enbishan.com |
| Ordering website | https://order.enbishan.com |
| Existing rejection | Website inaccessible (owner-provided notice) |

The requested sender is not proof of Authentica approval. Keep GoDaddy MX, SPF
and tested forwarding unchanged. Before resubmission, capture an unauthenticated
HTTPS 200 on the intended ENBISHAN website, with valid TLS, correct approved
brand/menu/contact content and no administrator login wall. A KPS page or a
simulation does not meet that requirement. Final owner production activation
approval remains a separate release gate.

## Primary contract references

Rechecked the current primary API reference:

- https://docs.authentica.sa/api-reference/otp-verification/send-otp
- https://docs.authentica.sa/api-reference/otp-verification/verify-otp
- https://github.com/AuthenticaSA/Authentica/blob/main/docs/otp.md

The detailed reference documents fixed HTTPS v2 endpoints, `X-Authorization`,
SMS/E.164 delivery, boolean `success` for sending and boolean `status` for
verification. The older GitHub example illustrates a different success field;
this adapter follows the detailed API reference. API requests do not invent
sender, digit or expiry payload fields: these are application dashboard settings.

The public references do **not** establish ENBISHAN's approved digit count,
expiry, replacement-on-resend or single-use semantics. The implementation does
not infer them. It requires independently verified application settings before
any provider selection or request can succeed. Synthetic fixtures are not that
verification.

## Implemented canonical flow

`app/authentica_verification.py` uses the fixed HTTPS origin, dedicated
`AUTHENTICA_ENBISHAN_API_KEY`, strict response booleans, bounded timeout, no
redirects, no automatic retries and sanitized errors. It rejects other brands,
a missing key and reuse of a global Authentica credential. Provider body,
headers, recipient, code and key are never included in public errors. Durable
pre-send and successful-send audits contain only the challenge ID, brand,
reviewed contract hash and test-mode flag.

`app/authentica_challenges.py` integrates the existing customer request/verify
routes and canonical `otp_challenges`, identity linking, channel delivery proof
and revocable sessions. There is no second identity database or staff/owner
sensitive proof route. Authentica cannot be selected as the global provider and
cannot service KPS or employee/owner-sensitive challenges.

Phone-only provider verification requires more than a random local challenge:

1. A PostgreSQL nonblocking session advisory lock serializes send, resend and verification
   for each ENBISHAN phone across customer login, phone change and owner probes.
   Another process receives a clear retryable error; it cannot overlap calls.
2. Resending expires every older unconsumed Authentica challenge for that phone
   in a committed transaction before contacting the provider. The session lock
   survives that commit; no database transaction or brand-wide channel row lock
   spans provider latency. Release is checked in `finally`; an uncertain release
   closes/discards the connection. The application contract must confirm that
   the provider replaces the previous phone code on resend.
3. The pre-send commit retires older challenges and stores an already expired
   generation. Failed/unknown sends, a process crash or a later SQL/audit/commit
   failure therefore cannot revive an old challenge and preserve resend cooldown. They do
   not leave a usable local challenge or trigger an automatic paid retry.
4. The existing phone/IP request limits remain. A reviewed resend cooldown of
   at least 60 seconds, three attempts, IP verification limit, exact contract
   hash, stored phone and brand, per-provider digit length and local maximum
   five-minute validity are enforced. Expiry is checked against wall-clock time
   again after provider latency.
5. Attempts commit before provider verification. Invalid provider checks keep the
   committed attempt; uncertain results retire that local challenge. Successful local consumption,
   customer proof/linking and session creation are atomic. A failed identity
   finalization rolls back all partial identity writes and retires a provider
   proof that may already have been consumed. Cookies are copied only after
   commit. Replays never create another session.
6. Owner delivery test mode retains the existing work session and records only
   channel delivery proof. It cannot create a customer login or become an
   employee/sensitive owner proof. Public activation still requires a real
   delivery test and the existing channel controls.

Existing KPS/Tawked behavior and its four-digit validation remain. Only an
approved Authentica challenge returns its configured length, allowing the
customer input to adapt. No ENBISHAN fallback borrows the KPS sender credential.

## Closed runtime gates and reviewed settings

The following are requirements to prepare privately **after** obtaining actual
provider evidence; their presence is not asserted here:

- Exact-release `customer_sms/enbishan` and `authentica_customer_sms/enbishan`
  gates, each with enabled flag, acceptance SHA and owner approval SHA matching
  the runtime revision. Existing multi-brand/host/operating release gates remain.
- Dedicated ENBISHAN Authentica key in the server secret settings only.
- `AUTHENTICA_ENBISHAN_APPLICATION_CONTRACT`: strict JSON with only
  `application_id`, `sender` (`ENBISHAN`), `provider_approval_reference`,
  `settings_evidence_reference`, `code_length`, `expiry_seconds`,
  `resend_cooldown_seconds`, `resend_replaces_code` and
  `verification_consumes_code`. The last two must be true and supported by
  provider/dashboard evidence. No code length or expiry value is supplied as a
  presumed ENBISHAN default. Length may be 4–10, provider expiry 30–3600 seconds
  (local validity is capped at 300), cooldown 60–600 seconds.
- `AUTHENTICA_ENBISHAN_APPLICATION_CONTRACT_SHA256`: the exact UTF-8 JSON hash
  reviewed with that evidence. Any changed contract invalidates older local
  challenges; changing a selected provider also expires its pending proofs. An in-flight
  send is bound to the unchanged channel configuration generation, so switching
  away and back cannot revive a retired generation.
- Explicit `identity_channels` selection for ENBISHAN SMS, initially `testing`
  and disabled. The settings route rejects an unapproved provider before writing
  the selection. An API key or input enum alone grants no capability.

Additive migration `097_authentica_customer_challenges.sql` extends the existing
provider allowlists and adds nullable per-challenge contract hash/length plus
scope checks, a partial phone-generation index and an ENBISHAN SMS configuration
generation trigger. Provider/state/enabled or delivery-proof presence changes advance that generation;
unrelated successful delivery-proof refreshes do not. It does not select a provider,
enable a channel, rewrite historical rows, create credentials or grant work
proofs. It runs with normal startup migrations and therefore belongs in the
production schema/backup/locking/recovery gate review. Older KPS rows keep null
new columns. Unrestricted rollback with live Authentica challenge data requires
separate approval; disabling a provider is not a database downgrade.

## Test evidence to capture

`tests/test_authentica_challenges.py` supplies synthetic strict-contract checks
and real isolated PostgreSQL/HTTP witnesses for send/verify, owner-only probes,
expiry, three attempts, resend replacement, failed-send retirement/cooldown,
provider/contract switches, concurrent send/verify, single session, wrong-brand
constraints, KPS digit preservation and failed-finalization rollback. The
transport boundary tests retain malformed/false response, redirect, timeout and
secret-sanitization coverage. These tests never contact Authentica.

Before public enablement, a consenting owner must complete actual send/verify
in owner test mode and verify sender/application, expiry, failed attempts,
resend invalidation and replay without putting the code/key in evidence. Obtain
provider application approval and retain the existing safe provider/guest policy
until then. No live success or Authentica approval is claimed by this document.
