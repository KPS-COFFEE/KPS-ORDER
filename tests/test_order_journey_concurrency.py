"""Concurrent endpoint functions use the same real canonical PostgreSQL locks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app import db, orders, order_amendments
from test_operations import ops
from test_order_amendments import amendment_case
from test_order_interventions import controlled_order


@pytest.mark.postgres
def test_customer_basket_edit_racing_manager_accept_cannot_accept_stale_basket(amendment_case, ops):
    _, post, _, _ = ops
    post('manager', '/api/shifts', {'kind': 'manager', 'branch_ids': ['ops-branch']})
    oid = controlled_order(state='new', snapshot_overrides={
        'delivery_workflow': 'manager_dispatch', 'delivery_period': 'evening'})
    with db.tx() as c:
        principals = {role: c.execute('SELECT * FROM users WHERE id=%s', ('ops-'+role,)).fetchone()
                      for role in ('customer', 'manager')}
    barrier = Barrier(2)

    def run(role):
        barrier.wait(timeout=10)
        try:
            if role == 'customer':
                body = order_amendments.Proposal(version=1,
                    items=[{'product_id': 'ops-product', 'quantity': 2}],
                    customer={'name': 'اختبار'}, reason='إضافة صنف قبل القبول')
                result = order_amendments.request_amendment(oid, body,
                    user=principals[role], idempotency_key=uuid4().hex)
            else:
                result = orders.manager_accept(oid, orders.ManagerAcceptanceBody(version=1),
                    user=principals[role], idempotency_key=uuid4().hex)
            return role, 200, result
        except HTTPException as exc:
            return role, exc.status_code, exc.detail

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict((role, (status, value)) for role, status, value in pool.map(run, principals))
    assert sorted(status for status, _ in results.values()) == [200, 409]
    with db.tx() as c:
        saved = c.execute('SELECT * FROM orders WHERE id=%s', (oid,)).fetchone()
        accepted = c.execute("SELECT count(*) n FROM order_events WHERE order_id=%s "
                             "AND event_type='preparation.accepted'", (oid,)).fetchone()['n']
        revisions = c.execute('SELECT * FROM order_revisions WHERE order_id=%s', (oid,)).fetchall()
    assert saved['version'] == 2
    if results['customer'][0] == 200:
        assert results['customer'][1]['status'] == 'applied'
        assert saved['preparation_status'] == 'new' and saved['total_halalas'] == 2000
        assert saved['amendment_revision'] == 1 and len(revisions) == 1 and accepted == 0
    else:
        assert saved['preparation_status'] == 'accepted' and saved['total_halalas'] == 1000
        assert saved['amendment_revision'] == 0 and revisions == [] and accepted == 1
