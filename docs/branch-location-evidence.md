# KPS ORDER — branch location evidence

Read-only investigation on 2026-10-10. This document does not activate services or change any branch, customer, order, driver, or settlement record.

The approved identities come from `data/branches.json`; branch 9 uses the owner's latest approved display name, **حفرالباطن - المروج - درايف ثرو**. Branches 1 and 9 remain different identities.

## Result

All twenty branch identities were searched using Arabic and English city/district variants. Public listings corroborate several business addresses, but **no exact Google Maps point / Google Place ID was independently confirmed and matched to an official branch number during this investigation**. The current project seeds contain no coordinates or branch-specific Maps links. Read-only GitHub code searches for `maps`, `latitude`, and `coordinates` in KPS-MENU / KPS-APEX-OS returned no matching source records; those default-branch search results do not prove other branches or private operational exports lack location data.

Google Maps pages could not be read through the available web lookup, and Google Maps buttons in Top-Rated.Online redirect through an inaccessible endpoint. Public listing addresses below are **research leads**, not approved production coordinates. No location candidates JSON was created because the criterion for a fully verified import was not met.

## Branch-by-branch findings

| No. | Approved branch identity | Evidence found | Missing input for exact Maps binding |
| --- | --- | --- | --- |
| 1 | حفر الباطن - المروج | KPS listing on Abu Bakr As Siddiq Road, and a separate Yango Al Muruj listing at CNAC3968. Neither supplies official number 1. [S1, S2] | Branch 1's actual Google Maps share link; do not reuse a generic المروج result. |
| 2 | حفر الباطن - الخالدية | Yango identifies KPS Coffee, Al Khalidiyah, King Abdulaziz Road, CNMG7570, matching the approved city/district. No exact Google point available. [S3] | Actual Google Maps share link for branch 2. |
| 3 | حفر الباطن - النايفية | Arabic النايفية / النايفيه and English Nayfiyah searches did not locate an unambiguous KPS branch point. King Abdulaziz Road alone is insufficient to assign a number. | Actual Google Maps share link for branch 3. |
| 4 | حفر الباطن - العزيزية | Snapchat identifies KPS Coffee in Al Aziziyah on الستين; Restaurant Guru corroborates a KPS listing on الستين with the brand contact number. [S4, S5] | Actual Google Maps share link for branch 4. |
| 5 | الذيبية | Public directory identifies KPS Coffee in Ath Thybiyah, but no exact point could be read. [S6] | Actual Google Maps share link for branch 5. |
| 6 | النعيرية - العزيزية | KPS listing in Nuayriyah / Nairyah is present; the available directory text does not prove العزيزية or provide a readable exact Google point. [S7, S8] | Actual Google Maps share link for branch 6. |
| 7 | الدمام - أحد | Yandex identifies KPS Coffee at Uhud, Omar Ibn Al Khattab, EDPC7048. Its building page exposes a building coordinate, not a verified KPS Google pin. [S9, S10] | Actual Google Maps share link for branch 7. |
| 8 | الأرطاوية | Directory names KPS Coffee on طريق عبدالعزيز آل سعود, Al Artawiyah 15738. Google Maps redirect could not be resolved. [S11] | Actual Google Maps share link for branch 8. |
| 9 | حفرالباطن - المروج - درايف ثرو | The Al Muruj sources lack branch number and drive-through identity. Assigning either المروج result to branch 9 would risk swapping branches 1 and 9. [S1, S2] | Branch 9's distinct actual Google Maps share link. |
| 10 | القيصومة - الخالدية | Directory identifies KPS Coffee on King Abdulaziz Road, Al Qaisumah 39551. District and exact Google point remain unconfirmed. [S12] | Actual Google Maps share link for branch 10. |
| 11 | الخفجي - العزيزية | KPS Coffee listing on King Abdullah Road in Khafji; no proof of the approved district or readable exact pin. [S13] | Actual Google Maps share link for branch 11. |
| 12 | حفر الباطن - الباطن | Snapchat names KPS Coffee in Al Batin, on طريق الملك خالد / طريق الكويت. No Google point available. [S14] | Actual Google Maps share link for branch 12. |
| 13 | الزلفي - الفاخرية | A KPS listing in Zulfi is on King Abdullah Road. The available public district evidence is incomplete or inconsistent with الفاخرية; no renaming or reassignment was made. [S15] | Actual Google Maps share link for branch 13. |
| 14 | الدمام - الجامعيين | A second KPS listing is on Al Imam Ali Ibn Abi Talib Street. It does not explicitly prove الجامعيين or branch 14. [S16] | Actual Google Maps share link for branch 14. |
| 15 | المجمعة | Snapchat names KPS Coffee in Al Majmaah on King Fahd Road, with the brand contact number. [S17] | Actual Google Maps share link for branch 15. |
| 16 | عنيزة | WorldCafeMap identifies KPS Coffee at Zamil Abdullah Al Suleim, Al Fakhriyah, Unaizah 56241. No Google point could be read. [S18] | Actual Google Maps share link for branch 16. |
| 17 | حائل | Search finds the KPS merchant in delivery listings; delivery-region labels are not the merchant's physical location and were not used as an address. | Actual Google Maps share link for branch 17. |
| 18 | شعبة نصاب | Arabic شعبة / شعبه and English Shubah searches did not identify an unambiguous KPS point. A returned كوفي مسرى listing was rejected as another business. | Actual Google Maps share link for branch 18. |
| 19 | رفحاء | KPS merchant exists in delivery listings, including a result labeled الجميماء / رفحة. This is not proof of the branch's physical pin. | Actual Google Maps share link for branch 19. |
| 20 | قبه | Arabic قبه / قبة and English Qibah variants did not identify an unambiguous KPS point. Unrelated Qibah shops and other coffee brands were rejected. | Actual Google Maps share link for branch 20. |

