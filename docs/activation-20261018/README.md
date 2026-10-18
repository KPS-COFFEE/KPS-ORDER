# Activation preparation checkpoint — NOT READY

Code candidate: `d443e686725c61e0534961dc4eff61fc832a46ca` on `agent/enbishan-isolated-development-20261017`.

This is an incomplete engineering checkpoint, not either requested READY TO ACTIVATE package. No production merge, deployment, DNS, payment activation, real customer/staff/order/invoice/shift mutation or Render configuration change occurred.

The preserved-history merge includes KPS main `6d9d556d` and security `6b073967`. CRM now has scoped search/segments/registration/branch/service/area filters, full order history and owner-SMS audited CSV export. Private backups include staff memberships and driver dedications; exact restore manifests include the optional migration. Shared owners retain their home-identity SMS policy/provider when selecting Enbishan.

107 focused source tests passed; 41 PostgreSQL cases were deselected locally and are NOT PASS. JavaScript parsing and Python compilation passed. PostgreSQL18/browser CI on the preceding merge candidate received no runner, executed zero steps and reported an account billing lock. Do not misclassify this as an application regression; equally, do not treat the unexecuted tests as successful. The newest code remains unverified on PG18/browser.

The separate security branch has a successful exact-SHA run; it is not a container certificate for the combined candidate. No expensive load repetition was dispatched. No new KPS regression or cross-brand leakage can be ruled out until the required executed acceptance passes.

`KPS.json` and `ENBISHAN.json` contain the requested checklist fields, exact source SHA, migration checksums, rollback requirements and explicit remaining engineering/external inputs. Percentages are null because full-scope completion has not been measured; arbitrary 90–99% estimates would be misleading.
