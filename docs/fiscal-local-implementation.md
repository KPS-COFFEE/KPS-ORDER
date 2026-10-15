# KPS ORDER local fiscal implementation

Official issuance is disabled. This change prepares immutable **local previews**;
it does not certify a Phase 1 or Phase 2 production invoicing system.
No merge, deployment, live activation, terminal payment, customer data change,
provider integration, DNS change or Render change is part of this stream.

## Identity and address evidence

The owner-approved VAT certificate, number 100241103016858, dated 02/04/2024,
was inspected visually. Seller: مؤسسة ريم فهاد لافي الحربي لتقديم الوجبات.
VAT: 311887526600003. Address transcribed from the certificate:

> حفر الباطن,حفر الباطن,8437 - حي الصفاء,39912

Postal code is 39912. The certificate supplies no verified short national address
code, so none is invented. The Dammam address in the separate historical operating
agreement is not reused for these fiscal documents. Historical agreement and
order snapshots remain unchanged. New local fiscal previews use this verified
identity independently of fulfilment branch, its owner, driver or cash/card method.

The legal supplier name and address are displayed alongside the original KPS logo.
They are not silently omitted in favor of branding. Existing receipt CSS, paper
widths, logo bytes, font sizes, printer configuration and Windows bridge are kept.

## Implemented

- Decimal arithmetic in integer halalas: VAT is included, not added. 39.50 gross
  is 34.35 net plus 5.15 VAT. Discounts reduce taxable value; delivery is included
  at the same 15% rate. Inconsistent saved amounts or items fail closed.
- Actual UTF-8 TLV/Base64 QR tags 1–5, generated with the existing QR encoder.
  Tag lengths count UTF-8 bytes, including Arabic. No trade-name/address tags,
  placeholder images, tracking links or former provider are used.
- Owner-only, brand-scoped local preview route for paid, delivered KPS orders.
  One frozen original per sale, unique PREVIEW numbers, canonical row locking,
  durable idempotency, append-only database protection and audit events.
- Credit previews derive only from existing authorized refund rows and reference
  the original number. Cumulative VAT allocation reverses exactly the original
  tax on a full refund. They do not execute or authorize a payment refund.
- Frozen documents feed both existing thermal renderers. Operational receipts
  and preparation printing continue unchanged; an explicit owner preview button
  opens the fiscal preview with an unavoidable NOT OFFICIALLY ISSUED label.
- Backup/export inclusion and tenant filtering; number sequence restoration and
  startup preservation of saved previews.

## API

- GET `/api/fiscal/readiness`
- POST `/api/fiscal/orders/{id}/preview` (Idempotency-Key required)
- POST `/api/fiscal/orders/{id}/credit-previews` (Idempotency-Key required)
- POST `/api/fiscal/orders/{id}/issue` always returns FISCAL_ISSUANCE_DISABLED
  after authentication and order authorization.

No customer or driver route issues invoices. Terminal collection does not create
another document. Preview numbering is deliberately separate from a future
production fiscal sequence. No production issuance timestamp is claimed: the
preview timestamp is its actual generation time, frozen with its tax data.

## Remaining production work

Confirm applicable ZATCA phase. Where Phase 2 applies, implement and certify the
UBL/XML document, UUID/counter/previous-invoice hash chain, XML hashing/signing,
protected onboarded CSID certificate/key, authentic QR tags 6–9, reporting within
the applicable deadline, durable retries and response archival. No signature,
certificate, clearance or reporting evidence has been fabricated here.

Production numbering, completed-sale issuance, credit/debit-note issuance before
applicable refund/adjustment operations, cancellation policies and archival must
be activated and certified together. Local preview credit notes are not a claim
that the existing refund workflow already issues official tax credit notes.
The five-tag encoder rejects Phase 2 requests explicitly.

Sources checked on 16 October 2026 Riyadh:
- https://zatca.gov.sa/ar/E-Invoicing/SystemsDevelopers/Documents/QRCodeCreation.pdf
- https://zatca.gov.sa/en/e-invoicing/introduction/guidelines/documents/e-invoicing_detailed__guideline.pdf
- https://www.zatca.gov.sa/ar/E-Invoicing/SystemsDevelopers/Documents/20220624_ZATCA_Electronic_Invoice_Security_Features_Implementation_Standards.pdf

## Validation status

Initial source regression: 677 passed, 197 subtests passed, 9 skipped and 662
PostgreSQL-marked cases deselected. The 42 targeted passing cases overlap that
count. Actual QR symbols were decoded with zxing-cpp at 384 and 544 pixel widths.
New real PostgreSQL cases cover cash/card, different retry keys, unique originals,
refunds, immutability, denied roles/scopes and concurrency. Browser acceptance
uses the real owner screen, popup, original logo, Arabic/English and 58/80mm output.
These database/browser cases require the isolated CI environment; do not treat
their presence as a passing result. Local Chromium execution was blocked by an
OS socket restriction and no local PostgreSQL server was available. Physical
paper output still requires a real printer acceptance check.
