"""Real isolated PostgreSQL routing, money preservation and preparation scopes."""
from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import db
from app.preparation_routing import pickup_branch_id
from test_core import _branch
from test_operations import ops


@pytest.fixture
def routing(ops):
    actor, post, _, shifts = ops
    source, target = 'ops-branch', 'route-target'
    seller = {'legal_name': 'Synthetic seller', 'vat_enabled': True, 'vat_number': '311887526600003'}
    config = {'entities': {'same-seller': seller}, 'assignments': {source: 'same-seller', target: 'same-seller'}}
    settings = _branch()['settings']
    settings['coverage']['radius_meters'] = 10000
    product = {'enabled': True, 'source_verified': True, 'option_groups': [
        {'code': 'temperature', 'name': 'Temperature', 'required': True, 'values': [
            {'code': 'hot', 'name': 'Hot'}, {'code': 'iced', 'name': 'Iced'}]}]}
    with db.tx() as conn:
        conn.execute('UPDATE branches SET settings=%s WHERE id=%s', (db.json(settings), source))
        target_settings = deepcopy(settings)
        target_settings.update(location={'lat': 28.405, 'lng': 45.905}, delivery_fee_halalas=900)
        conn.execute("INSERT INTO branches(id,brand_id,branch_no,source_id,name,city,settings) "
                     "VALUES(%s,'ops-brand',2,'route-target-source','Target preparation','حفرالباطن',%s)",
                     (target, db.json(target_settings)))
        conn.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) "
                     "VALUES('route-product','ops-brand','Synthetic coffee','Coffee',9999,%s)", (db.json(product),))
        conn.execute("INSERT INTO app_settings(brand_id,settings) VALUES('ops-brand',%s) "
                     "ON CONFLICT(brand_id) DO UPDATE SET settings=EXCLUDED.settings", (db.json({'invoice_profiles': config}),))
        conn.execute("UPDATE users SET branch_ids=%s WHERE id IN ('ops-manager','ops-driver')", (db.json([source, target]),))
        conn.execute("INSERT INTO users(id,display_name,role,brand_id,branch_ids) VALUES('ops-target','Target employee','branch_manager','ops-brand',%s)", (db.json([target]),))
        for bid in (source, target):
            conn.execute("INSERT INTO branch_print_devices(id,brand_id,branch_id,label,printer_name,token_hash,approved_at,created_by) "
                         "VALUES(%s,'ops-brand',%s,'Synthetic','TEST',%s,now(),'ops-owner')", ('printer-' + bid, bid, 'synthetic-' + bid))

    def order(*, workflow='branch_fulfillment', snapshot_changes=None, status='new', customer_phone='+966500000001'):
        oid = 'route-order-' + uuid4().hex
        snapshot = {'branch_id': source, 'branch_name': 'Original branch', 'branch_city': 'حفر الباطن',
            'branch_location': deepcopy(settings['location']), 'delivery_workflow': workflow,
            'receipt_profile': {'entity_id': 'same-seller', **seller}, 'notes': 'Preparation note',
            'items': [{'product_id': 'route-product', 'name': 'Synthetic coffee', 'quantity': 1,
                'unit_price_halalas': 1000, 'line_total_halalas': 1000, 'note': 'No sugar',
                'units': [{'price_halalas': 1000, 'options': {'temperature': 'hot'},
                    'option_snapshot': {'temperature': {'name': 'Temperature', 'choices': [
                        {'code': 'hot', 'name': 'Hot', 'price_delta_halalas': 0}]}}}]}],
            **(snapshot_changes or {})}
        with db.tx() as conn:
            conn.execute("INSERT INTO orders(id,brand_id,branch_id,customer_id,service,preparation_status,delivery_status,payment_method,"
                         "subtotal_halalas,delivery_fee_halalas,total_halalas,snapshot,customer) "
                         "VALUES(%s,'ops-brand',%s,'ops-customer','delivery',%s,'pending','cash',1000,500,1500,%s,%s)",
                         (oid, source, status, db.json(snapshot), db.json({'name': 'Synthetic customer', 'phone': customer_phone,
                             'address': 'Customer private address', 'location': {'lat': 28.408, 'lng': 45.908}})))
        return oid

    def choose(oid, *, who='manager', destination=target, status=200, key=None, body=None):
        with db.tx() as conn:
            row = conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
            current = pickup_branch_id(conn, row)
        return post(who, '/api/preparation-routing/orders/' + oid, body or {
            'target_branch_id': destination, 'expected_preparation_branch_id': current,
            'version': row['version'], 'reason': 'Synthetic nearer preparation'}, status=status, key=key)

    def both_shifts():
        manager = post('manager', '/api/shifts', {'kind': 'manager', 'branch_ids': [source, target]})['shift']['id']
        driver = post('driver', '/api/shifts', {'kind': 'driver', 'branch_ids': [source, target], 'manager_shift_id': manager})['shift']['id']
        return manager, driver

    return {'actor': actor, 'post': post, 'order': order, 'choose': choose, 'source': source, 'target': target,
            'shifts': shifts, 'both_shifts': both_shifts, 'seller': seller, 'config': config}


