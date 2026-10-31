# Independent KPS / Enbishan training runtime

Original application baseline: `e67c61643717be8ca8744409f0214bc35b6d0a0a`.

All `app/`, `public/`, `migrations/`, dependency lock and business rules remain
byte-identical to that release. Only this directory is added. The operating
Dockerfile and protected branch do not import the training entrypoint.

The separate Dockerfile runs the real Python application with local PostgreSQL.
Each browser training room receives a separate disposable database copied from
a synthetic template. Switching roles with `resume=1` stays in the same room.
An expired room cannot fall back to the template or another learner's database.
The public menus/assets were read from each storefront. Staff, customers,
orders, shifts, contact numbers and cash are synthetic. General manager and
evening manager capability sets match the live UI inspection (39 / 35).

This is not certification that every production setting has been exported.
Private delivery polygons, exact branch fees/minimums, fiscal profiles and all
unobserved account settings are not available in the public catalog snapshot.
The fixture uses explicitly synthetic configuration for those values. Morning
manager policy is the original program's policy with a synthetic account.

External SMS transport is simulated with code `0123`. Email, payments, maps
providers and printer connections are disabled. No real provider credentials or
production database URL can be inherited by this entrypoint. Existing account
authorization remains inside the original application; role selection is only
for disposable synthetic accounts in this independent training host.

Runtime flag: `KPS_TRAINING_ONLY=true`. No `DATABASE_URL` supplied: the isolated
entrypoint creates loopback storage itself. No production domain, volume or
network database is connected. Training state is temporary and is lost on a
service restart. Rooms expire after inactivity. This is a training resource,
not a production-capacity or physical-device acceptance certificate.

Local verification: real PostgreSQL migrations and original HTTP endpoints;
all nine role sessions, both brand projections, API authorization, room
separation, and actual partial collection with an accounting difference.
Browser QA and live deployment checks are recorded separately.
