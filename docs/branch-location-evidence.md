# KPS ORDER — Google Maps branch evidence

The owner-supplied `KPS_Google_Business_Consolidation.xlsx`, sheet `03-Existing Matches`, supersedes the earlier public-search investigation. The workbook contains **20 existing Google Business Profiles** with **20 distinct Place IDs**, not missing links or new branches. Its SHA-256 is `c748eeb39586fe2e024005165c160a20e2017722ffa1b93db5b7c216b73f3e66`. The original workbook is not uploaded to this repository; `data/branch_google_matches.json` retains only public branch evidence.

Workbook labels were matched explicitly to the existing operational `branch_no` identities in `data/branches.json`. Excel row order and `KPS-00n` business codes were not used as branch numbers. In particular, workbook row 6 is the candidate for operational branch 9, while row 7 is operational branch 5.

**14 source-confirmed bindings are eligible for a one-time update of existing branch settings. Six ambiguous matches remain unpublished evidence.** The import never creates branches, enables ordering, invents coverage, changes fees or resets hours. Existing owner location settings, including explicit removals, take precedence. Subsequent restarts preserve all later owner edits.

Actual Google Maps directions for branch 1 resolved to KPS Coffee on Abu Bakr As Siddiq Road, Al Muruj, Hafar Al Batin 39913. The returned destination record, not the camera center, contained `!1s0x3fd739f66d9a80e1:0xb78fbc8e5f04f846!2m2!1d45.9836647!2d28.3986693`. This proves the destination pin `28.3986693,45.9836647` for its exact Place ID. All thirteen remaining source-confirmed bindings were also opened individually through the signed-out public Google Maps directions UI. Their returned destination records match the exact supplied Place IDs and approved branch city/identity. **14 destinations now have independently verified pins.** The workbook itself contains no coordinates; these are separate public destination observations, stored alongside the workbook match rather than attributed to Excel. Origin coordinates, viewport coordinates and browser session URLs are excluded. The six ambiguous identities were not promoted. No route distance or ETA is claimed.

## Operational mapping

| Branch | Existing identity | Workbook row | Google Place ID | Import status |
| --- | --- | --- | --- | --- |
| 1 | حفر الباطن - المروج | 2 | `ChIJ4YCabfY51z8RRvgEX468j7c` | Source-confirmed; exact Google destination verified |
| 2 | حفر الباطن - الخالدية | 3 | `ChIJE0dZrh5B1z8RpGSHUypO04s` | Source-confirmed; exact Google destination verified |
| 3 | حفر الباطن - النايفية | 4 | `ChIJTTqSsnM_1z8RLcbfIQNNGEQ` | Source-confirmed; exact Google destination verified |
| 4 | حفر الباطن - العزيزية | 5 | `ChIJaYGp-6tB1z8Rb8bBNgUZjfI` | Requires owner confirmation; no automatic public link |
| 5 | الذيبية | 7 | `ChIJt40TGPIH1z8R3adbLg_CRaU` | Source-confirmed; exact Google destination verified |
| 6 | النعيرية - العزيزية | 8 | `ChIJXeEbPYu7zD8Rx5b5LNB9Q7s` | Source-confirmed; exact Google destination verified |
| 7 | الدمام - أحد | 9 | `ChIJRYLq1WT9ST4RLaB_Fa1OlBE` | Source-confirmed; exact Google destination verified |
| 8 | الأرطاوية | 10 | `ChIJewUEvecT1T8RhfkIudi8hZg` | Source-confirmed; exact Google destination verified |
| 9 | حفر الباطن - المروج - درايف ثرو | 6 | `ChIJZdnl4lI51z8RDyi016qRLCA` | Requires owner confirmation; no automatic public link |
| 10 | القيصومة - الخالدية | 11 | `ChIJ_YqVSfMv1z8RiMv2Io4YDgg` | Source-confirmed; exact Google destination verified |
| 11 | الخفجي - العزيزية | 12 | `ChIJwSVdlTkdzD8R3abKRHhv0_0` | Requires owner confirmation; no automatic public link |
| 12 | حفر الباطن - الباطن | 13 | `ChIJOcElampH1z8RTzgR8HgAO2E` | Requires owner confirmation; no automatic public link |
| 13 | الزلفي - الفاخرية | 14 | `ChIJ6Y_gAH3DfxURmBPLD-wOWhs` | Requires owner confirmation; no automatic public link |
| 14 | الدمام - الجامعيين | 15 | `ChIJkZIme8L7ST4RExsODhjY9f0` | Requires owner confirmation; no automatic public link |
| 15 | المجمعة | 16 | `ChIJ_zX9D1DHKj4RvcG-zzHUzzQ` | Source-confirmed; exact Google destination verified |
| 16 | عنيزة | 17 | `ChIJVxEa7p_tgRUR7WzWZroZnlA` | Source-confirmed; exact Google destination verified |
| 17 | حائل | 18 | `ChIJQXOnj4ZFdhURTjeuegRGvOo` | Source-confirmed; exact Google destination verified |
| 18 | شعبة نصاب | 19 | `ChIJz4xSleONYhUR2k3VuwcFagc` | Source-confirmed; exact Google destination verified |
| 19 | رفحاء | 20 | `ChIJ2c3SY1ZnZBURSWdXIaLYMxw` | Source-confirmed; exact Google destination verified |
| 20 | قبه | 21 | `ChIJF2e1GDkrfBUROBGkEmfkl-M` | Source-confirmed; exact Google destination verified |