@pytest.mark.postgres
@pytest.mark.parametrize('workflow', ['manager_dispatch', 'branch_fulfillment'])
def test_choice_preserves_canonical_sale_and_returns_both_distances(routing, workflow):
    r = routing; oid = r['order'](workflow=workflow)
    with db.tx() as conn:
        before = conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
    context = r['actor']('manager').get('/api/preparation-routing/orders/' + oid)
    assert context.status_code == 200, context.text
    selected = next(b for b in context.json()['branches'] if b['id'] == r['target'])
    assert selected['available'] and selected['distance_from_branch_meters'] > 0 and selected['distance_to_customer_meters'] > 0
    chosen = r['choose'](oid, key='route-once')
    with db.tx() as conn:
        after = conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
        for key in ('branch_id', 'brand_id', 'customer_id', 'snapshot', 'customer', 'subtotal_halalas', 'delivery_fee_halalas', 'total_halalas'):
            assert after[key] == before[key]
        assert pickup_branch_id(conn, after) == r['target']
        assert after['version'] == before['version'] + 1
        assert after['preparation_route_id'] == chosen['route_id']
        assert conn.execute('SELECT count(*) n FROM collections WHERE order_id=%s', (oid,)).fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) n FROM fiscal_documents WHERE order_id=%s', (oid,)).fetchone()['n'] == 0
        assert conn.execute('SELECT count(*) n FROM order_preparation_routes WHERE order_id=%s', (oid,)).fetchone()['n'] == 1


@pytest.mark.postgres
def test_target_has_only_minimal_preparation_access(routing):
    r = routing; oid = r['order'](); r['choose'](oid)
    assert r['actor']('target').get('/api/orders/' + oid).status_code == 404
    task = r['actor']('target').get('/api/preparation-routing/tasks/' + oid)
    assert task.status_code == 200, task.text
    assert 'phone' not in task.text and 'halalas' not in task.text and 'receipt_profile' not in task.text
    assert 'Customer private address' not in task.text and 'Synthetic customer' not in task.text
    assert 'Hot' in task.text and 'Preparation note' in task.text
    assert r['actor']('branch').get('/api/preparation-routing/tasks/' + oid).status_code == 404
    r['post']('branch', '/api/orders/' + oid + '/preparation', {'version': 2, 'status': 'accepted'}, status=409)
    for version, status in [(2, 'accepted'), (3, 'preparing'), (4, 'ready')]:
        result = r['post']('target', '/api/preparation-routing/tasks/' + oid + '/prepare', {'version': version, 'status': status})
        assert result['preparation_task']['preparation_status'] == status
    r['post']('target', '/api/preparation-routing/tasks/' + oid + '/prepare', {'version': 5, 'status': 'cancelled'}, status=422)
    r['post']('target', '/api/orders/' + oid + '/collect', {'method': 'cash', 'amount_halalas': 1500}, status=404)


