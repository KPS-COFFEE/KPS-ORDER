"""Prepare owner-approved Enbishan branch settings without touching a database.

Input is a reviewed KPS branch snapshot, never a connection string. Output can be
reviewed and supplied to the existing brand-scoped owner branch PATCH endpoint.
This is intentionally not called at application startup or by migrations.
"""
from copy import deepcopy

COPY_FIELDS = ('delivery_fee_halalas', 'minimum_order_halalas', 'opening_hours', 'delivery_hours')
APPROVED_BRANCH_NUMBERS = frozenset({1, 2, 3, 4})


def _validate_snapshot(settings):
    for field in COPY_FIELDS:
        if field not in settings or settings[field] is None:
            raise ValueError(f'KPS source is missing {field}')
    for field in COPY_FIELDS[:2]:
        if type(settings[field]) is not int or not 0 <= settings[field] <= 100_000_000:
            raise ValueError(f'Invalid KPS {field}')
    # Use the same validation as owner edits, including malformed day/time rows.
    from .admin import hours
    for field in COPY_FIELDS[2:]:
        hours(settings[field])


def prepare_branch_patches(kps_branches, enbishan_branches, *, source_by_branch=None):
    """Return independent, default-OFF owner API patches for existing branches.

    Where KPS branches differ, require an explicit source branch ID per target.
    Never guess a fee by geographic proximity. Where all KPS snapshots agree,
    the common values can initialize any approved branch. Existing Enbishan
    values always win, so rerunning preparation cannot undo owner changes.
    No customer, payment seller, coverage polygon, staff or financial data is
    copied. Coverage belongs to the destination branch and needs own review.
    """
    source_by_branch = source_by_branch or {}
    sources = {}
    for row in kps_branches:
        if row.get('brand_id') != 'kps' or not row.get('id') or row['id'] in sources:
            raise ValueError('Expected unique KPS source branches')
        _validate_snapshot(row.get('settings', {}))
        sources[row['id']] = {key: deepcopy(row['settings'][key]) for key in COPY_FIELDS}
    if not sources:
        raise ValueError('A reviewed KPS branch snapshot is required')
    common = next(iter(sources.values()))
    common = common if all(value == common for value in sources.values()) else None
    targets, numbers, patches = set(), set(), []
    for row in enbishan_branches:
        number, identifier = row.get('branch_no'), row.get('id')
        if (row.get('brand_id') != 'enbishan' or type(number) is not int
                or number not in APPROVED_BRANCH_NUMBERS or not identifier
                or identifier in targets or number in numbers):
            raise ValueError('Expected unique owner-approved Enbishan branches 1–4')
        targets.add(identifier); numbers.add(number)
        current = row.get('settings', {})
        # Preparation must never disable a live branch or change live behavior.
        if any(current.get(key) is True for key in ('delivery_enabled', 'pickup_enabled', 'car_enabled')):
            raise ValueError('Preparation requires an inactive destination branch')
        source_id = source_by_branch.get(identifier)
        if source_id is not None and source_id not in sources:
            raise ValueError('Selected source is not a supplied KPS branch')
        snapshot = sources[source_id] if source_id else common
        if snapshot is None:
            raise ValueError(f'KPS settings differ; select source for {identifier}')
        changes = {key: deepcopy(value) for key, value in snapshot.items()
                   if current.get(key) is None}
        changes.update(pickup_enabled=False, car_enabled=False, delivery_enabled=False)
        patches.append({'id': identifier, 'brand_id': 'enbishan',
                        'source_branch_id': source_id, 'source_mode': 'explicit' if source_id else 'common_snapshot',
                        'desired_services_after_activation': {'delivery': True, 'pickup': False, 'car_pickup': False},
                        'body': {'settings': changes,
                                 'reason': 'Owner-approved independent KPS defaults for Enbishan; prepared, not live'}})
    if set(source_by_branch) - targets:
        raise ValueError('Source selection contains an unknown target branch')
    return patches


