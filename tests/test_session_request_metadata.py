"""Session metadata reuses one fresh listing while retaining live authorization."""
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app import auth, central_operations, customer_message_routing, multibrand


class Connection:
    def __init__(self):
        self.grants = {'kps': ['kps-branch'], 'enbishan': ['enbishan-branch']}
        self.session_live = True
        self.calls = []
        self.rows = []

    def user(self):
        return {'id': 'staff', 'active': True, 'role': 'delivery_manager',
                'brand_id': 'kps', 'branch_ids': list(self.grants['kps'])}

    def execute(self, sql, args):
        self.calls.append((sql, args))
        if sql.startswith('SELECT u.*,CASE'):
            assert all(value in sql for value in (
                's.revoked_at IS NULL', 's.expires_at>now()', 'u.active AND b.active'))
            self.rows = [self.user()] if self.session_live else []
        elif sql.startswith('SELECT b.id,b.name FROM brands'):
            self.rows = [{'id': brand, 'name': brand} for brand in sorted(self.grants)]
        elif sql.startswith('SELECT m.branch_ids FROM staff_brand_memberships'):
            self.rows = [{'branch_ids': list(self.grants[args[1]])}] if args[1] in self.grants else []
        elif sql.startswith('SELECT settings FROM app_settings'):
            value = {'delivery': ['staff'], 'care': []} if args[0] == 'enbishan' else None
            self.rows = [{'settings': {'customer_message_routing': value}}]
        elif sql.startswith('SELECT id,brand_id,branch_no,name,city,settings FROM branches'):
            self.rows = [{'id': identifier, 'brand_id': args[0], 'branch_no': 1,
                          'name': 'Synthetic branch', 'city': 'Synthetic city', 'settings': {}}
                         for identifier in self.grants.get(args[0], [])
                         if args[1] or identifier in args[2]]
        else:
            raise AssertionError(sql)
        return self

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

    def listing_count(self):
        return sum(sql.startswith('SELECT b.id,b.name FROM brands') for sql, _ in self.calls)


def configured(monkeypatch):
    from app import central_workdays, portals, staff_geo_access, storefront_scope
    monkeypatch.setattr(auth, 'context_cookies_enabled', lambda conn=None: False)
    monkeypatch.setattr(auth, 'owner_session_approved', lambda conn, user: False)
    monkeypatch.setattr(staff_geo_access, 'require_user', lambda *args: None)
    monkeypatch.setattr(multibrand, 'request_user', lambda conn, request, user: user)
    monkeypatch.setattr(multibrand, 'enabled', lambda: True)
    monkeypatch.setattr(multibrand, 'isolation_guard', lambda: None)
    monkeypatch.setattr(central_operations, 'enabled', lambda: True)
    monkeypatch.setattr(central_operations, 'project_read', lambda conn, request, user: user)
    monkeypatch.setattr(central_workdays, 'drain_metadata', lambda conn, user: [])
    monkeypatch.setattr(portals, 'enforce_role', lambda *args: None)
    monkeypatch.setattr(storefront_scope, 'require_brand', lambda *args: None)
    return Connection()


def request(csrf='synthetic-csrf'):
    return Request({'type': 'http', 'method': 'GET', 'path': '/api/session',
                    'headers': [(b'cookie', (auth.COOKIE + '=' + 's' * 60 + '; ' +
                                            auth.CSRF_COOKIE + '=' + csrf).encode())]})


