from copy import deepcopy
from datetime import date

import pytest
from app import db
from app.operational_day_policy import present_dates, report_snapshot
from test_operations import ops


def test_projection_preserves_financial_evidence_and_original_date():
    source = {'shift': {'id': 's', 'brand_id': 'b', 'kind': 'manager',
                       'delivery_operational_date': '2026-10-22',
                       'submission': {'expected_halalas': 10700, 'counted_halalas': 10700}},
              'operational_date': '2026-10-22', 'summary': {'net_collections_halalas': 12900}}
    original = deepcopy(source)
    class Cursor:
        def execute(self, sql, params):
            assert params == ('s', 'b')
            return self
        def fetchone(self):
            return {'reporting_operational_date': date(2026, 10, 23)}
    result = report_snapshot(Cursor(), source)
    assert result['operational_date'] == '2026-10-23'
    assert result['original_operational_date'] == '2026-10-22'
    assert result['summary'] == source['summary']
    assert result['shift']['submission'] == source['shift']['submission']
    assert source == original
    row = {**source['shift'], 'reporting_operational_date': '2026-10-23'}
    assert present_dates({'shifts': [row]})['shifts'][0]['delivery_operational_date'] == '2026-10-23'
    assert row['delivery_operational_date'] == '2026-10-22'


@pytest.mark.postgres
def test_closed_morning_and_evening_share_friday_without_mutating_money(ops):
    actor, post, order, shifts = ops
    manager, driver = shifts()
    with db.tx() as c:
        # Friday morning, and a normal morning preparation on Saturday.
        c.execute("UPDATE shifts SET opened_at='2026-10-23T12:27:00+03:00',"
                  "delivery_operational_date='2026-10-22',delivery_window_snapshot=%s WHERE id=%s",
                  (db.json({'period': 'morning'}), manager))
        c.execute("UPDATE shifts SET opened_at='2026-10-23T16:01:00+03:00',"
                  "delivery_operational_date='2026-10-23' WHERE id=%s", (driver,))
        c.execute("UPDATE shifts SET status='closed' WHERE id=ANY(%s)", ([manager, driver],))
        before = c.execute('SELECT id,submission,opening_halalas,delivery_operational_date,reporting_operational_date '
                           'FROM shifts WHERE id=ANY(%s) ORDER BY id', ([manager, driver],)).fetchall()
    assert {r['reporting_operational_date'] for r in before} == {date(2026, 10, 23)}
    with pytest.raises(Exception, match='immutable'):
        with db.tx() as c:
            c.execute("UPDATE shifts SET opening_halalas=999 WHERE id=%s", (driver,))
    result = actor('owner').get('/api/shifts', params={'owner_view': 'history', 'start': '2026-10-23', 'end': '2026-10-23'})
    assert result.status_code == 200, result.text
    rows = {r['id']: r for r in result.json()['shifts']}
    assert {manager, driver} <= rows.keys()
    assert rows[manager]['delivery_operational_date'] == '2026-10-23'
    assert rows[manager]['original_operational_date'] == '2026-10-22'
    with db.tx() as c:
        after = c.execute('SELECT id,submission,opening_halalas,delivery_operational_date,reporting_operational_date '
                          'FROM shifts WHERE id=ANY(%s) ORDER BY id', ([manager, driver],)).fetchall()
    assert after == before
