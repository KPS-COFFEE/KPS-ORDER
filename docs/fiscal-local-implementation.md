# KPS ORDER fiscal engine

## Deployment and regulatory boundary

Invoice issuance is **disabled by default**. Migration and deployment do not
activate any fiscal policy or convert historical receipts into tax invoices.
The application implements transactional Phase 1 simplified invoice and credit
note generation; it also retains clearly labelled local previews.

The owner's 16 October follow-up requests actual invoices across all registered
KPS branches without treating an unverified regulatory phase as confirmed.
An explicit `owner_requested` issuance mode therefore records `unconfirmed`
applicability, an empty evidence reference, the real owner decision and no ZATCA
integration claim. It uses the same immutable invoice/credit ledger and default
printing path for completed paid deliveries in every KPS branch. It does not
retroactively issue historical sales, invent an authority notice or enable Phase 2.
`issuance_enabled` reports this operational setting separately from the existing
`official_issuance_enabled` field, which remains false for unconfirmed applicability.
This mode is not certification that the business meets its regulatory obligations.

An append-only, owner-only policy decision records the applicable phase,
evidence reference, reason, and optimistic revision. Phase 1 activation requires
an evidence reference. This is an owner attestation, **not automated verification
of a ZATCA notice** or a claim of regulatory approval. Actual applicability must
be checked against the establishment's records before enabling issuance.

Phase 2 activation fails closed. UBL XML, a fiscal hash chain, onboarded CSID/key,
cryptographic stamping/QR tags 6–9, and reporting/recovery are **not implemented**.
No signatures, certificates or reporting evidence are fabricated. A Phase 2
business cannot use the Phase 1 toggle as an alternative to those requirements.
Direct live ZATCA access is not needed to test the completed local Phase 1 flow.

## Verified identity and approved presentation

Certificate 100241103016858, dated 02/04/2024, supplies:

- Seller: مؤسسة ريم فهاد لافي الحربي لتقديم الوجبات
- VAT: 311887526600003
- Address (verbatim): حفر الباطن,حفر الباطن,8437 - حي الصفاء,39912
- Postal code: 39912

No short national address code is invented. The previous Dammam address is not
used. All KPS delivery fiscal sales use Reem, irrespective of fulfilment branch,
driver or card terminal. Other brands and non-delivery sales are not silently
assigned this seller.

The existing original logo and large heading (كبز قهوة مختصة / KPS COFFEE) remain.
At the owner's direction there is no duplicate small brand/legal-name line on
the printout. QR tag 1 and the saved seller retain the legal Reem name. City
repetition is removed only for display; the certificate string remains unchanged.
The assertion that the registered trade name alone satisfies supplier naming
has not been independently certified here; verify the legal presentation before
production activation. No claim of full ZATCA compliance is made.

Paper sizes, existing fonts/layout, print dispatch, paired devices and logo are
preserved. Optional owner-only occasion text prints above the original logo:
240 characters / six lines, blank disables; Arial/Tahoma/Times New Roman,
12–32 pt, bold/regular, right/center/left. New orders freeze text and formatting;
amendments, fiscal documents, credits and reprints retain it. Preparation tickets
omit it. No live occasion text was changed by this implementation.

## Accounting and QR

Menu prices and delivery fees include 15% VAT. Decimal calculations in integer
halalas with half-up rounding yield 39.50 gross = 34.35 net + 5.15 VAT. Discounts
reduce taxable value; fees are included once. Saved item and order totals must
reconcile. No 15% surcharge is added above the customer price.

QR uses the existing local QR encoder to render Base64 TLV tags 1–5: legal name,
VAT number, actual UTC issuance timestamp, VAT-inclusive total and VAT amount.
UTF-8 byte lengths preserve Arabic. No tracking URL, placeholder, trade-name tag,
address tag or former POS provider is used. Phase 2 is rejected by this encoder.

## Completed-sale issuance and credits

Migration 061 adds `fiscal_documents` and `fiscal_policy_decisions`, both immutable.
An enabled owner policy causes paid delivery completion to issue one original
in the same database transaction. A unique original-sale index, canonical order
lock, unified production number sequence, durable request replay and audit events
prevent duplicate originals, including terminal-payment retries. Preview numbers
remain entirely separate.

Owner manual issuance supports eligible completed-sale recovery and returns an
existing original unchanged. It records the actual generation time, never
backdates it. Historical sales with regular refunds require review rather than
silently creating a new original. Cancelled or incomplete sales cannot issue.
Existing post-completion amendment restrictions remain intact.

Each authorized refund against an issued sale saves a credit note **before** the
refund row. A deferred FK commits both together; a constraint trigger rejects
refund bypass without a matching note. Partial notes allocate cumulative VAT;
a full refund reverses the original gross and VAT exactly. Notes reference the
original number and UUID. No original is edited/deleted. If policy is disabled,
new credits and the corresponding refunds fail atomically; existing originals
remain available. Historical unissued sales retain their original refund process.

Saved documents freeze monetary values, seller, items, payment method, branch
label, translated item labels, occasion and source revision. Standard browser and
device print paths use the saved original. The order screen lists originals and
credit notes for reprinting. Customer document access uses canonical ownership;
policy and manual issuance are owner-only. Export, tenant filtering, sequence
restoration and startup preservation cover both new tables. No operating data
is reset or rewritten.

## API

- GET `/api/fiscal/readiness`: actual policy status and Phase 2 gaps.
- GET/POST `/api/fiscal/policy`: owner read / audited decision (idempotency required).
- POST `/api/fiscal/orders/{id}/issue`: owner issuance/replay (idempotency required).
- GET `/api/fiscal/orders/{id}/documents`: authorized saved documents and print data.
- POST `/api/fiscal/orders/{id}/preview`: owner immutable preview, clearly not issued.
- POST `/api/fiscal/orders/{id}/credit-previews`: owner previews from authorized refunds.

## Validation

Source tests use independent TLV parsing and real zxing-cpp QR decoding at thermal
widths. PostgreSQL acceptance covers cash/card delivery, payment retries,
concurrent issuance, immutable rows, partial/full refund totals, credit-before-refund
ordering, rollback, direct refund bypass, Phase 2 blocking, disabled policy and
role/brand isolation. Browser acceptance opens the real AR/EN thermal popup,
verifies original logo and 58/80mm dimensions, and decodes screenshot QR pixels
against the saved original. Physical paper output still needs an actual printer
witness; software rendering is not physical-device certification.

Exact-commit CI results are reported separately; test presence is not a passing
result. Resource-specific load failures remain separate deployment-capacity gaps.

Sources checked 16 October 2026:
- https://zatca.gov.sa/en/E-Invoicing/Introduction/Pages/Roll-out-phases.aspx
- https://zatca.gov.sa/en/e-invoicing/introduction/guidelines/documents/e-invoicing_detailed__guideline.pdf
- https://zatca.gov.sa/en/E-Invoicing/Documents/Einvoicing_FAQ_EN.pdf
- https://zatca.gov.sa/ar/E-Invoicing/SystemsDevelopers/Documents/QRCodeCreation.pdf
