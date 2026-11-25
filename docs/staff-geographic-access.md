# Saudi-only administration and owner exceptions

The owner requested Saudi-only administration for both `order.kps.sa` and
`order.enbishan.com`, with every exception controlled by the owner. The policy
belongs to the shared application, including its Render hostname. It does not
change the public customer ordering area.

## Behavior

- Saudi staff use their existing login and permissions.
- Foreign or unverified entry to work pages and work login is denied. Every
  authenticated staff session is checked again before brand projection,
  including shared order/chat/report APIs and sessions that were opened in SA.
- No role, including owner, has an automatic exemption.
- An active owner grants an identified active employee (or their own account)
  temporary access after a distinct, real, session-bound owner SMS proof.
  Reasons and durations are mandatory. The default is 24 hours; the maximum is
  seven days. Grants can be revoked immediately.
- The employee's own normal SMS authentication is still required. A grant does
  not add a role, a branch, or any permission.
- An owner travelling abroad should issue a self-grant before leaving. There
  is no public recovery bypass or shared universal access code.

Use **Administration → Employees → Access from abroad** on the selected staff
record. Grant history shows the issuer, reason, expiry, redemption and
revocation. The owner copies the temporary link and chooses how to deliver it.
Creating a grant does not send any message automatically.

## Credential handling

Entry links carry a 256-bit random value in a URL fragment. The neutral
`/work-access` page immediately clears that fragment from history and posts it
to the same origin. Fragments are not sent in HTTP URLs or referrers. A link is
single-use and exchanges for a different random, host-only, HttpOnly, Secure,
SameSite=Strict browser cookie. Only hashes are stored. Neither secret appears
in listings or audit details.

The cookie clears only the geographic gate for the exact approved canonical
account; it is not a login session. Redeeming clears an older work identity so
the employee must sign in themselves. Separate customer sessions survive.
Each foreign request checks expiry, revocation, the employee's active status,
and the approving owner's current authority. Brand projection cannot change
the approved user identity. Restoring an archive retires all geographic grants
in the restored schema; business exports exclude these credentials.

## Ingress trust contract

`STAFF_GEO_SOURCE=render-cloudflare` is enabled only on the verified public
Render web service, whose public ingress always passes through Cloudflare.
The immediate socket must belong to explicitly configured private Render
proxy CIDRs. Exactly one valid `CF-Connecting-IP`, country header and Ray ID is
required. X-Forwarded-For, X-Real-IP, browser GPS, language, timezone and
user-supplied context/brand hints never determine allowed country.

Cloudflare Worker subrequests and the cross-zone Worker sentinel IP are
rejected as interactive staff ingress. Duplicate/malformed country/IP headers,
private/non-global visitor addresses and untrusted peers cannot authorize SA.
A known global visitor address whose country is unknown may use an explicit
owner grant; absent trustworthy visitor IP metadata cannot use any grant.

This is an Internet connection country policy. It is not proof of physical
presence: a Saudi VPN or proxy exit can appear to be in Saudi Arabia. Normal
SMS authentication, account authorization, CSRF protection and audits remain
required. Hosting/network changes require re-verifying the ingress contract.

Sources checked on 2026-10-25:

- [Render: real client IP on public services](https://render.com/articles/host-pocketbase-on-render)
- [Render: public ingress and DDoS protection](https://render.com/articles/how-render-handles-ddos-attacks)
- [Cloudflare: request headers and Worker semantics](https://developers.cloudflare.com/fundamentals/reference/http-headers/)

## Rollout and verification

Three explicit settings control rollout; they contain no secrets:

```
STAFF_GEO_MODE=observe
STAFF_GEO_SOURCE=render-cloudflare
STAFF_GEO_TRUSTED_PROXY_CIDRS=10.0.0.0/8
```

`off` preserves the previous policy. `observe` records bounded, sanitized
country/source observations without restricting access; `/ops` and work
`/api/session` responses expose country/source and a truncated IP digest for
verification. `enforce` activates the Saudi/owner-grant rule and removes these
observation headers. Invalid mode/source/proxy configuration blocks startup.

Before enforcement, verify actual Saudi and foreign ingress, and verify that
forged CF country/IP and forwarding headers do not alter the observed source
or country on either official domain or the Render hostname. Only after that
evidence is established should mode change to `enforce`. Verify Saudi work
entry remains available, foreign work entry is 403, public customer pages
remain available, and normal health checks succeed.

Printer-bridge endpoints retain device-token authentication and require Saudi
ingress independently. A human exception cannot be reused as a printer device
permission. Public customer endpoints, provider webhooks and `/health`/`/ready`
are not geographically restricted.

Focused tests: `tests/test_staff_geo.py`, `tests/test_staff_geo_access.py` and
`tests/test_staff_geo_ui.cjs`. The existing protected host, exact-image and
prior-application CI gates also run for the change branch. Local DB tests use
an isolated `test_*` PostgreSQL database with synthetic identities and SMS.
