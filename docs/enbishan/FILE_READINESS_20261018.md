# Enbishan file preparation — activation remains OFF

The owner will supply an updated Excel workbook with products, branch locations,
prices, pictures and before/after prices. An older menu is a layout reference;
it is not authority for a price or an absent/new product. The original logo may
be supplied with that workbook and is not a prerequisite for software testing.

## Implemented preparation

`tools/enbishan_workbook.py FILE.xlsx` inspects the supplied workbook locally,
records its SHA256, lists every worksheet and inventories embedded images. It
does not contact a database or download image links. Oversized archives, duplicate
ZIP members, macros, external workbook links, XML entities and formula cells are
rejected. Unsupported layouts are reviewed and mapped explicitly; values are not
guessed from colours, images, row positions or an older menu.

`--menu-sheet NAME` compiles an explicitly selected worksheet. `--column-map`
accepts reviewed JSON header mapping. Canonical rows contain `product_code`,
`name`, `category`, `size`, `flavour`, `price`, optional `price_before`, English
names and an image reference. Missing source codes can be assigned during the
file review without asking the owner to rebuild the workbook. Such mappings
are data preparation, not a new catalog engine.

One product card supports required size/flavour selectors. Each valid combination
has its own exact SAR selling price and optional higher before price. Unlisted
combinations are unavailable. Before prices never become invoice amounts.
Server prices are authoritative, and saved order units preserve the selected
combination and charged price. Differently priced units become separately priced
snapshot lines, preserving discount bases and accounting totals. Legacy KPS
option pricing and approved menu fingerprints retain their format.

## Controlled data application

1. Inspect the actual workbook and reconcile additions, branches, amounts and
   image associations. Upload original owner assets through existing scoped
   product/appearance APIs; resolve image filenames without remote URL fetching.
2. Use an owner account in its explicitly authorized Enbishan scope.
3. `POST /api/admin/enbishan-menu/preview` with `source_sha256` and canonical
   `rows` returns a draft preview, `catalog_digest`, existing-catalog digest and
   unresolved images. It does not import or enable anything.
4. After review and real owner SMS step-up, `POST .../apply` supplies the same
   rows/source plus `reviewed_digest`, `expected_catalog_digest` and an idempotency
   key. The transaction imports Enbishan drafts only; current-catalog changes,
   active-product overwrites, cross-brand IDs/assets and stale approvals fail.
5. Review the drafts and approve prices/options/images through the existing
   product administration path. Matrix price approval requires owner SMS and a
   commercial digest. Subsequent unapproved price changes return the item to
   draft. Import never enables brand services, branches, live payments or domains.

Branch locations, hours, delivery zones/fees, seller/VAT identity, original logo,
provider credentials and approved benefit terms remain external inputs. Existing
branch/brand/configuration APIs handle them; no Enbishan values are fabricated.
Rabwah and Muhammadiyah are the target branches; Qaisumah remains inactive.

## Integration and acceptance

This candidate reconciles Enbishan `88a332b4` with operational/security
`b72e92c9`, preserving both histories. Payment/CRM/optional-plate development
that had been excluded from the operational stream is retained here. The newer
cash/card branch workflow remains the default; online-only policy is explicit,
and all provider/live gates remain required and OFF. Vehicle-hour, printer,
owner-permission and alert-policy changes are included for shared regression.

Local available-source acceptance: 1132 passed, 15 skipped, 1032 deselected,
197 passing subtests. The new import/matrix source tests and JavaScript selection
contract passed. These counts are not substitutes for PostgreSQL/browser/image
execution. Mandatory native CI includes actual workbook draft import/SMS/replay
and revocation, AR/EN mobile/desktop matrix controls, shared KPS/Enbishan journeys,
the seven retained operational browser cases, exact images, scans, backup/restore
and capped performance. READY may be declared only after their final results.

No production changes, public Enbishan activation, deployment or DNS change are
authorized by this document. After software acceptance, only reviewed external
data/configuration, approved activation and a smoke test should remain.