def apply_isolated(conn, actor_id, source_ids, target_ids, *, source_by_branch=None):
    """Explicit local test application; production/staging are rejected outright.

    Caller owns the transaction. Reuses existing branch records and permissions;
    never creates duplicate branches, employees or shifts. Real deployment uses
    the normal reviewed owner configuration route, not this test helper.
    """
    import os
    from . import admin, db, multibrand
    if os.getenv('APP_ENV') != 'testing' or os.getenv('ENBISHAN_ISOLATED_ENABLED') != 'true':
        raise RuntimeError('Operations preparation application is local-test-only')
    multibrand.isolation_guard()
    user = conn.execute('SELECT * FROM users WHERE id=%s AND active', (actor_id,)).fetchone()
    if not user or user['role'] != 'owner' or not multibrand.project(conn, user, 'enbishan'):
        raise PermissionError('An owner with explicit Enbishan membership is required')
    if not multibrand.project(conn, user, 'kps'):
        raise PermissionError('Reading KPS defaults requires explicit KPS authority')
    if not source_ids or not target_ids or len(set(source_ids)) != len(source_ids) or len(set(target_ids)) != len(target_ids):
        raise ValueError('Select unique source and target branch IDs')
    sources = conn.execute("SELECT * FROM branches WHERE brand_id='kps' AND id=ANY(%s) FOR SHARE", (list(source_ids),)).fetchall()
    targets = conn.execute("SELECT * FROM branches WHERE brand_id='enbishan' AND id=ANY(%s) FOR UPDATE", (list(target_ids),)).fetchall()
    if len(sources) != len(source_ids) or len(targets) != len(target_ids):
        raise ValueError('Branch selection crosses brand or contains missing records')
    # Enforce even an owner's explicit branch restriction when projecting brands.
    for brand, rows in (('kps', sources), ('enbishan', targets)):
        principal = multibrand.project(conn, user, brand)
        if (principal.get('home_brand_id') or principal.get('branch_ids')) and not {row['id'] for row in rows} <= set(principal.get('branch_ids') or []):
            raise PermissionError('Selected branches exceed the owner brand grant')
    patches = prepare_branch_patches(sources, targets, source_by_branch=source_by_branch)
    by_id = {row['id']: row for row in targets}
    brand_row = conn.execute("SELECT settings FROM app_settings WHERE brand_id='enbishan' FOR UPDATE").fetchone()
    if not brand_row:
        raise ValueError('Enbishan app settings must already exist')
    profiles = prepare_invoice_profiles(targets, brand_row['settings'].get('invoice_profiles'))
    for patch in patches:
        row = by_id[patch['id']]
        changes = patch['body']['settings']
        admin.validate_branch_settings(changes, row['city'], brand_id='enbishan', branch_no=row['branch_no'])
        settings = row['settings'] | changes
        conn.execute("UPDATE branches SET settings=%s,updated_at=now() WHERE id=%s AND brand_id='enbishan'", (db.json(settings), row['id']))
        db.audit(conn, actor_id, 'enbishan.operations.prepared', 'branch', row['id'],
                 {'source_branch_id': patch['source_branch_id'], 'fields': list(changes), 'live': False})
    conn.execute("UPDATE app_settings SET settings=%s,updated_at=now() WHERE brand_id='enbishan'",
                 (db.json(brand_row['settings'] | {'invoice_profiles': profiles}),))
    db.audit(conn, actor_id, 'enbishan.invoice_profiles.prepared', 'brand', 'enbishan',
             {'branch_ids': list(target_ids), 'live': False})
    return patches


def prepare_invoice_profiles(enbishan_branches, current=None):
    """Explicit owner-approved legal seller snapshot, not a cross-brand fallback.

    Existing owner profiles/assignments are preserved. This returns preparation
    data only and does not authorize fiscal issuance or online payments.
    """
    from .fiscal import SELLER
    from .invoice_profiles import ProfileBody, validate
    config = deepcopy(current or {'entities': {}, 'assignments': {}})
    key = 'reem'
    config['entities'].setdefault(key, {
        'legal_name': SELLER['legal_name'], 'vat_enabled': True,
        'vat_number': SELLER['vat_number'], 'address': SELLER['address']})
    seen = set()
    for branch in enbishan_branches:
        if (branch.get('brand_id') != 'enbishan' or not branch.get('id')
                or type(branch.get('branch_no')) is not int
                or branch['branch_no'] not in APPROVED_BRANCH_NUMBERS or branch['id'] in seen):
            raise ValueError('Fiscal preparation requires unique approved Enbishan branches')
        seen.add(branch['id'])
        config['assignments'].setdefault(branch['id'], key)
    validate(ProfileBody(**config, reason='Owner approved same KPS legal operator', revision='preparation-only'))
    return config