@pytest.mark.postgres
def test_printer_and_output_follow_preparation_branch_without_repricing(routing):
    r = routing; oid = r['order'](); r['choose'](oid)
    devices = r['actor']('manager').get('/api/printing/operational-devices', params={'order_id': oid})
    assert devices.status_code == 200, devices.text
    assert [d['branch_id'] for d in devices.json()['devices']] == [r['target']]
    r['post']('manager', '/api/printing/orders/' + oid, {'device_id': 'printer-' + r['source']}, status=409)
    printed = r['post']('manager', '/api/printing/orders/' + oid, {'device_id': 'printer-' + r['target'], 'kind': 'both'})
    with db.tx() as conn:
        jobs = conn.execute('SELECT * FROM branch_print_jobs WHERE order_id=%s ORDER BY kind', (oid,)).fetchall()
        assert {j['branch_id'] for j in jobs} == {r['target']}
        assert all(j['payload']['preparation_branch_id'] == r['target'] for j in jobs)
        assert conn.execute('SELECT delivery_fee_halalas FROM orders WHERE id=%s', (oid,)).fetchone()['delivery_fee_halalas'] == 500
        invoice = next(j for j in jobs if j['kind'] == 'order')
        conn.execute("UPDATE branch_print_jobs SET status='submitted',claimed_at=now(),finished_at=now() WHERE id=%s", (invoice['id'],))
    output = r['post']('manager', '/api/printing/orders/' + oid + '/output', {'version': 2, 'job_id': invoice['id'], 'paper_confirmed': True})
    assert output['confirmed']
    blocked = r['choose'](oid, destination=r['source'], status=409)
    assert blocked['detail']['code'] == 'PREPARATION_ROUTE_PRINTED'


@pytest.mark.postgres
@pytest.mark.parametrize('print_status', ['queued', 'claimed', 'submitted', 'uncertain', 'cancelled'])
def test_any_print_attempt_locks_branch_choice(routing, print_status):
    r = routing; oid = r['order']()
    with db.tx() as conn:
        conn.execute("INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,order_id,kind,status,payload,requested_by,claimed_at,finished_at) "
                     "VALUES(%s,'ops-brand',%s,%s,%s,'order',%s,'{}','ops-manager',"
                     "CASE WHEN %s IN ('claimed','submitted','uncertain') THEN now() END,"
                     "CASE WHEN %s IN ('submitted','uncertain','cancelled') THEN now() END)",
                     ('route-print-' + uuid4().hex, r['source'], 'printer-' + r['source'], oid, print_status, print_status, print_status))
    assert r['choose'](oid, status=409)['detail']['code'] == 'PREPARATION_ROUTE_PRINTED'


@pytest.mark.postgres
@pytest.mark.parametrize('condition,code', [('city', 'PREPARATION_CITY_MISMATCH'),
    ('seller', 'PREPARATION_SELLER_MISMATCH'), ('printer', 'PREPARATION_PRINTER_REQUIRED'),
    ('option', 'PRODUCT_UNAVAILABLE'), ('coverage', 'OUTSIDE_COVERAGE')])
def test_candidate_guards_are_rechecked_at_write(routing, condition, code):
    r = routing; oid = r['order']()
    assert r['actor']('manager').get('/api/preparation-routing/orders/' + oid).status_code == 200
    with db.tx() as conn:
        if condition == 'city':
            conn.execute("UPDATE branches SET city='الخفجي' WHERE id=%s", (r['target'],))
        elif condition == 'seller':
            config = deepcopy(r['config']); config['entities']['other'] = {**r['seller'], 'legal_name': 'Other seller'}
            config['assignments'][r['target']] = 'other'
            conn.execute("UPDATE app_settings SET settings=%s WHERE brand_id='ops-brand'", (db.json({'invoice_profiles': config}),))
        elif condition == 'printer':
            conn.execute('UPDATE branch_print_devices SET revoked_at=now() WHERE branch_id=%s', (r['target'],))
        elif condition == 'option':
            product = conn.execute("SELECT settings FROM products WHERE id='route-product'").fetchone()['settings']
            product['option_groups'][0]['values'][0]['unavailable_branch_ids'] = [r['target']]
            conn.execute("UPDATE products SET settings=%s WHERE id='route-product'", (db.json(product),))
        else:
            settings = conn.execute('SELECT settings FROM branches WHERE id=%s', (r['target'],)).fetchone()['settings']
            settings['coverage']['radius_meters'] = 10
            conn.execute('UPDATE branches SET settings=%s WHERE id=%s', (db.json(settings), r['target']))
    assert r['choose'](oid, status=409)['detail']['code'] == code


