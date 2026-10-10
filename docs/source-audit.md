# KPS ORDER v4 source extraction

## Deployed-page evidence

Read-only GET of `https://kps-order-test.onrender.com/` on 2026-10-10 returned HTTP 200 and 7,245,235 bytes. Its SHA-256 is:

`2e5f31fc8ad2d0a3a46ae98dd50ae2c8018ab9963b9eb4d2ae60a49f722041d6`

That digest exactly matches `index.html` at the checked-out KPS-ORDER source commit `b9d4f523c18981032151f5ab5dad57bd23f946c5` (`agent/apex-v2-integration`). This proves page-content equality; deployment service/repository/branch metadata still requires a separate hosting check. No old `indexorder36.html`, `indexorder4.html`, or main-branch catalog was used.

The deployed script calls `https://kps-apex-os-v507-test.onrender.com/api/public/v2/catalog/delivery` and replaces embedded names, ids, prices, images, option groups and variants at runtime. Two read-only attempts to that catalog failed: proxy CONNECT timeout at 15 seconds, then request timeout at 45 seconds with zero response bytes. No order or other write was sent to APEX. The independent application must not call this endpoint at runtime.

## Catalog snapshot and limitations

`data/catalog.json` preserves all 35 embedded product cards and their exact `data-product-id`, names and prices. Original images and the approved logo were extracted byte-for-byte to `public/assets/`. No replacement logo or image was generated.

These are **deployed HTML source values**, not verified current runtime DB values. `metadata.runtime_verified`, product `source_verified`, and product `settings.source_verified` are false. The snapshot is suitable for a labeled preview; production checkout must remain blocked until catalog identity/prices/options are reconciled with a successful authoritative read or explicit administrative validation. An administrator should validate the independent persisted catalog through an audited operation instead of silently changing these source flags.

The HTML repeats three names in different placements with different source ids: cold brew (`kps-01`, `kps-08`), cold daily coffee (`kps-02`, `kps-05`), and chocolate cookies (`kps-03`, `kps-21`). Both thermal cups also share the same visible name while retaining different ids, prices and colors. None were merged by name or row order. A runtime catalog can have different ids or product counts; explicit reconciliation is required.

| Product | Embedded source id | Embedded price, halalas | Preserved restriction |
|---|---|---:|---|
| خلية بيكان | kps-33 | 1950 | حفر الباطن only |
| خلية كلاسيك | kps-34 | 1950 | حفر الباطن only |
| كوب حراري | kps-30 | 2100 | Per-unit color |
| كوب حراري | kps-31 | 1800 | Per-unit color |
| موهيتو | kps-11 | 900 | Per-unit flavor |

The two honeycomb products have the source attribute `data-available-governorates="حفرالباطن"`; `available_cities:["حفر الباطن"]` preserves that rule using the owner-approved spelling alias. This restriction applies to fulfillment-branch city for every order type; it is separate from delivery eligibility.

## Options provenance

The HTML reads `option_groups` and `variants` from the unavailable runtime catalog; it does not embed those values. Exact source definitions were retrieved read-only from KPS-APEX-OS:

- Commit: `22c66093bfbcca4b7539f4c31aa9099b8ee9d968`.
- File: `app/v1038_product_recipe_options_closure.py`.
- Blob SHA: `7109a534f7061f13dfbbbf0bba8ad4dccd58e67a`.

`MOJITO_FLAVOURS` and `THERMAL_CUP_COLOURS` plus their group-construction functions provide the exact machine codes, labels, required/per-unit selection behavior and zero price deltas below. This is source-code provenance, not proof of current runtime rows. Group/value numeric DB ids and live `variants`/hex colors were not recoverable and are not invented. `variants` is empty; the source option codes remain authoritative in the snapshot.

| Product code/source card | Group code | Exact value codes and labels |
|---|---|---|
| KPS-THERMAL-21 / kps-30 | COLOR | PNK وردي; BLK أسود; BLU أزرق; MAU موف |
| KPS-THERMAL-18 / kps-31 | COLOR | BLK أسود; BLU أزرق; MAU موف; OFW أوف وايت |
| موهيتو / kps-11 | FLAVOUR | BLACKBERRY توت أسود; BLUE_LAGOON توت أزرق; PASSION_FRUIT باشن فروت; POMEGRANATE رمان; RASPBERRY توت أحمر; STRAWBERRY فراولة |