def test_one_brand_listing_per_request_and_revocation_is_visible_on_next_request(monkeypatch):
    conn = configured(monkeypatch)
    first = auth._find_session(conn, request())
    assert conn.listing_count() == 1
    assert {b['id'] for b in first['operations_brands']} == {'kps', 'enbishan'}
    assert set(first['_message_capabilities']) == customer_message_routing.CAPABILITIES
    assert {b['id'] for b in first['central_operations']['brands']} == {'kps', 'enbishan'}

    del conn.grants['enbishan']
    second = auth._find_session(conn, request())
    assert conn.listing_count() == 2
    assert [b['id'] for b in second['operations_brands']] == ['kps']
    assert second['_message_capabilities'] == []
    assert [b['id'] for b in second['central_operations']['brands']] == ['kps']

    conn.grants['kps'] = []
    third = auth._find_session(conn, request())
    assert conn.listing_count() == 3
    assert third['branch_ids'] == []
    assert third['central_operations']['brands'][0]['branches'] == []

    conn.session_live = False
    assert auth._find_session(conn, request()) is None
    assert conn.listing_count() == 3
    assert sum(sql.startswith('SELECT u.*,CASE') for sql, _ in conn.calls) == 4


def test_supplied_listing_cannot_replace_current_brand_membership_checks(monkeypatch):
    conn = configured(monkeypatch)
    listing = multibrand.available(conn, conn.user())
    del conn.grants['enbishan']
    scopes = central_operations.context(conn, conn.user(), available_brands=listing)
    assert [b['id'] for b in scopes['brands']] == ['kps']
    assert customer_message_routing.capabilities(conn, conn.user(), available_brands=listing) == set()
    assert conn.listing_count() == 1
    assert sum(sql.startswith('SELECT m.branch_ids FROM staff_brand_memberships')
               for sql, _ in conn.calls) == 2


def test_default_helper_calls_still_read_fresh_brand_lists(monkeypatch):
    conn = configured(monkeypatch)
    first = central_operations.context(conn, conn.user())
    assert len(first['brands']) == 2
    assert customer_message_routing.capabilities(conn, conn.user())
    del conn.grants['enbishan']
    second = central_operations.context(conn, conn.user())
    assert [b['id'] for b in second['brands']] == ['kps']
    assert customer_message_routing.capabilities(conn, conn.user()) == set()
    assert conn.listing_count() == 4


def test_customer_does_not_read_staff_brand_metadata(monkeypatch):
    conn = configured(monkeypatch)
    customer = {**conn.user(), 'role': 'customer'}
    monkeypatch.setattr(conn, 'user', lambda: dict(customer))
    user = auth._find_session(conn, request())
    assert user['role'] == 'customer'
    assert user['_message_capabilities'] == []
    assert 'operations_brands' not in user
    assert len(conn.calls) == 1


def test_wrong_portal_role_rejected_before_staff_metadata_queries(monkeypatch):
    from app import portals
    enforce_role = portals.enforce_role
    conn = configured(monkeypatch)
    owner = {**conn.user(), 'role': 'owner'}
    monkeypatch.setattr(conn, 'user', lambda: dict(owner))
    monkeypatch.setattr(portals, 'enabled', lambda: True)
    monkeypatch.setattr(portals, 'enforce_role', enforce_role)
    incoming = request()
    incoming.scope['headers'].append((b'host', b'order.kps.sa'))
    with pytest.raises(HTTPException) as denied:
        auth._find_session(conn, incoming)
    assert denied.value.status_code == 403
    assert denied.value.detail['code'] == 'PORTAL_ROLE_FORBIDDEN'
    assert len(conn.calls) == 1



def test_unlisted_routing_avoids_membership_work_but_config_remains_live(monkeypatch):
    user = {'id': 'staff', 'active': True, 'role': 'delivery_manager', 'brand_id': 'kps'}
    listing = [{'id': 'kps'}]
    policy = {'delivery': [], 'care': []}
    monkeypatch.setattr(customer_message_routing, 'config', lambda conn, brand: policy)
    project = Mock(return_value={'branch_ids': ['branch']})
    monkeypatch.setattr(multibrand, 'project', project)
    assert customer_message_routing.capabilities(None, user, available_brands=listing) == set()
    project.assert_not_called()
    policy['delivery'] = ['staff']
    assert customer_message_routing.capabilities(None, user, available_brands=listing) == customer_message_routing.CAPABILITIES
    assert project.call_count == 1
    project.return_value = None
    assert customer_message_routing.capabilities(None, user, available_brands=listing) == set()
    assert project.call_count == 2