@pytest.mark.postgres
def test_grant_revocation_version_and_replay_are_checked(routing):
    r = routing; oid = r['order']()
    body = {'target_branch_id': r['target'], 'expected_preparation_branch_id': r['source'], 'version': 1, 'reason': 'Test route replay'}
    r['choose'](oid, body=body, key='same-route')
    assert r['choose'](oid, body=body, key='same-route')['version'] == 2
    assert r['choose'](oid, body=body, key='different-route', status=409)['detail']['code'] == 'ORDER_VERSION_CONFLICT'
    with db.tx() as conn:
        conn.execute("UPDATE users SET permission_denials='[\"orders_route_preparation\"]' WHERE id='ops-manager'")
    r['choose'](oid, body=body, key='same-route', status=403)
    with db.tx() as conn:
        conn.execute("UPDATE users SET permission_denials='[]',branch_ids=%s WHERE id='ops-manager'", (db.json([r['source']]),))
    r['choose'](oid, body=body, key='same-route', status=403)


@pytest.mark.postgres
@pytest.mark.parametrize('command', ['route', 'prepare'])
@pytest.mark.parametrize('change,expected_status', [
    ('permission', 403), ('branch', 404), ('active', 403), ('role', 403),
])
def test_mutation_and_replay_recheck_live_grants_with_stale_dependency(routing, command, change, expected_status):
    r = routing
    first, second = r['order'](), r['order'](customer_phone='+966500000002')
    if command == 'prepare':
        r['choose'](first); r['choose'](second)
        who, permission = 'target', 'orders_prepare'
        paths = [f'/api/preparation-routing/tasks/{oid}/prepare' for oid in (first, second)]
        body = {'version': 2, 'status': 'accepted'}
        expected_versions = (3, 2)
    else:
        who, permission = 'manager', 'orders_route_preparation'
        paths = [f'/api/preparation-routing/orders/{oid}' for oid in (first, second)]
        body = {'version': 1, 'target_branch_id': r['target'],
                'expected_preparation_branch_id': r['source'], 'reason': 'Synthetic live grant witness'}
        expected_versions = (2, 1)
    # Keep this exact dependency object for the successful request and both
    # requests after revocation, reproducing an auth snapshot before lock wait.
    client = r['actor'](who)
    headers = {'Idempotency-Key': 'routing-stale-actor-replay'}
    first_response = client.post(paths[0], json=body, headers=headers)
    assert first_response.status_code == 200, first_response.text
    with db.tx() as conn:
        if change == 'permission':
            conn.execute('UPDATE users SET permission_denials=%s WHERE id=%s', (db.json([permission]), 'ops-' + who))
        elif change == 'branch':
            conn.execute("UPDATE users SET branch_ids='[]' WHERE id=%s", ('ops-' + who,))
        elif change == 'active':
            conn.execute('UPDATE users SET active=false WHERE id=%s', ('ops-' + who,))
        else:
            conn.execute("UPDATE users SET role='admin',extra_permissions='[\"orders_view\"]' WHERE id=%s", ('ops-' + who,))
    replay = client.post(paths[0], json=body, headers=headers)
    assert replay.status_code == expected_status, replay.text
    new_request = client.post(paths[1], json=body, headers={'Idempotency-Key': 'routing-stale-actor-new'})
    assert new_request.status_code == expected_status, new_request.text
    with db.tx() as conn:
        rows = [conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone() for oid in (first, second)]
        assert tuple(row['version'] for row in rows) == expected_versions
        assert rows[1]['preparation_status'] == 'new'
        assert conn.execute('SELECT count(*) n FROM order_preparation_routes WHERE order_id=%s',
                            (second,)).fetchone()['n'] == (1 if command == 'prepare' else 0)


