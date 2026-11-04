# Owner source menu review — activation OFF

Current manifest: all 17 owner decisions are resolved under the 20 October approval at the end of this document: **31 draft cards / 95 canonical rows**. The original 20-card assessment below is retained as source-review history. Current artwork status is recorded in [image preparation](../image-preparation.md).

Source: `Enbishan Menu_Arabic_English هنجر.xlsx`, `THE MENU`, rows 3–58.
SHA256: `ca574c39384e250535397f4bac40dfabc234487ffee8fa1d623344e085402a1e`.

Reproduce offline with `python tools/enbishan_workbook.py SOURCE.xlsx --reference-menu`.
`reference-menu.json` preserves every source column, original worksheet row,
price, image reference and pending decision. It contains no customer data.
Sale prices exclusively use column I (سعر المنيو بعد الخصم), original prices
use J (السعر الاساسى). L (Item Price) remains source provenance only. Equal
prices show one amount; a lower original price fails validation.

The 56 source rows produce 39 unambiguous source variants, expanded to 51
size/flavour combinations in 20 draft products. Seventeen rows are held for
review: 10–13 (grape-leaf/cabbage option list includes an ambiguous heading),
19 (choose-three repeat policy), 41 (Crispy & Cabbage differs from Arabic name),
42–45 (Khaliya family names), 52–54 (Mojito naming/composition), and 55–58
(Ramadan assortment naming and seasonal availability). They are preserved in
inventory, not silently discarded or made orderable.

Merged C/D/H cells are expanded only inside their actual workbook merge ranges.
No fill-forward guesses are used. Piece counts distinguish the two medium
crispy grape-leaf variants (10 and 15 pieces). Product identifiers derive from
the reviewed bilingual identity, not worksheet row numbers; repeated identical
imports retain identifiers. When renaming an item, retain its reviewed product
code through the canonical import API rather than treating a new semantic key
as an update. Source reordering alone does not alter the product code.

There are 49 image-link rows and seven missing links (35–40 and 56). The links
are hosting pages, not approved product image assets. Distinct size images are
preserved with option references; no arbitrary image is chosen for the product.
Upload/associate approved originals using scoped product assets before review.
No logo is embedded in the workbook. No logo or product image was fabricated.

Read-only HEAD requests checked the 43 unique hosting links: 31 pages responded,
12 remained unverified (request/HTTP failure). See `image-link-checks.json`.
A reachable hosting page is not certification of image content, brand identity,
resolution or ownership, and no linked image was installed or activated.

All prepared products remain disabled, draft and source-unverified. The
existing import API additionally requires Enbishan owner scope, reviewed and
current catalog digests, owner SMS step-up and idempotency. No import or
activation against production was performed.

Compiled option values include explicit English size/piece and flavour labels.
Categories have Arabic and English presentation labels from a fixed mapping of
the source categories. These translations do not change source-derived product
IDs, option IDs or prices. Conflicting labels for the same source option and
conflicting English/Arabic size values fail closed for review.

Focused source acceptance: 21 PASS, one PostgreSQL test excluded from that
source-only invocation. Database/browser certification must be reported
separately on the final integrated revision.


## Owner decisions — 20 October 2026

The current manifest resolves all 17 formerly held rows. Reproduce the same
source with `--reference-menu --owner-approved-20261020`. This explicit flag is
bound to the reviewed workbook SHA256; changed workbooks fail closed pending
review. The unapproved mode continues to show the original held decisions.

The approved source produces 31 disabled draft cards from 56 source rows
(95 canonical size/flavour rows). The small mixed-mahashi card offers three
choice slots using the six source choices and their stated piece counts.
Its 120 permitted distinct selections enforce no repetition on both the server
and existing browser price matcher. The price stays SAR 20 for every selection.
Mixed grape/cabbage and crispy/cabbage sizes state the half-and-half composition.
Khaliya is one card with four flavours. Each mojito card includes its source
plate and offers six drink flavours. Four Ramadan boxes retain their source
compositions/prices and use the existing owner rename/enable/disable controls.
Khaliya is approved for all four Enbishan branches; brand/branch operation
activation is a separate owner-controlled step.

This update does not activate ordering, send OTP, apply a production import,
or invent missing images (including Ramadan row 56).
