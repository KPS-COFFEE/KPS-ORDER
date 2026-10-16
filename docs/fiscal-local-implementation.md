# KPS ORDER local fiscal implementation

Deployment default: official issuance remains disabled pending evidenced phase applicability and owner activation. The application now includes a transactional Phase 1 issuance engine as well as immutable local previews. This is not Phase 2 certification.
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

## Owner print presentation decision

The printed preview uses only the existing large brand heading (كبز قهوة مختصة / KPS COFFEE), with no duplicate seller line, at the owner’s explicit request. The saved legal seller and QR tag 1 remain Reem Establishment unchanged. This omission of the legal supplier name from the printed output is an unresolved compliance gap; official issuance remains disabled. City repetition is removed only in presentation; the certificate address remains preserved.

## Optional receipt occasion message
Owner operating settings accept receipt_occasion_text (plain text, up to 240 characters / 6 lines; blank disables). New orders freeze it in their snapshot, amendments preserve it, fiscal previews and credits inherit it. Browser and device receipts print large centered bold lines above the original logo; preparation tickets and QR fields are unchanged. No operational setting was enabled by this code change.

Owner-only occasion formatting: Arial, Tahoma or Times New Roman; 12–32 pt; bold/regular; physical right/center/left alignment. Text and formatting are frozen in each new order and inherited by fiscal previews/credits. Browser and Windows bridge consume the saved formatting. Installed device fonts may use system fallback. Physical printer and Windows compilation validation remain pending.


## Completion follow-up: transactional issuance engine

Migration 061 introduces a separate immutable fiscal document ledger and append-only
owner policy decisions. Production documents never reuse preview numbers. A saved
owner decision with Phase 1 applicability evidence is required for issuance. No
policy is inserted or enabled by migration or deployment. Phase 2 activation is
rejected: real XML, cryptography, CSID and reporting are still unavailable.

The delivery completion transaction issues the original after paid delivery when
the policy permits it. Collection from a driver terminal does not issue another
invoice. Order locking, a unique original-sale index, durable request idempotency
and immutable documents protect retries, concurrent workers and reprints.
Manual owner issuance provides recovery for an eligible completed sale; it does
not backdate its issuance timestamp or rewrite historical preview documents.
Historical sales with existing regular refunds require review and cannot be
silently turned into new originals.

Before recording each authorized refund against an issued invoice, the canonical
refund transaction saves its corresponding credit note. The deferred refund FK
requires both records to commit together. A database constraint trigger prevents
refund callers from bypassing the credit. Full refunds reverse the original VAT
exactly; partial credit allocation uses cumulative Decimal rounding. Existing
post-completion amendment prevention stays in force; issued invoices are never
edited or deleted. Disabling issuance also blocks new credits and associated
refunds against issued sales until the issuance capability is restored.

Saved originals feed existing browser and device print mechanisms. The new
Invoices and credit notes action retrieves saved originals and credits with the
same order authorization. Preview labels remain on previews only; no preview is
promoted to an issued document. Customer reads of documents use canonical order
ownership, while owner policy and issuance are restricted server-side. Backup,
tenant filtering, deferred references and sequence restoration include the ledger.

New acceptance tests exercise real delivery/collection/refund endpoints for cash
and card; duplicate payment/retry requests; issuance concurrency; immutable rows;
refund/credit rollback; direct refund bypass rejection; disabled/Phase 2 guards;
role/brand isolation; Arabic/English thermal data; real symbol decoding against
saved original and credit fields. CI results must be recorded against the exact
commit before claiming these PostgreSQL cases pass.
