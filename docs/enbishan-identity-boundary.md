# Customer identity boundary and future TILQA linking

The current account key is the opaque customer user ID plus its brand ID. A
phone number is a sign-in attribute within that brand, not a global identity.
The same verified phone in KPS and Enbishan must retain two customer accounts.
Explicit employee memberships are separate from customer identity and never
authorize customer account switching.

The existing `customer_identity_links` table is exclusively a same-brand,
verified guest-recovery mechanism. Migration `002_customer_brand_ownership.sql`
enforces both endpoint brands at the database level, in addition to application
authorization. It is not suitable for cross-brand TILQA linking.

Future TILQA linking must use a separate association, without rewriting any
current customer IDs. Its acceptance contract is:

1. Obtain fresh, brand-specific verification of each participating account.
   Matching phone values, an employee session, or a proof for only one account
   are insufficient.
2. Show the named accounts and obtain explicit, versioned customer consent for
   the association. Store each proof reference, consent version, timestamp,
   actor and revocation event without storing OTP codes.
3. Bind each association to `(brand_id, customer_id)` and a separate opaque
   TILQA subject. A TILQA login chooses the intended brand and establishes its
   own scoped session. It never adds cross-brand IDs to `customer_alias_ids`.
4. Keep orders, saved addresses, invoices, wallets, loyalty eligibility and
   marketing consent under their original brand and account. Linking alone
   grants no data transfer, balance transfer, combined history, or messaging
   consent. Any such future feature needs separate authorization and tests.
5. Revoking an association prevents future linked sign-ins without deleting
   either brand account or altering orders, financial records or local login.

No cross-brand linking API, association write, account migration or TILQA
activation is enabled by this preparation. Future implementation must retain
the database ownership witnesses in
`tests/test_enbishan_customer_scope_constraints.py` and demonstrate rejection
of stale/replayed proof, missing consent, foreign account IDs and revoked
links before a separately approved release.
