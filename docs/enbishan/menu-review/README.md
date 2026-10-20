# Owner source menu review — activation OFF

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

Focused source acceptance: 20 PASS, one PostgreSQL test excluded from that
source-only invocation. Database/browser certification must be reported
separately on the final integrated revision.
