# AUTH-OBS — reviewed SMS-only transition and recovery

Status: candidate implementation and isolated testing only. This document does
not authorize deployment, a provider send, account edits or production changes.

## Candidate policy and proofs

KPS work accounts (owner, admin, managers, staff and drivers) always require a
four-digit SMS challenge for a **new** work session. Provider failure, an old
pilot record, a missing environment flag or `EMPLOYEE_SMS_EMAIL_RETIRED=false`
cannot enable password-only KPS access. The existing global
`EMPLOYEE_SMS_LOGIN_ENABLED=true` extends the same mandatory rule to every
brand; an individual brand's persisted `employee_sms_rollout.mode=active` also
requires SMS after the global flag is removed. Other explicitly legacy brands
keep their prior policy. There is no testing/development bypass in runtime code.

Password hashes are preserved. A correct password receives `WORK_SMS_REQUIRED`
without a session cookie. Customer sessions and customer email verification
remain separate. SMS work verification never requires email verification.
Existing unexpired, unrevoked work sessions continue under their current role,
permissions and scope; the candidate does not bulk-revoke them.

Password and staff email links/codes cannot authenticate work identities or
supply sensitive proof under the mandatory rule. The centralized session
factory checks the verified canonical `employee_otp_` challenge, its principal,
brand, current registered mobile, SMS provider/channel and freshness. Client
JSON cannot supply this internal proof parameter. Normal employee SMS login is
not owner step-up. Sensitive authorization requires a recent verified
`owner_sms_` challenge bound to the exact current owner session and mobile.
A pre-existing password/email `step_up_at` alone is rejected.

Queued work identity mail is reauthorized before send and rejected after the
policy cutover. Pre-existing staff invitation/recovery tokens remain in storage
for audit but cannot activate staff, create work sessions or provide step-up.
Operational mail and customer email identities are not retired.

The operations UI starts with the password form hidden, fetches the brand's
read-only login-policy endpoint, displays four-digit SMS controls, and blocks
forced password form submission when mandatory. The server remains authoritative
if a caller bypasses the UI or changes `brand_id` in a query string.

## Read-only deployment preflight — required before any later approval

Run aggregate-only checks with authorized read-only access. Do not export real
customer data or copy production identities into test databases. Record counts,
provider configuration presence and revision identifiers; redact phone numbers,
provider credentials, cookies, OTP values and access tokens.

1. Record current running revision and candidate SHA. Compare auth, identity,
   session and portal configuration since the audited revision. Recheck the
   intended brand policy and every application's environment flags.
2. Count active work accounts missing a normalized Saudi mobile, duplicate work
   mobiles within the same brand, inactive/pending/revoked accounts that should
   not gain access, and staff whose registered mobile needs owner confirmation.
   Count owners with a usable registered mobile and independently certified
   current SMS access. Customer and staff mobiles may coincide; that is allowed.
3. Read the SMS channel/provider state: active, enabled and delivery-proven for
   each mandatory brand. Confirm provider credentials are configured without
   printing their values. For Twilio, independently certify a dedicated
   four-digit employee service and a **different** sensitive-owner service;
   preserve the customer and owner-phone services. For Tawked, use its existing
   four-digit contract and purpose-separated, session-bound challenge references.
4. Under separate explicit authorization, certify real owner **login and
   sensitive SMS** before the release, while retaining a valid current owner
   session. Certify a second authorized owner if available and representative
   staff roles. No real certification is performed by this security-only task.
   Provider dashboard claims or an accepted send do not prove delivery/login.
5. Resolve missing or ambiguous staff mobiles through the existing authorized,
   audited administration flow **before** deployment. Never set verification
   timestamps on somebody's behalf, substitute an operator-owned mobile or
   infer ownership from an order/customer record. Verify recovered possession
   using canonical SMS. Do not erase existing password hashes.
6. Review invitation drafts and pending email-only accounts. Newly approved work
   identities require owner SMS authorization and a registered mobile, without
   an email requirement. Preserving pending rows must not automatically activate
   them or broaden their role/scope. Confirm session/context-cookie continuity
   for the owner's supported browser and same-mobile customer/work separation.

Hold the release if any required owner cannot receive/verify SMS, any mandatory
brand lacks a certified provider, or mobile conflicts leave staff unable to
access required operations. Existing sessions are continuity aids, not a
replacement for certifying fresh owner sign-in and sensitive proof.

## Limited release proposal — pending owner approval

Prepare one reviewed candidate commit, immutable candidate image digest,
PostgreSQL 18 test evidence, SBOM, configuration comparison and backup/rollback
references. Obtain owner approval for that exact candidate before publishing.
Keep existing sessions and data intact. There are no password-hash or account
rewrite migrations. Deploy only the intended service/revision in an approved
window; observe health/readiness, sanitized authentication failures, SMS provider
errors, owner access and one representative staff flow before wider acceptance.
Any future real SMS or sensitive operational smoke check requires its own
approved execution scope; never run payment, refund or print merely to validate
authentication. No 500-user load test is required for this patch.

## Recovery and rollback boundaries

If SMS is unavailable **before** release, stop the release and repair/certify the
provider or resolve registered-mobile readiness first. There is no password-only
emergency exception, magic OTP, hidden account or alternate work-email fallback.

After release, retain existing valid owner sessions while restoring the approved
SMS provider configuration through the authorized operational process. A verified
second owner can perform existing audited staff recovery with fresh sensitive
SMS. A lost mobile requires documented identity/ownership checks and an approved,
audited mobile re-enrollment process; email or password alone must not grant a
new work session or a sensitive authorization. If all owners lose SMS possession
and no approved re-enrollment path exists, escalate to the owner and block new
sensitive work. Do not invent a backdoor; that operational recovery capability is
a separately reviewed prerequisite for deployment.

Application rollback is an explicit owner-approved release action. Preserve
password hashes, account IDs, invoices, orders, sessions and financial history.
Never automatically roll back to the audited vulnerable revision: that would
restore password-only work authentication. Prefer a forward repair or a prepared
known-good image retaining the mandatory guard. If no such image exists, record
that limitation and pause deployment until the owner approves a concrete safe
rollback plan. Do not change DNS, Render plans, shifts or financial records as
part of this candidate task.

## Evidence boundaries

The new mandatory-policy tests prove prevention on the candidate, using real
server endpoints, isolated synthetic PostgreSQL data and mocked provider I/O.
They include valid passwords denied, actual canonical four-digit SMS login
accepted without email for all work roles, email token issue/send/consume denied,
old password timestamps rejected, sensitive SMS accepted, incorrect roles denied
and same-mobile customer/work separation. Browser cases are separately marked
and must actually execute before claiming DOM/browser validation. An unrun or
skipped browser test is not evidence of a browser PASS. Production provider
availability, mobile possession and deployment configuration remain read-only or
unverified here, as documented in the closure report.
