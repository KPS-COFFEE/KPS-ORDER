# Final security release: SMS continuity and safe rollback addendum

Reviewed source: `7ee848fcdb309d518e6178686e905ad7d63bf551`, branch
`agent/kps-order-security-closure-20261018`. This is a proposed release runbook,
not a claim that deployment, real SMS verification or rollback has occurred.

## Verified source boundaries

- `app/sms_rollout.py::work_sms_required` always requires SMS for KPS work
  identities. Missing/false rollout environment flags and provider failure must
  not enable passwords. Other brands with persisted active policy remain
  mandatory after the global flag is removed.
- `app/auth.py::login` denies a correct password with `WORK_SMS_REQUIRED` for
  mandatory brands; the central session factory independently requires a
  verified canonical employee SMS challenge. Preserve both guards together.
- `app/employee_sms.py` accepts exactly four ASCII login digits, keeps employee
  and sensitive-owner challenge namespaces separate, checks active account and
  current mobile/brand/provider again, and consumes each challenge atomically.
- Owner sensitive SMS is bound to one current owner session and current
  registered mobile. Ordinary login SMS and old password/email step-up
  timestamps cannot authorize sensitive actions.
- Existing unexpired, unrevoked sessions remain valid subject to fresh role,
  account and scope checks. This is temporary continuity, not a guarantee after
  session expiry and not a substitute for proving fresh SMS sign-in.

## Release record required before an approval is executable

Record the candidate source SHA, immutable image digest and retained archive
checksum; scanner versions/database timestamp; PostgreSQL 18 test evidence;
the current running revision; configuration comparison; and the separately
approved safe rollback image digest. A mutable tag, source SHA alone, or a
rebuild of a previously reviewed Dockerfile does not identify tested image
bytes. Do not describe any empty item as certified.

The source-only security work has not certified live owner SMS delivery,
possession or the production provider configuration. Retain a valid current
owner work session on the original browser. Under a later owner-approved
release procedure, prove a fresh four-digit SMS owner sign-in in a second
browser context and a separate sensitive-owner SMS verification on that new
session. Keep the original session until acceptance. Do not perform an account,
order, payment, invoice or shift mutation just to prove sensitive SMS. Certify a
second already-authorized owner if one exists; do not create one for this task.

An accepted send or provider dashboard success is not delivery/login proof.
Missing/duplicate registered work mobiles, an uncertified SMS provider, failed
fresh owner login or failed sensitive-owner proof block release until resolved
through an independently approved audited process. No production accounts were
changed in this review.

## Safe deployment sequence after exact-candidate owner approval

1. Confirm the recorded immutable image passed the full mandatory security
   suites, actual browser cases, image vulnerability/secret scan, readiness and
   focused suites inside that same image against isolated PostgreSQL 18. Do not
   deploy if required steps were skipped, failed, or ran on other image bytes.
2. Confirm read-only account/provider readiness and the real owner proofs above;
   retain the existing owner session and current provider configuration. Freeze
   the approved configuration comparison for the change window.
3. Publish/deploy only the reviewed image by immutable digest in the approved
   service/window. Do not change DNS, database records, SMS policy, cookie names,
   session signing/hashing inputs or provider service identities as an incidental
   release action. Do not run data-restoration or account-rewrite migrations.
4. Check source/image revision, health and readiness; preserved owner session;
   new owner four-digit SMS login and session-bound sensitive SMS; representative
   existing staff four-digit login; and sanitized authentication/provider error
   rates. Validate successful password-only authentication remains impossible.
   Use no business transaction merely for authentication smoke testing.

## Recovery decision tree

### Image/startup regression with healthy SMS

Stop wider acceptance. Apply only an owner-approved forward repair or switch
to the **separately retained and tested immutable safe rollback image** recorded
above. Its source must include all mandatory SMS/session/email guards, the same
four-digit contract and current database/schema compatibility. Re-run its
PostgreSQL 18, owner continuity and password-denial gates before designating it
as a rollback artifact. The pre-security production revision is not an approved
fallback: it may restore password-only access. The audited image with open
HIGH/CRITICAL findings cannot silently become a clean fallback either.

Do not roll back the database or reverse identity settings; retain orders,
invoices, accounts, sessions and shifts. After an approved image switch, repeat
the same revision, health, readiness, owner-session and SMS proofs. If there is
no independently tested safe fallback artifact, record **rollback not ready**
and hold deployment; a written intention is not a prepared rollback image.

### Provider outage or bad provider configuration

An application downgrade does not repair SMS delivery. Retain the valid owner
session, halt new sensitive work, and restore the previously certified provider
configuration only through the approved operations procedure. Certify a new
four-digit owner login and a fresh sensitive SMS before acceptance. Never turn
off mandatory policy, enable email/password fallback, rotate owner mobiles,
mark phones verified in SQL, or add a universal/test OTP to recover service.

If all valid owner sessions expire and owners cannot receive SMS, secure new
login remains unavailable. Use only a separately approved, documented identity
and mobile re-enrollment procedure. The current patch does not demonstrate an
out-of-band owner re-enrollment mechanism; an unproven one must not be invented
or represented as available. This residual operational limitation blocks an
unconditional promise that the owner can never be locked out.

## Existing test evidence to retain for both images

`tests/test_sms_mandatory_policy.py` covers valid-password denial for all work
roles; canonical four-digit SMS login without email; rejection of forged session
proof; existing-session continuity; password/old timestamp sensitive denial;
email-token retirement; same-mobile customer/work separation; and two real
AR/EN browser cases when they actually execute.

`tests/test_owner_sensitive_sms.py` covers session binding, code normalization,
replay, CSRF/current authorization and separate Twilio service requirements.

Additional existing suites worth retaining alongside the mandatory focused
list: `tests/test_employee_sms_portals.py`, `tests/test_employee_sms_diagnostics.py`
and `tests/test_tawked_otp.py`. They exercise certified-provider prerequisites,
four-digit service configuration, provider-reference matching, replay and
concurrent employee verification, account revocation/mobile change, rate limits
and safely redacted failure diagnostics. They use synthetic provider I/O;
passing them does not certify real delivery. They are absent from the current
`docs/security-closure-tests.txt` list at the reviewed SHA, so cite their actual
results separately or add them without removing any existing required suite.

## Scope confirmation

This review inspected repository code and documentation only. It sent no real
SMS, changed no account or business data, performed no production login, and
made no Render, DNS, merge or deployment action.