@pytest.mark.postgres
def test_target_branch_must_belong_to_source_brand_even_if_user_branch_list_contains_it(routing):
    r = routing; oid = r['order']()
    foreign = 'ops-branch-other'
    with db.tx() as conn:
        conn.execute('UPDATE users SET branch_ids=%s WHERE id=\'ops-manager\'',
                     (db.json([r['source'], r['target'], foreign]),))
    response = r['actor']('manager').get('/api/preparation-routing/orders/' + oid)
    assert response.status_code == 200, response.text
    assert foreign not in {row['id'] for row in response.json()['branches']}
    r['choose'](oid, destination=foreign, status=404)
    with db.tx() as conn:
        row = conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
        assert row['version'] == 1 and row['preparation_route_id'] is None
        assert not conn.execute('SELECT 1 FROM order_preparation_routes WHERE order_id=%s', (oid,)).fetchone()


@pytest.mark.postgres
def test_assigned_driver_needs_both_sale_and_preparation_branch_scope(routing):
    r = routing; oid = r['order'](workflow='manager_dispatch'); r['choose'](oid)
    manager = r['post']('owner', '/api/shifts', {'kind': 'manager', 'user_id': 'ops-manager', 'branch_ids': [r['source']]})['shift']['id']
    driver = r['post']('owner', '/api/shifts', {'kind': 'driver', 'user_id': 'ops-driver', 'branch_ids': [r['source']], 'manager_shift_id': manager})['shift']['id']
    r['post']('manager', '/api/orders/' + oid + '/assign', {'driver_shift_id': driver, 'accept_order': True}, status=409)
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM driver_tasks WHERE order_id=%s', (oid,)).fetchone()['n'] == 0


@pytest.mark.postgres
def test_routed_driver_assignment_pickup_projection_and_physical_departure(routing):
    r = routing; oid = r['order'](workflow='manager_dispatch'); r['choose'](oid)
    _, driver = r['both_shifts']()
    assigned = r['post']('manager', '/api/orders/' + oid + '/assign', {'driver_shift_id': driver, 'accept_order': True})
    tid = assigned['task']['id']
    response = r['actor']('driver').get('/api/tasks')
    assert response.status_code == 200, response.text
    task = next(t for t in response.json()['tasks'] if t['id'] == tid)
    assert task['branch_id'] == r['source'] and task['preparation_branch']['id'] == r['target']
    with db.tx() as conn:
        pickup_branch_id(conn, conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone())
        conn.execute("UPDATE driver_tasks SET status='picked_up',accepted_at=now()-interval '10 minutes',picked_up_at=now() WHERE id=%s", (tid,))
        conn.execute("UPDATE orders SET preparation_status='handed_over',delivery_status='picked_up' WHERE id=%s", (oid,))
        accepted = conn.execute('SELECT accepted_at FROM driver_tasks WHERE id=%s', (tid,)).fetchone()['accepted_at']
    r['post']('driver', '/api/driver-work-hours/depart', {'shift_id': driver, 'order_ids': [oid], 'branch_id': r['source']}, status=403)
    trip = r['post']('driver', '/api/driver-work-hours/depart', {'shift_id': driver, 'order_ids': [oid], 'branch_id': r['target']})['trip']
    assert trip['departure_branch_id'] == r['target']
    with db.tx() as conn:
        assert conn.execute('SELECT accepted_at FROM driver_tasks WHERE id=%s', (tid,)).fetchone()['accepted_at'] == accepted


@pytest.mark.postgres
@pytest.mark.parametrize('snapshot', [{'delivery_period': 'morning'}, {'internal_routing': {'reason': 'khaliya_branch_2'}}])
def test_fixed_morning_and_catalog_routing_are_not_overridden(routing, snapshot):
    r = routing; oid = r['order'](snapshot_changes=snapshot)
    # Scope itself may already hide a morning order; never grant broader access.
    response = r['actor']('manager').post('/api/preparation-routing/orders/' + oid,
        json={'version': 1, 'target_branch_id': r['target'], 'expected_preparation_branch_id': r['source'], 'reason': 'Attempt override'},
        headers={'Idempotency-Key': uuid4().hex})
    assert response.status_code in (403, 404, 409), response.text


