# Final owner menu review — 22 October 2026

This additive review applies the exact decisions previously shown in the customer Site's `owner-review.js`. It prepares disabled drafts only. It performs no database connection, import, activation or startup write.

Use `owner-final-20261022.json` with the existing `/api/admin/enbishan-menu/preview` endpoint. Its `owner_review` selection is explicit. The importer requires the original workbook SHA256 and exact 95-row source digest before applying this review. A changed source fails closed. The owner must review the resulting digest and current-catalog digest before the existing draft-only import endpoint accepts it. Active products cannot be overwritten through this importer.

The source remains 31 retained cards; the review contains 28 visible candidates. Pepsi Diet, 7UP Diet and Mirinda orange retain their IDs, prices and original source rows, with `purchasable=false`. No product is deleted. Every imported card remains disabled, draft and source-unverified until the separate commercial and release controls are satisfied.

The mixed grape leaves and cabbage card offers the exact original option IDs and prices for:

- 6 pieces: 4 grape leaves + 2 cabbage.
- 12 pieces: 8 grape leaves + 4 cabbage.
- 24 pieces: 16 grape leaves + 8 cabbage.

The previous 36-piece size and all four of its flavour prices remain in `owner_review_original_variants`, outside the current orderable matrix. The original 20 October reference manifest remains byte-for-byte unchanged. No price, product ID, option ID or flavour choice is invented.

Five existing approved JPEGs are copied byte-for-byte into `/assets/enbishan-menu/owner-review/`. Red, yellow and blue cups map to grape leaves, mahashi and fattah respectively. The Ramadan box image maps to its approved product. The crispy tray replaces only the small five-piece variant image; other crispy sizes retain their original variant images. Exact hashes and sizes are in `owner-images-20261022.json` and the normal bundled-asset hash allowlist.

Offline workbook reproduction: `python tools/enbishan_workbook.py SOURCE.xlsx --reference-menu --owner-approved-20261022`. The complete approved source payload and resulting catalogue digest are preserved beside this report.

Validation: `tests/test_enbishan_final_menu_review.py` checks all 31 IDs, exactly three hidden products, all 28 visible candidates, retained source prices, exact three mixed sizes, rejected retired-size ordering, five image hashes, small-only crispy replacement, draft-only output and rejected altered source/brand/artwork.

## Production migration blocker

The integrated historical ENBISHAN branch also carries **nine normal startup migrations** absent from observed live revision `f8f3840`: `064_branch_checkout_sessions.sql`, `065_branch_payment_webhooks.sql`, `066_branch_online_refunds.sql`, `067_branch_payment_reconciliation.sql`, `068_branch_payment_renewal.sql`, `069_branch_refund_reconciliation.sql`, `070_branch_payment_modes.sql`, `071_late_branch_payment_receipts.sql` and `079_customer_engagement.sql`.

`app/db.py:apply_migrations` currently discovers normal migration files without a feature gate. Feature activation being OFF does **not** prevent those nine schema changes at startup. The two separate multibrand migrations are gated by `multibrand.enabled()` and the database isolation guard. No existing applied migration was edited in this review, and none was executed against production.

Production deployment must remain blocked until the nine additive migrations, preservation/rollback compatibility and backup have been reviewed and approved, or a separately tested exact-release startup guard prevents unapproved pending migrations. This menu review is pure preparation and does not supply migration or production approval.
