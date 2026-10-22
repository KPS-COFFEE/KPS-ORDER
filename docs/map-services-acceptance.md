# KPS map services — acceptance scope

This change belongs to KPS-ORDER. It does not alter Tilqa or require a new APK to collect server-side location receipt evidence.

| Service | Implementation | Limits |
| --- | --- | --- |
| Eligible nearby driver | Assignment dialog recommendation; canonical shift, parent-manager, branch, capacity and zone checks | Manager confirms. Uses current GPS with accuracy <=100m and direct distance to pickup; not road-distance ranking |
| Delivery ordering | Map destination selector offers a 2–8 stop plan for visible picked-up orders | Nearest-neighbour straight-line suggestion; no task writes. Other authorized work may exist outside the chosen scope |
| Road ETA | Existing Directions path extended to all planned stops; public tracking offers an explicit ETA calculation | Estimated driving duration only; no handoff/stop duration, no guarantee or automatic assignment. Existing monthly/per-driver budget applies |
| Customer tracking | Explicit create/copy/revoke controls on order map popup; token in fragment and POST body; only token hash stored | Four-hour expiry, replacement revokes previous link, completion hides GPS. No GPS while driver has other active tasks; no customer/staff identity or phone in view response |
| Zones and fees | Scoped approved coverage overlays and existing branch fee display | No unapproved fee/radius changes or per-kilometre pricing invented |
| Alerts | Missing/stale/approximate location while delivering; current proximity observation within 150m including accuracy uncertainty | Proximity is not arrival/delivery proof. Existing branch geofence and confirmation workflow remain authoritative. Outage causes are unknown |
| Trip reports | Per-shift pickup-to-delivery times and branch-to-customer direct distance | Direct distance is not actual kilometres driven. Existing driver-hours reports retain branch departure/return evidence |

Location health stores minute counts, accuracy and maximum gap between accepted GPS samples. It stores no coordinate history and does not reconstruct previous shifts. No historical GPS claim is made for Ibrahim's first pilot. Existing GPS publication requires the authenticated driver's own open shift; samples stop being accepted after shift closure.

Dedicated PostgreSQL 18.6 and Chromium acceptance workflow: `.github/workflows/map-services.yml`. No real provider requests or messages are made by tests. Physical all-shift Android reliability, offline recovery and Play Protect distribution approval remain separate acceptance items.