@pytest.mark.postgres
def test_routing_history_and_original_branch_are_immutable(routing):
    r = routing; oid = r['order'](); route = r['choose'](oid)
    with pytest.raises(Exception), db.tx() as conn:
        conn.execute('UPDATE orders SET branch_id=%s WHERE id=%s', (r['target'], oid))
    with pytest.raises(Exception), db.tx() as conn:
        conn.execute("UPDATE order_preparation_routes SET reason='Edited history' WHERE id=%s", (route['route_id'],))
    with pytest.raises(Exception), db.tx() as conn:
        conn.execute('UPDATE orders SET preparation_route_id=NULL WHERE id=%s', (oid,))


def test_synthetic_roster_candidate_requires_no_route_query():
    class NoDatabase:
        def execute(self, *args):
            raise AssertionError('A synthetic branch readiness candidate has no order routing row')
    assert pickup_branch_id(NoDatabase(), {'brand_id': 'ops-brand', 'branch_id': 'source'}) == 'source'


@pytest.mark.postgres
def test_unaware_worker_cannot_prepare_or_print_routed_order(routing):
    r = routing; oid = r['order'](); r['choose'](oid)
    with pytest.raises(Exception, match='PREPARATION_ROUTE_RUNTIME_REQUIRED'), db.tx() as conn:
        conn.execute("UPDATE orders SET preparation_status='accepted' WHERE id=%s", (oid,))
    with pytest.raises(Exception, match='PREPARATION_ROUTE_RUNTIME_REQUIRED'), db.tx() as conn:
        conn.execute("INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,order_id,kind,payload,requested_by) "
                     "VALUES(%s,'ops-brand',%s,%s,%s,'order','{}','ops-manager')",
                     ('old-worker-' + uuid4().hex, r['target'], 'printer-' + r['target'], oid))
    # The marker is transaction-local: an earlier preview or action cannot make
    # a later old worker route-aware or erase the unchanged sale.
    with db.tx() as conn:
        row = conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
        assert row['preparation_status'] == 'new' and row['branch_id'] == r['source']
        conn.execute("UPDATE orders SET payment_status='paid' WHERE id=%s", (oid,))
    with pytest.raises(Exception, match='PREPARATION_ROUTE_RUNTIME_REQUIRED'), db.tx() as conn:
        conn.execute("UPDATE orders SET preparation_status='preparing' WHERE id=%s", (oid,))


@pytest.mark.postgres
def test_transaction_marker_cannot_authorize_another_route(routing):
    r = routing; first = r['order'](); second = r['order'](customer_phone='+966500000002')
    r['choose'](first); r['choose'](second)
    with pytest.raises(Exception, match='PREPARATION_ROUTE_RUNTIME_REQUIRED'), db.tx() as conn:
        pickup_branch_id(conn, conn.execute('SELECT * FROM orders WHERE id=%s', (first,)).fetchone())
        conn.execute("UPDATE orders SET preparation_status='accepted' WHERE id=%s", (second,))


