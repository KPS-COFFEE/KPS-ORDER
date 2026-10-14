# KPS ORDER — Saudi PDPL cross-border transfer readiness (internal, NOT a compliance certificate)

Status: **OPEN / NOT APPROVED FOR PUBLIC CUSTOMER LAUNCH**. Prepared 2026-10-15.
Scope: independent KPS ORDER test service on Render. No TILQA or DNS change.
This is an evidence register and pre-release decision checklist, not a legal opinion or an executed agreement. Do not store secrets or personal records here.

## 1. Verified operational facts

- Existing test web service: `srv-db4s1v942hec73epmar0` (Frankfurt), single instance, 0.5 CPU / 512 MB; auto-deploy off.
- Existing private managed PostgreSQL 18: `dpg-db4rjgo473hc738soueg-a` (Frankfurt), 1 GB, 0.1 CPU / 256 MB; public database IP allowlist empty; HA not enabled.
- Database is paid and eligible for Render point-in-time recovery; actual available restore points, snapshot/export custody, external copy and periodic alerts require separately recorded provider evidence.
- Underlying web-service filesystem is ephemeral. The project hosting docs indicate protected application attachments and report bytes are persisted as PostgreSQL BYTEA records rather than durable local web files. Validate the currently deployed code path before launch.
- KPS customer, delivery, driver and support data may include names, Saudi mobile numbers, verification state, delivery addresses and precise pins, order history, customer messages/optional images, staff/driver identifiers, GPS/shift data, vehicle information, receipts and audit/security events. Do not classify all GPS or ordinary location data as sensitive by default, but assess tracking and linkage risks.
- Render's DPA states its **primary processing operations are in the United States**, while our web app and database hosting region is Frankfurt. Render's trust page lists US-based subprocessors (AWS, GCP, Cloudflare, ClickHouse). A Frankfurt service does **not** prove exclusive handling within Germany.
- SMS verification via Tawked, mapping/location-related APIs, SMTP and any third-party integrations need their own processing-country, subprocessor and transfer assessments; their data locations have NOT been verified.

## 2. Saudi legal framework — verify applicability; no automatic approval

- Saudi PDPL Article 29 permits transfers for specified purposes subject to conditions. Cross-border hosting is **not automatically prohibited or automatically lawful**.
- The Transfer Regulation Article 3 says the competent authority publishes an adequacy list. **No published adequacy determination for Germany or the United States has been established in this review.** Do not infer Saudi adequacy from GDPR/ISO certification.
- Article 4 sets out specific exemptions and appropriate safeguards (Saudi standard contractual clauses, binding common rules, approved certification) with **conditions for each case**, not a blanket entitlement for any foreign host. Verify the actual legal transfer route with a qualified Saudi privacy adviser before relying on one.
- Article 7 requires a documented transfer risk assessment in the cases it specifies (including transfers invoking Article 4). A proportionate written assessment is being started below but remains pending verification, review and owner sign-off.
- Render's existing public DPA and GDPR/EU/UK SCC coverage do not expressly execute **SDAIA's Saudi SCCs**. Do not record `transfer_safeguards` as approved merely because the Render DPA exists; if Saudi SCCs are required for the selected legal route, obtain proper bilateral execution/confirmation. The official Saudi SCC wording must not be modified except fillable fields.
- Controller must keep written processing-activity records (ROPA) for ongoing processing and five years after the respective activity ends (per Implementing Regulation Article 33).

Authoritative sources:
- Saudi cross-border Transfer Regulation: https://dgp.sdaia.gov.sa/wps/portal/pdp/knowledgecenter/details/RegulationonPersonalDataTransferOutsidetheKingdom
- Saudi Standard Contractual Clauses: https://dgp.sdaia.gov.sa/wps/portal/pdp/knowledgecenter/details/StandardContractualClauses
- Official SCC PDF: https://sdaia.gov.sa/Documents/StandardContractualClausesForPersonalDataTransferAR.pdf
- Saudi ROPA guide: https://dgp.sdaia.gov.sa/wps/portal/pdp/knowledgecenter/details/PersonalDataProcessingActivities
- Render DPA: https://render.com/dpa
- Render trust/subprocessors: https://render.com/security
- Render PostgreSQL recovery: https://render.com/docs/postgresql-backups

## 3. Processing-activities register — draft, controller validation pending

| Activity and data subjects | Minimum required data | Purpose/legal-basis assessment | Foreign recipient/country (verified or pending) | Retention/rights |
| --- | --- | --- | --- | --- |
| Customer OTP and account | name, verified mobile, session metadata | Order fulfilment / authentication; legal basis to be approved | Render DE + Render potential US operational access; Tawked country unverified | 30-day **verified sign-in session** from successful proof, not deletion of order history; OTP 5-minute validity |
| Checkout and order fulfilment | order, products, branch, contact, payment-method metadata | Provide requested product and pickup/delivery; legal basis to be approved | Render DE; designated branch/driver in KSA | Retention by business + legal obligations; needs a documented schedule |
| Delivery, driver tasks and incidents | delivery pin, driver identity/shift/location, optional photographic proof | Dispatch, evidence, safety and operational accountability | Render DE; mapping and other processors to identify | Role-specific access, necessity and location tracking limits; schedule pending |
| Support and privacy rights | complaint text/photos, linked order, callback contact, request/response | Service and legal rights | Render DE; any message transport processor TBD | Case closure schedule and legal holds pending |
| Security, audit and backup | hashes/session logs, timestamps, restricted archives | Security/integrity, recovery, legitimate compliance duties | Render DE and potential US access; off-provider location TBD | Limited access, TTLs, key custody and protected archive expiry pending |

