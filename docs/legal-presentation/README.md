# Customer legal presentation — 2026-10-15

Baseline: `c37d2d84764391b63188f1ef6140013f5c9859a5` on `agent/kps-order-emergency-v4`, PR #1.
The baseline archive was captured before editing. `baseline.json` records the exact SHA256 of each archived file. No customer/operational database backup or secret is included.

Scope: compact 11–12px legal navigation, local 18px social SVGs with 38px touch targets, public contact information with existing authenticated support, and paired AR/EN privacy, terms and cancellation/refund sections. KPS-only policy renderer; other brand policy behavior retained. No schema, identity, permission, invoice, ordering configuration or deployment changes.

Full rendered policy text: `policies-ar.txt`, `policies-en.txt`.
Business values: CR 7037181620. Stored VAT remains 311887526600003 in the existing invoice configuration. VAT certificate matching, approved processing bases, retention schedule and transfer safeguards remain prelaunch requirements, not certified by this UI change.

## Verification

Local commands:

```sh
python -m pytest -q tests/test_legal_presentation.py -m 'not postgres' tests/test_customer_language.py::test_customer_translation_codes_are_complete_and_keep_approved_source_immutable
node --check public/customer.js
git diff --check
```

Local result: 9 PASS; 2 PostgreSQL cases intentionally excluded from this local command. PostgreSQL/browser evidence is required separately; local runtime cannot switch OS user to initialize PostgreSQL, and Chromium downloads returned invalid archives.

Full isolated CI command (no production credentials):

```sh
TEST_BROWSER=1 TEST_DATABASE_URL=postgresql://USER:PASSWORD@127.0.0.1:5432/test_kps_order python -m pytest -q tests/test_legal_presentation.py tests/test_legal_presentation_browser.py tests/test_customer_experience.py tests/test_customer_experience_browser.py tests/test_customer_final_account.py tests/test_customer_final_account_browser.py
```

Existing full-regression workflow collects the added tests without weakening any gate. Browser evidence paths: `test-artifacts/legal-{before,after,footer,contact,privacy,terms,refunds}-{ar,en}-{390,1280}.png`. Before images replay only exact archived static assets against the same synthetic backend; APIs are never intercepted. These are browser-emulation screenshots, not physical-device certification.

## Exact rollback

Prefer `git revert <presentation-commit>` (and any explicitly listed follow-up presentation commits in reverse order) on the current branch. This preserves unrelated later work. Resolve any overlapping later edits consciously; never reset the whole branch.

For an exact presentation restoration after reviewing the current working tree:

```sh
git restore --source=c37d2d84764391b63188f1ef6140013f5c9859a5 -- app/customer_legal.py public/index.html public/customer.js public/customer.css
```

The immutable baseline archive includes the unchanged style.css as well. If the baseline commit is unavailable, extract only those four named files from `baseline-c37d2d8.tar` and compare SHA256 with `baseline.json`. The added content module and social icons are then unused. Restore the three adapted browser-test entry paths along with the presentation, or revert the presentation commit as recommended. Do not restore operational settings/data, deploy, merge or change DNS as part of rollback.

## SVG provenance

Local unchanged Simple Icons glyphs, retrieved 2026-10-15 from `simple-icons/simple-icons` (CC0 project; brand marks remain their owners’ marks):
- whatsapp.svg blob `2a6c69fdfcbd75a9ee458964de48eb0cefe600c1`
- instagram.svg blob `c0e86b0bfc7b902c485699953dee878f290f575b`
- snapchat.svg blob `3110fbadace6d93bbe5da3796499178ad60134fb`
- tiktok.svg blob `57ce3ae5d6e099d2681dccfb5050af2dabf5c353`

Policy review references (no compliance certification): SDAIA Personal Data Protection Law / rights guidance and Ministry of Commerce consumer guidance. Owner-supplied controller, CR and contacts are presented as supplied; no address/email invented.
