"""Cross-brand station aggregation and independent authorization failures."""
import pytest
from fastapi import HTTPException
from app import dispatch_map, central_operations, central_workdays, multibrand


def value(brand, count):
    tasks = [{'id': brand + str(i), 'location': {'lat': 28., 'lng': 46.}} for i in range(count)]
    return {'driver_id': 'driver', 'origin': {'lat': 28., 'lng': 46.}, 'brand_id': brand,
            'tasks': tasks, 'stops': [dict(t, kind='dropoff') for t in tasks],
            'return_branches': [{'id': brand + '-branch'}], 'next_destination': None}


@pytest.mark.parametrize('counts', [(1, 1), (2, 1)])
def test_mixed_context_retains_each_station_and_ledger(counts):
    result = dispatch_map.merge_route_contexts([value('kps', counts[0]), value('enbishan', counts[1])])
    assert len(result['tasks']) == sum(counts)
    assert len(result['stops']) == sum(counts)
    assert {b['brand_id'] for b in result['return_branches']} == {'kps', 'enbishan'}
    assert [t['brand_id'] for t in result['tasks']].count('kps') == counts[0]
    assert 'brand_id' not in result


def test_conflicting_return_intent_is_not_taken_from_first_ledger():
    a, b = value('kps', 1), value('enbishan', 1)
    a['next_destination'] = {'branch_id': 'kps-1'}
    b['next_destination'] = {'branch_id': 'enb-1'}
    assert dispatch_map.merge_route_contexts([a, b])['next_destination'] is None


class Connection:
    def execute(self, sql, args):
        self.args = args
        self.sql = sql
        return self

    def fetchone(self):
        if 'FROM shifts' in self.sql:
            return {'id': self.args[1], 'branch_ids': ['branch']}
        return {'id': 'driver'}


def setup(monkeypatch):
    monkeypatch.setattr(central_workdays, 'require_enabled', lambda *a: None)
    monkeypatch.setattr(central_operations, 'context', lambda *a: {'brands': [
        {'id': 'kps', 'branch_ids': ['branch']}, {'id': 'enbishan', 'branch_ids': ['branch']}]})
    monkeypatch.setattr(multibrand, 'project', lambda c, u, b: {**u, 'brand_id': b, 'branch_ids': ['branch']})
    monkeypatch.setattr(dispatch_map, 'shift_access', lambda *a: None)
    monkeypatch.setattr(central_workdays, 'association', lambda c, sid: {'id': 'shared'})


def test_error_in_authorized_leg_is_not_a_partial_success(monkeypatch):
    setup(monkeypatch)
    def resolve(c, user, driver):
        if user['brand_id'] == 'enbishan':
            raise HTTPException(409, 'unavailable')
        return value('kps', 1)
    monkeypatch.setattr(dispatch_map, 'route_context', resolve)
    with pytest.raises(HTTPException) as err:
        dispatch_map.central_route_context(Connection(), {'role': 'owner'}, 'driver')
    assert err.value.status_code == 409


def test_unrelated_open_shifts_cannot_be_joined(monkeypatch):
    setup(monkeypatch)
    monkeypatch.setattr(central_workdays, 'association', lambda c, sid: None)
    monkeypatch.setattr(dispatch_map, 'route_context', lambda c, u, d: value(u['brand_id'], 1))
    with pytest.raises(HTTPException) as err:
        dispatch_map.central_route_context(Connection(), {'role': 'owner'}, 'driver')
    assert err.value.status_code == 409


def test_matrix_budget_counts_elements_and_refuses_overshoot():
    from test_driver_routes import BudgetConnection
    c = BudgetConnection({})
    dispatch_map.consume_route_budget(c, 'driver', 'manager', units=100)
    assert c.settings[dispatch_map.ROUTE_BUDGET_KEY]['count'] == 100
    c.settings[dispatch_map.ROUTE_BUDGET_KEY]['count'] = dispatch_map.ROUTE_MONTHLY_LIMIT - 3
    from datetime import timedelta
    c.now += timedelta(seconds=60)
    with pytest.raises(HTTPException) as err:
        dispatch_map.consume_route_budget(c, 'driver', 'manager', units=4)
    assert err.value.status_code == 429
    assert c.writes == 1


@pytest.mark.parametrize('units', [0, -1, 626, True, 1.5])
def test_invalid_budget_units_cannot_write(units):
    with pytest.raises(HTTPException):
        dispatch_map.consume_route_budget(None, 'driver', 'manager', units=units)


def test_pending_pickup_preserves_missing_dropoff_and_dependencies(monkeypatch):
    monkeypatch.setattr(central_operations, 'enabled', lambda: False)
    monkeypatch.setattr(dispatch_map, 'map_config', lambda u: {'enabled': True})
    monkeypatch.setattr(dispatch_map.auth, 'has_permission', lambda *a: True)
    monkeypatch.setattr(dispatch_map.catalog, 'location_verified', lambda b: True)
    monkeypatch.setattr(dispatch_map.driver_destination, 'metadata', lambda c, u, rows: {'driver': {'next_destination': None}})
    class Routes:
        def execute(self, sql, args):
            self.sql = sql
            return self
        def fetchone(self):
            return {'id': 'driver', 'branch_ids': ['b1'], 'shift_branches': ['b1'], 'shift_id': 's1',
                    'lat': 28., 'lng': 46., 'updated_at': None}
        def fetchall(self):
            if 'FROM driver_tasks' in self.sql:
                return [{'id': 'pending', 'order_id': 'o1', 'order_no': 1, 'status': 'assigned', 'branch_id': 'b1', 'customer': {}},
                        {'id': 'picked', 'order_id': 'o2', 'order_no': 2, 'status': 'picked_up', 'branch_id': 'b1',
                         'customer': {'location': {'lat': 29., 'lng': 46.}}}]
            return [{'id': 'b1', 'name': 'Branch', 'settings': {'location': {'lat': 28., 'lng': 46.}}}]
    result = dispatch_map.route_context(Routes(), {'role': 'owner', 'brand_id': 'kps'}, 'driver')
    assert [t['id'] for t in result['tasks']] == ['picked']
    assert [t['id'] for t in result['stops']] == ['pickup:pending', 'dropoff:pending', 'dropoff:picked']
    assert result['stops'][1]['location'] is None
    assert result['stops'][1]['prerequisite_ids'] == ['pickup:pending']
    assert result['stops'][2]['prerequisite_ids'] == []
