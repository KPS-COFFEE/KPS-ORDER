"""Browser navigation must use real SMS work cookies, including exports."""

import pytest

from app import db
from tests.test_core import _make_user
from tests.sms_policy_helpers import sms_login


@pytest.mark.postgres
def test_hours_navigation_without_context_header_preserves_auth(client, monkeypatch):
    assert client.get('/driver-hours', follow_redirects=False).status_code == 303
    assert client.get('/api/driver-work-hours/report').status_code == 401
    owner = _make_user('owner')
    sms_login(client, owner, monkeypatch)
    assert client.cookies.get('kps_work_session')
    client.headers.pop('X-KPS-Context', None)
    page = client.get('/driver-hours')
    assert page.status_code == 200 and 'text/html' in page.headers['content-type']
    result = client.get('/api/driver-work-hours/report')
    assert result.status_code == 200, result.text
    report = result.json()
    for kind in ('xlsx', 'pdf'):
        response = client.get('/api/driver-work-hours/export.' + kind,
                              params={'start': report['start'], 'end': report['end']})
        assert response.status_code == 200
    with db.tx() as conn:
        conn.execute('UPDATE users SET active=false WHERE id=%s', (owner['id'],))
    assert client.get('/driver-hours', follow_redirects=False).status_code == 303
    assert client.get('/api/driver-work-hours/report').status_code == 401


@pytest.mark.postgres
def test_customer_cookie_cannot_open_hours(client, monkeypatch):
    owner = _make_user('owner')
    sms_login(client, owner, monkeypatch)
    client.headers.pop('X-KPS-Context', None)
    customer = client.post('/api/identity/guest', json={'display_name':'TEST customer', 'phone':'0501112222'})
    assert customer.status_code == 200
    # Even an explicit customer header cannot redirect the hours route to a
    # customer credential. The valid work session remains the only credential.
    assert client.get('/driver-hours', headers={'X-KPS-Context':'customer'}).status_code == 200
    client.cookies.delete('kps_work_session')
    assert client.get('/driver-hours', follow_redirects=False).status_code == 303
    assert client.get('/api/driver-work-hours/report').status_code == 401