Both groups are required, per-unit, with minimum and maximum selections 1. The thermal source group name is `لون الكوب الحراري`; mojito remains `نكهة الموهيتو`. Distinct per-unit selections must remain in order snapshots and cart identity.

## Original branch-number mapping

The twenty original numbers and names were retrieved from an explicit dictionary, **not inferred from row order, remembered names, database ids, Google Business suggestions, or fuzzy-name matching**:

- Repository: `KPS-COFFEE/KPS-APEX-OS`, read-only source lookup.
- Commit: `22c66093bfbcca4b7539f4c31aa9099b8ee9d968`.
- File: `app/v1011_canonical_branch_selectors.py`.
- Blob SHA: `536acb8d65fa909a034b8ba67348991fbb488370`.
- Symbol: `OFFICIAL_BRANCH_NAMES_BY_NO`.

| branch_no | Exact official source name | Owner-approved delivery-city eligibility |
|---:|---|---|
| 1 | حفر الباطن - المروج | Yes |
| 2 | حفر الباطن - الخالدية | Yes |
| 3 | حفر الباطن - النايفية | Yes |
| 4 | حفر الباطن - العزيزية | Yes |
| 5 | الذيبية | Yes |
| 6 | النعيرية - العزيزية | No |
| 7 | الدمام - أحد | No |
| 8 | الأرطاوية | No |
| 9 | حفر الباطن - المروج | Yes |
| 10 | القيصومة - الخالدية | Yes |
| 11 | الخفجي - العزيزية | Yes |
| 12 | حفر الباطن - الباطن | Yes |
| 13 | الزلفي - الفاخرية | No |
| 14 | الدمام - الجامعيين | No |
| 15 | المجمعة | No |
| 16 | عنيزة | No |
| 17 | حائل | No |
| 18 | شعبة نصاب | No |
| 19 | رفحاء | Yes |
| 20 | قبه | Yes |

The source `BRANCH_DISPLAY_OVERRIDES_BY_NO` identifies branch 9 separately as `حفر الباطن - المروج (كشك / درايف ثرو)`. Its official name remains unchanged; its descriptor is stored separately. Branches 1 and 9 remain separate records. The source module labels this dictionary a compatibility fallback for existing branches; persisted master identities take precedence in APEX itself. Current runtime DB identities were not read, so that limitation remains explicit.

Independent ids are `kps-branch-{branch_no}`. `source_id:"apex-canonical-branch-no:{branch_no}"` records the dictionary provenance; `source_db_id:null` explicitly states that these are not recovered APEX database ids. No foreign system is accessed by the application's import or runtime. City is taken from the explicit official name prefix before ` - `, with standalone official names used as cities. Current runtime city/ownership metadata remains unverified.

## Missing operational configuration

No verified operational branch coordinates, coverage polygons, delivery fees, accepting hours, pickup/car enablement, or opening cash float were present in the deployed order HTML or the recovered branch dictionary. The old order script contains city-level minimum values but provides no current per-branch verification; those values were not imported as launch policy. Its default Hafar Al Batin map center is not a branch location and was not used as one.

All twenty branches are retained. `delivery_eligible` derives only from the owner-approved six cities, while `pickup_enabled`, `car_enabled` and `delivery_enabled` are false pending operating-data setup. Coordinates, coverage, hours, delivery fee and minimum-order values are null. Eligibility alone must never authorize a delivery address or activate service.

Required launch data: each branch's actual pickup/car choices and accepting hours; verified branch pins; delivery coverage and fee/minimum policy for eligible branches; then exact current product identity, price, options/variants, offers and availability. These are operational blockers, not reasons to invent values or drop branches.

## Reproduction

`tools/extract_catalog.py` is an offline standard-library extractor. Supply the original verified source HTML explicitly:

```sh
python tools/extract_catalog.py --source /path/to/verified-kps-source.html
```

The tool extracts embedded assets and writes both versioned seed contracts. It refuses a runtime-catalog replacement without explicit identity/code reconciliation. Source-card ids and original branch numbers remain stable. The private owner handoff and email are not copied into these public data artifacts.
