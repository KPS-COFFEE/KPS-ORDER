# Prelaunch portals and employee SMS — candidate, not activated

No DNS, environment, provider setting, deployment or real SMS was changed.
Two rollout gates default OFF: `KPS_PORTAL_HOST_ROUTING_ENABLED` and
`EMPLOYEE_SMS_LOGIN_ENABLED`. Existing staff email-code/password recovery access
continues while employee SMS is uncertified. Never call this SMS delivery PASS.

## Portal application contract

When the portal routing gate is enabled:

| Exact host | Accepted authenticated roles | Landing |
| --- | --- | --- |
| order.kps.sa | customer | storefront |
| admin.kps.sa | owner, authorized admin | /ops |
| staff.kps.sa | owner, delivery_manager, delivery_staff, branch_manager, driver | /ops |

Every protected request rechecks the canonical PostgreSQL identity. Host-based
checks add to existing endpoint permission and branch checks. Staff endpoints and
operational scripts are blocked on the customer host. Customer OTP and guest
creation are blocked on staff/admin hosts. Writes require their own exact HTTPS
Origin, even when another portal is globally allowed. Session cookies remain
Secure/HttpOnly/SameSite Strict and host-only; they are not widened to `.kps.sa`.
No cross-host session token or cookie is copied. Unknown hosts serve only health
and ready endpoints once routing is enabled. Forwarded host never selects a role.
Email links use the recipient's portal when enabled, including recovery.

Integration: import `portals` in app/main.py; register
`app.add_middleware(portals.PortalBoundaryMiddleware)` immediately before the
existing SecurityMiddleware registration, so existing security response headers
remain outermost. No new router or database migration is required.

## Exact DNS / Render steps for separate owner approval

1. In the EXISTING Render service `kps-order-emergency-v4`
   (`srv-db4s1v942hec73epmar0`), open Settings → Custom Domains and add
   `admin.kps.sa` and `staff.kps.sa`. Keep `order.kps.sa` and its certificate.
2. At DNet, add two CNAME records: name `admin`, target
   `kps-order-emergency-v4.onrender.com`; name `staff`, same target. Confirm that
   this target matches the existing service's actual Render hostname before
   saving. Remove a conflicting A/AAAA/CNAME for only those two names if present;
   do not touch the existing order record. Use the DNS provider default TTL.
3. Verify BOTH domains in Render and wait for BOTH issued HTTPS certificates.
   Provisioning/certificate waiting is independent of application test results.
4. In an explicitly approved narrow release, preserve the current allowed-host
   list and add exact hosts `admin.kps.sa` and `staff.kps.sa`. Add exact origins
   `https://admin.kps.sa` and `https://staff.kps.sa` to the existing origin list.
   Keep `PUBLIC_BASE_URL=https://order.kps.sa`, secure cookies and existing CSRF.
5. Only after host/origin tests, certificates and owner emergency access are
   verified, set `KPS_PORTAL_HOST_ROUTING_ENABLED=true` in that approved release.
   Render environment edits can deploy; they are intentionally NOT executed here.
6. Verify each role on each host, direct API rejection, old sessions, invitation
   and recovery links, and same-host login/logout. Rollback is the previous
   certified revision plus restoring the prior flag/config values; data is shared
   and has not been migrated. Do not activate an unverified release to provision DNS.

## Employee SMS contract

Reuse `/api/identity/staff/code/request` and `/verify`; there is no competing SMS
login API. With the SMS gate OFF, their existing email flow is preserved.
With it ON, the request only targets the normalized REGISTERED mobile of exactly
one active, owner-authorized employee in an active brand. A pending invitation is
not activated by SMS. Unknown, inactive and ambiguous records receive the same
accepted response, without a message or identity creation.

The existing Tawked customer adapter uses four digits. Its certificate does not
prove the required six-digit employee contract. The candidate supports the
existing Twilio Verify adapter only if that provider is already selected and its
SMS channel is active with delivery proof. It additionally requires
`EMPLOYEE_TWILIO_VERIFY_SERVICE_SID`, a distinct, approved six-digit Verify service
under the existing account. This prevents changing the customer's four-digit
service. No provider service is created, purchased or altered. Service GET must
confirm the exact service SID and `code_length=6` before any employee send.

Codes expire locally in five minutes, have three attempts, and are consumed
atomically once. Provider reference, normalized registered phone and SMS channel
must all match. A fresh employee check before session creation rejects revoked,
inactive, changed or unauthorized records. Employee updates after challenge
issuance invalidate it. Sessions reuse the existing canonical session machinery;
no role, branch, activation or account property is inferred from a typed number.
Employee SMS does not grant owner step-up. Existing owner emergency recovery stays.

Live dependency: approved provider credentials, dedicated six-digit service,
actual employee SMS receipt/verification on a separately approved test number,
mobile/desktop bilingual UAT and owner rollout authorization. None is represented
as live PASS. Do not enable SMS merely because mocked tests pass.

Provider contract source: https://www.twilio.com/docs/verify/api/service
(Verify service code length can be 4–10; this candidate requires exactly 6.)