def _approved_prior_writer_revision(conn, oid):
    """Write the ordinary approved revision chain, without the new app guard.

    The existing immutable-snapshot trigger must accept this evidence, so a
    refusal proves the route invariant rather than an incomplete SQL fixture.
    """
    before = conn.execute('SELECT * FROM orders WHERE id=%s FOR UPDATE', (oid,)).fetchone()
    after = deepcopy(before['snapshot'])
    after.update(subtotal_halalas=before['subtotal_halalas'] + 100,
                 delivery_fee_halalas=before['delivery_fee_halalas'], total_halalas=before['total_halalas'] + 100)
    item = after['items'][0]
    item['unit_price_halalas'] += 100
    item['line_total_halalas'] += 100
    item['units'][0]['price_halalas'] += 100
    aid, revision = 'prior-writer-amendment-' + uuid4().hex, before['amendment_revision'] + 1
    conn.execute("INSERT INTO order_amendments(id,order_id,requested_by,base_revision,proposal,status,reason,reviewed_by,"
                 "branch_confirmed_by,consent_by,consent_channel,consent_note) "
                 "VALUES(%s,%s,'ops-manager',%s,%s,'applied','Synthetic prior writer revision','ops-manager',"
                 "'ops-branch','ops-customer','customer','Synthetic consent')",
                 (aid, oid, before['amendment_revision'], db.json({'snapshot': after, 'customer': before['customer']})))
    conn.execute('INSERT INTO order_revisions(order_id,revision,amendment_id,before_snapshot,before_customer,after_snapshot,'
                 'after_customer,before_total,after_total,approved_by,consent_by,consent_channel) '
                 "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'ops-manager','ops-customer','customer')",
                 (oid, revision, aid, db.json(before['snapshot']), db.json(before['customer']), db.json(after),
                  db.json(before['customer']), before['total_halalas'], after['total_halalas']))
    conn.execute('UPDATE orders SET snapshot=%s,subtotal_halalas=%s,total_halalas=%s,amendment_revision=%s,'
                 'version=version+1 WHERE id=%s',
                 (db.json(after), after['subtotal_halalas'], after['total_halalas'], revision, oid))


@pytest.mark.postgres
@pytest.mark.parametrize('route_aware', [False, True])
def test_database_rejects_approved_old_writer_amendment_on_routed_order(routing, route_aware):
    r = routing; oid = r['order'](); r['choose'](oid)
    with db.tx() as conn:
        before = conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
        history = conn.execute('SELECT * FROM order_preparation_routes WHERE order_id=%s ORDER BY id', (oid,)).fetchall()
    with pytest.raises(Exception, match='PREPARATION_ROUTE_AMENDMENT'), db.tx() as conn:
        if route_aware:
            pickup_branch_id(conn, conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone())
        _approved_prior_writer_revision(conn, oid)
    with db.tx() as conn:
        assert conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone() == before
        assert conn.execute('SELECT * FROM order_preparation_routes WHERE order_id=%s ORDER BY id', (oid,)).fetchall() == history
        assert not conn.execute('SELECT 1 FROM order_amendments WHERE order_id=%s', (oid,)).fetchone()
        assert not conn.execute('SELECT 1 FROM order_revisions WHERE order_id=%s', (oid,)).fetchone()
        assert not conn.execute('SELECT 1 FROM collections WHERE order_id=%s', (oid,)).fetchone()
        assert not conn.execute('SELECT 1 FROM fiscal_documents WHERE order_id=%s', (oid,)).fetchone()


@pytest.mark.postgres
def test_approved_revision_remains_available_after_return_to_original_preparation(routing):
    r = routing; oid = r['order'](); r['choose'](oid)
    r['choose'](oid, destination=r['source'])
    with db.tx() as conn:
        _approved_prior_writer_revision(conn, oid)
    with db.tx() as conn:
        order = conn.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
        assert order['amendment_revision'] == 1 and order['total_halalas'] == 1600
        assert pickup_branch_id(conn, order) == r['source']
        assert conn.execute('SELECT count(*) n FROM order_revisions WHERE order_id=%s', (oid,)).fetchone()['n'] == 1


@pytest.mark.postgres
def test_preparation_history_is_preserved_and_exported_with_source_brand(routing):
    from app import exports
    r = routing; oid = r['order'](); r['choose'](oid)
    assert 'order_preparation_routes' in db.PRESERVED_TABLES
    assert exports.TABLES[-1] == 'order_preparation_routes'
    clause, values = exports._scoped_sql('order_preparation_routes', 'ops-brand')
    with db.tx() as conn:
        rows = conn.execute('SELECT * FROM order_preparation_routes WHERE ' + clause, values).fetchall()
        assert [row['order_id'] for row in rows] == [oid]
        exports._verify_preparation_routes(conn, {'orders', 'order_preparation_routes'})
        clause, values = exports._scoped_sql('order_preparation_routes', 'ops-other')
        assert not conn.execute('SELECT * FROM order_preparation_routes WHERE ' + clause, values).fetchall()