## Rejected substitutions

- The KPS site at `kps.com.sa` is Al-Khonaini petrol stations, a different business. Its numbered stations must never be mapped to these KPS Coffee branches.
- Customer delivery regions on HungerStation are service areas, not reliable merchant coordinates. No service-area coordinate or delivery ETA was imported.
- The Yandex EDPC7048 building coordinate is approximately `26.407060, 50.059302`; the page lists several tenants, including KPS. It is retained here solely as a research lead and **must not be presented as a verified KPS Google Maps pin**.
- A single Al Muruj result cannot populate both branches 1 and 9, and neither result proves drive-through identity.
- Directory operating hours, including purported 24-hour opening, were not adopted. The owner's actual opening schedule, services, coverage, fees and product availability remain the operational authority.
- No city center, guessed coordinate, review link, or unsupported route duration was used.

## Evidence sources

- S1 — [KPS Coffee, Abu Bakr As Siddiq Road, Hafar Al Batin](https://ar.restaurantguru.com/kbz-kafyh-KPS-COFFEE-Hafar-Al-Batin-3).
- S2 — [KPS Coffee, Al Muruj, CNAC3968](https://maps.yango.com/ar-sa/org/4067885198/).
- S3 — [KPS Coffee, Al Khalidiyah, CNMG7570](https://maps.yango.com/en-sa/org/kps_coffee/220542161030/).
- S4 — [KPS Coffee, Al Aziziyah, Snapchat place](https://www.snapchat.com/place/kpscoffee/5aa35c91-899e-4003-9ce6-ec00f95bd8e6).
- S5 — [KPS Coffee, الستين, Hafar Al Batin](https://ar.restaurantguru.com/kbz-kafyh-KPS-COFFEE-Hafar-Al-Batin-5).
- S6 — [KPS Coffee, Ath Thybiyah](https://kps-coffee.goto-where.com/).
- S7 — [KPS Coffee, Nuayriyah listing](https://www.top-rated.online/cities/Nuayriyah/place/p/19053643/).
- S8 — [KPS Coffee, Nairyah](https://ar.restaurantguru.com/kbz-kafyh-KPS-COFFEE-Nairyah).
- S9 — [KPS Coffee, Uhud, EDPC7048](https://yandex.com/maps/org/kps_coffee/111684383774/).
- S10 — [EDPC7048 building and tenants](https://yandex.com/maps/40419/dammam/house/YUkYdgJpSkUFQFxsfXhxdnxlZA==/).
- S11 — [KPS Coffee, Al Artawiyah](https://www.top-rated.online/cities/Al%2BArtawiyah/place/p/17107514/).
- S12 — [KPS Coffee, Al Qaisumah](https://saudicoffeecrafters.com/listing/kps-coffee-2/).
- S13 — [KPS Coffee, Khafji, King Abdullah Road](https://ar.restaurantguru.com/kbz-kafyh-KPS-COFFEE-Khafji).
- S14 — [KPS Coffee, Al Batin, Snapchat place](https://www.snapchat.com/place/-kps-coffee/8c1a0b97-73ba-423b-b1c0-7cb6551569fd).
- S15 — [KPS Coffee, Zulfi, King Abdullah Road](https://ar.restaurantguru.com/kbz-kafyh-KPS-COFFEE-Az-Zulfi).
- S16 — [KPS Coffee, Dammam, Al Imam Ali Ibn Abi Talib Street](https://ar.restaurantguru.com/kbz-kafyh-KPS-COFFEE-Dammam-2).
- S17 — [KPS Coffee, Al Majmaah, King Fahd Road](https://www.snapchat.com/place/-kps-coffee/deab6fad-325c-45a4-aae7-e77744f4820e).
- S18 — [KPS Coffee, Unaizah, Al Fakhriyah](https://worldcafemap.com/en/saudi-arabia/unaizah/kps-coffee).

## Operational handoff

Request only the exact missing Google Maps links, identified by the approved number/name above. A numbered owner export can satisfy the same input. Each link must resolve to the KPS business itself, and branches 1 and 9 must resolve to distinct verified records. Store the exact verified point, stable Google place identifier or trusted share link, source and verification timestamp through the existing branch administration workflow. Preserve service switches; location verification alone does not authorize activating a branch or opening live orders.