No marketing without separately recorded opt-in. No full payment card/PIN/CVV storage in KPS ORDER.

## 4. Initial transfer-risk assessment — DRAFT, needs owner/legal review

| Risk | Impact and rationale | Existing verified control | Residual/action |
| --- | --- | --- | --- |
| Inadequate transfer legal basis or assurances | HIGH: processing outside KSA before evidence of lawful route | Render public DPA, GDPR controls and available ISO/SOC audit material | **OPEN**: legal route, Saudi SCC where applicable, vendor response, documented owner decision |
| Subsequent transfers / support access outside Frankfurt | HIGH: Render DPA describes primary US operations | Render lists subprocessors and 10-day pre-notice mechanism | **OPEN**: request exact data/support/log/backup regions, access model, country transfer map and objection process |
| Unauthorised customer/driver data access | HIGH: mobile identifiers and precise destination pins | Database external allowlist empty; app has server-side auth/session enforcement in source | **OPEN**: live authorization and access-log audit, least privilege, encryption/key handling evidence, privacy-aware logging |
| Lost or irrecoverable order records | HIGH: business disruption and customer impact | Paid Render PostgreSQL PITR; private backup and isolated restore procedures exist in source | **OPEN**: check actual restore window, verify protected export and alert, independent copy/location, repeat isolated restore |
| Unbounded location/photo/OTP retention | MEDIUM/HIGH: excess collection or long retention | Code uses short OTP expiry, protected attachments, customer privacy routes | **OPEN**: approve category-specific retention, cleanup verification, data subject requests and secure erasure |
| Access/transfer through other suppliers | MEDIUM/HIGH: SMS and maps/providers unverified | OTP provider adapter and provider-independent design | **OPEN**: evaluate Tawked, Google Maps, email delivery and later payments separately; no assumptions |

## 5. Pre-public-launch clearance (all required)

- [ ] Confirm the exact current legal controller / owner entity, Saudi commercial registration, approved business address, contact and privacy-response channel. **Do not infer from outdated contracts.**
- [ ] Establish the Saudi Article 29 transfer grounds and whether an Article 3 adequacy route or a specific Article 4 exemption applies for EACH foreign importer/jurisdiction; record legal review.
- [ ] Request written Render confirmation of DPA binding status, Saudi PDPL support and Saudi SCC execution if applicable; record signed/binding evidence, importer entity and governing legal terms.
- [ ] Obtain from Render written country/access/subprocessor mapping (including US operations), backup and logs region, deletion/return and incident notification terms; subscribe to subprocessor updates.
- [ ] Complete and approve the final transfer risk assessment; document effects on data subjects, minimisation, additional safeguards, and a risk owner.
- [ ] Inventory the Tawked/SMS, email and maps disclosures and countries and their transfer basis; do not assume these are only in Germany.
- [ ] Finalise records of processing activities (ROPA) and per-category retention/deletion/rights response procedure, including financial/legal record exceptions.
- [ ] Validate access controls, audit and secret-handling without disabling the private PostgreSQL allowlist. Confirm HTTPS/TLS in transit and encryption at rest evidence.
- [ ] Prove live database recovery/backups and secure archive retention in a separate test environment; keep customer data and keys out of GitHub.
- [ ] Owner signs a dated release decision after legal review, technical audit and customer end-to-end testing. **Do not imply approval from a checkmark or nonempty free-text field.**

## 6. Customer notice + app controls

Current `app/customer_legal.py` publishes privacy, terms and refunds pages before data collection, names Render Frankfurt and leaves controller, registration, address, contact, legal basis, retention and safeguards explicitly incomplete until the owner enters verified facts. Do NOT insert generic “compliant with SDAIA” claims or fill `transfer_safeguards` with Render's EU DPA alone. Update the notice after supplier confirmation to accurately explain relevant later transfers/support access (including US if applicable), in clear Arabic/English.

The live-preview PR currently has unrelated failing full CI/performance gates; **this legal document does not certify engineering readiness**. Do not merge, promote, change `order.kps.sa`, turn on customer SMS, open public ordering, broaden DB networking or move data as a result of this document.

## 7. Vendor response and approval record (to be completed)

- Render ticket/reference: PENDING
- Request date / requester: PENDING
- DPA evidence (version/acceptance): Public version located; account-specific applicability/signature PENDING
- Saudi SCC / equivalent applicable route: PENDING
- Data/subprocessor country map: DE service/DB confirmed; USA operational access declared; details PENDING
- Risk assessment owner/legal reviewer & date: PENDING
- Retention and deletion policy approval & date: PENDING
- Owner launch consent: **NOT GIVEN**
