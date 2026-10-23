# ENBISHAN domain: concrete preparation, no activation

The refreshed read-only evidence is in
`enbishan-domain-preflight-20261023.json`. Google Public DNS returned status 3
(NXDOMAIN) for `order.enbishan.com` CNAME/A queries; its response names the
authoritative response source. KPS's CNAME resolves to the existing
`kps-order-emergency-v4.onrender.com` service. ENBISHAN HTTP/HTTPS requests
returned upstream/proxy 503. That response does not establish origin TLS state.

The Render service is still the existing KPS ORDER backend with automatic
deployment off. The connector has no custom-domain management capability.
Dashboard access was attempted through secure authentication, but a fresh
canonical settings page still displayed sign-in after device confirmation.
No authenticated dashboard/domain association or certificate state is claimed.

## Prepared change after the release gate

| Setting | Intended value |
| --- | --- |
| Existing Render service | `srv-db4s1v942hec73epmar0` / `kps-order-emergency-v4` |
| Custom hostname | `order.enbishan.com` |
| DNS type | CNAME |
| DNS host/name | `order` in the existing `enbishan.com` zone |
| DNS target | `kps-order-emergency-v4.onrender.com` |

Review the live zone and Render domain list first; do not blindly replace an
existing record or assume its absence from this observation. Keep all MX, SPF,
email-forwarding and unrelated records untouched. DNS publication and domain
association are held until approved brand routing and public launch gates pass;
the currently deployed source must not serve a KPS page as ENBISHAN.

Before activation, review the exact candidate's host mapping, explicit existing
database host/name binding, multi-brand acceptance SHA and owner approval SHA.
Prepare the source-approved menu draft and required operational configuration
without copying KPS offers, guessing prices or enabling a new OTP provider.
Obtain actual private recovery/schema evidence before deploying the candidate's
eleven pending normal migrations. Production activation requires the owner's
final approval after these checks.

After approved publication: verify Render domain ownership and certificate
issuance, public resolver CNAME/A, valid HTTPS and HTTP-to-HTTPS redirect, and
an unauthenticated ENBISHAN page with approved identity/menu. Verify KPS's
existing domain and ongoing orders independently. Only then is the website
evidence ready for Authentica resubmission; approval and consented delivery
testing remain separate provider gates.

No DNS, custom-domain association, environment, deployment, live catalog,
permission, provider application, sender or email route was changed here.
