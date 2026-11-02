# Customer, driver accounting and evidence clarity

Scope approved by the owner on 1 November 2026: implement, test, merge through protection, and deploy.

- General customer help, suggestions and complaints can start without an order or branch, through a revocable customer/guest session. Branchless messages route to customer care; existing identity, rate, attachment and inbox authorization remain enforced.
- Driver statements select multiple drivers and filter approved shortage days, shortage under review, or outstanding receivables. Screen, PDF and Excel use the same server-filtered rows. Surplus, pending receipts and outstanding amounts remain separate. Saved closures and their amounts are not changed.
- An independent branch catalog includes drafts, paused and otherwise unavailable items, with search, category and status filters. Accounting report access is read only; changing stock requires the existing independent stock/menu permission. Stock never overrides city, service, publication or permanent branch restrictions.
- Customer requests and decisions remain append-only. Their original author, decision author, times and reasons appear in the change/cancellation history. Authorized order evidence includes customer conversation attachments.
- Normal delivery photo retention remains unchanged. Orders with amendments, cancellation/rejection, incident tickets, complaint conversations or recorded problems require a formal owner/delivery-manager retention decision before photo expiry. Closing a ticket or merely reading a photo does not release this hold. Release records the exact existing photo IDs; later photos remain held. New documented issues invalidate an earlier release. Chat attachments are not removed by this cleanup.
- Normal session revalidation is labelled as verification, separately from failed-connection recovery. Live revocation and all fail-closed masking remain enforced.

No production orders, balances, staff grants, message routing, subscriptions, payment gateways or coverage configuration are changed by the source update. Manager accounting-report access is an explicit, separately revocable owner grant; it does not grant accounting review or mutation powers.

Validation is recorded against the PR head by the existing complete CI gates, PostgreSQL acceptance, and real HTTP browser tests at mobile and desktop widths. New acceptance tests are included in the accounting CI workflow. Production financial reconciliation and a physical iPhone test are separate from this source/Chromium acceptance.