## Six specific unresolved matches

| Branch | Approved identity | Source disagreement or missing proof |
| --- | --- | --- |
| 4 | العزيزية | أكد المطابقة أولًا. نتيجة Google الحالية تعرض الحي «الشمالي»، بينما مصادر أخرى تربط موقع الستين بالعزيزية. |
| 9 | المروج 9 - كشك/DT | أكد المطابقة أولًا. مطابق للمروج/طريق الأمير نايف؛ أكد أنه فرع المروج 9 (الكشك/DT). |
| 11 | الخفجي - العزيزية | أكد المطابقة أولًا. Google يعرض «الستين»؛ أكد أنه نفس فرع الخفجي المسمى داخليًا العزيزية. |
| 12 | الباطن | أكد المطابقة أولًا. مطابق لفرع KPS في نطاق الباطن بحسب نتائج الخرائط/التوصيل؛ راجع الاسم الداخلي. |
| 13 | الزلفي - الفاخرية | أكد المطابقة أولًا. Google يعرض حي الصديق بينما الاسم الداخلي عندنا الفاخرية. |
| 14 | الدمام - الجامعيين | أكد المطابقة أولًا. أقرب مطابقة KPS الحالية؛ Google يعرض النزهة وليس الجامعيين. أكد قبل نقل/تعديل الملف. |

Branch 1 and branch 9 have different Place IDs and different source addresses: Abu Bakr As Siddiq Road versus Prince Nayef Road. The workbook explicitly requires confirmation that the latter is the branch 9 kiosk/drive-through. Branch 1’s point is never copied to branch 9.

## Executable verification

`tests/test_branch_location_import.py` checks the exact twenty identity/Place-ID mappings, fourteen confirmed/six uncertain statuses, source checksum guards, URL/Place-ID agreement, preservation of owner changes, duplicate-binding prevention, directions destination arguments and idempotence. The PostgreSQL tests run in isolated UUID schemas. Source-only tests do not prove the production database import or browser destination resolution.

Public directions use `/maps/dir/?api=1&destination=...&destination_place_id=...&travelmode=driving`, with the exact confirmed Place ID. No generic Google homepage, review URL, viewport coordinate or free-text-only search is substituted. Manual city/branch selection remains available, and customer geolocation is optional. The existing nearest-branch engine requires verified coordinates plus eligible service, hours, coverage, stock and selected options; coordinates alone never activate ordering.
