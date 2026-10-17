# KPS WhatsApp link preview — review candidate

Original approved preview restored from saved commit `728868db1884f8f99c94f03a944b394a500b1f9e` onto current branch HEAD `f8172242608444a0ee5401e5c37d51276b8e36d1`, without replacing newer files or reverting other streams.

- Title: KPS COFFEE | كبز للقهوة المختصة
- Description: قهوتك صارت أقرب… اطلبها بكل سهولة ☕🧡
- Share URL: https://order.kps.sa
- Selected image: `/assets/kps-whatsapp-cf8b1309a87e.jpeg` (original croissant, unchanged)
- Review alternative: `/assets/kps-whatsapp-cookie-815deea7ce4d.png` (existing cookie design, unchanged; not selected in metadata)

SHA256:
- Croissant: `cf8b1309a87eea6fe219d26ea2dce98c25c017c714762af933241504a45e6384`
- Cookie: `815deea7ce4d56c203874f2d666086c776e2e6734290c4d7a472e333b263e1ae`

The two assets are alternatives, not simultaneous preview images. No generation, recompression, cropping or storefront advertisement was added. Metadata is in the server response head. Existing server brand filtering removes the marked KPS block for other-brand requests, and the existing KPS deployment scope rejects conflicting brand requests. No order/payment/customer code or data changed.

## Actual verification

PASS: 38 focused tests (`tests/test_whatsapp_preview.py` and `tests/test_storefront_scope.py -m 'not postgres'`). Includes raw server HTML, exact texts, single selected preview, public unauthenticated asset responses, JPEG/PNG Content-Type, file integrity/dimensions and brand isolation. One database test was deliberately deselected; no operational database was used.

PASS: storefront HTML body byte-for-byte equal to base HEAD; `git diff --check`.

NOT TESTED: new assets through production HTTPS, real WhatsApp manual sharing, automatic greeting on mobile WhatsApp Business, physical device display. Local HTTP response verification does not certify external crawler behavior. No guarantee that automatic greetings display link previews.

## Deployment boundary

No production deployment authorized or performed. Render service `srv-db4s1v942hec73epmar0` was read-only checked: `autoDeploy=no`, `autoDeployTrigger=off`. Recheck before updating the remote branch. PR #1 stays draft/unmerged.

After explicit owner approval: deploy the reviewed candidate, verify public HTTPS HTML and image responses/hashes, then have the owner manually share https://order.kps.sa and test an actual automatic greeting from WhatsApp Business on the phone. Record these separately as PASS/FAIL with observed evidence. If WhatsApp caches an older preview, report the observation without changing the approved image or promising automatic greeting behavior.
