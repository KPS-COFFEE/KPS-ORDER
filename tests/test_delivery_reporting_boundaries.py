"""Delivery report filters share 16:00 without re-dating saved money or shifts."""
from datetime import datetime
import pytest
from app import db,service_hours
from test_operations import ops


def test_date_bounds_match_operational_day_and_preserve_explicit_times():
    parse=service_hours.parse_reporting_bound
    low=parse('2026-10-23',delivery=True);high=parse('2026-10-24',delivery=True)
    assert low.isoformat()=='2026-10-23T16:00:00+03:00'
    assert high.isoformat()=='2026-10-24T16:00:00+03:00'
    for stamp,included in [('2026-10-23T15:59:59',False),('2026-10-23T16:00:00',True),
                           ('2026-10-24T00:00:00',True),('2026-10-24T15:59:59',True),('2026-10-24T16:00:00',False)]:
        point=parse(stamp,delivery=True)
        assert (low<=point<high)==included
    assert parse('2026-10-23T00:00:00',delivery=True).hour==0
    assert parse('2026-10-23T13:00:00+00:00',delivery=True)==low
    assert parse('2026-10-23').hour==0


@pytest.mark.postgres
def test_order_and_dashboard_date_filters_include_same_delivery_boundaries(ops):
    actor,_,order,_=ops;ids=[]
    stamps=['2026-10-23T15:59:59+03:00','2026-10-23T16:00:00+03:00','2026-10-24T00:00:00+03:00',
            '2026-10-24T15:59:59+03:00','2026-10-24T16:00:00+03:00']
    for stamp in stamps:
        oid=order();ids.append(oid)
        with db.tx() as c:c.execute('UPDATE orders SET created_at=%s WHERE id=%s',(datetime.fromisoformat(stamp),oid))
    params={'period':'custom','start':'2026-10-23','end':'2026-10-24','service':'delivery'}
    for endpoint in ('/api/orders','/api/dashboard'):
        result=actor('owner').get(endpoint,params=params)
        assert result.status_code==200,result.text
        data=result.json()
        if endpoint.endswith('orders'):assert {o['id'] for o in data['orders']}==set(ids[1:4])
        else:assert data['summary']['orders']==3 and data['period']['operational_day_start_minutes']==960
        explicit=actor('owner').get(endpoint,params={**params,'start':'2026-10-23T15:59:00','end':'2026-10-23T16:00:00'})
        assert explicit.status_code==200,explicit.text
        if endpoint.endswith('orders'):assert {o['id'] for o in explicit.json()['orders']}=={ids[0]}
        else:assert explicit.json()['summary']['orders']==1
    # Delivery manager's omitted service uses delivery clock; owner/general remains calendar/configured.
    general={k:v for k,v in params.items() if k!='service'}
    manager=actor('manager').get('/api/dashboard',params=general).json()
    assert manager['summary']['orders']==3 and manager['period']['operational_day_start_minutes']==960
    pickup=actor('manager').get('/api/dashboard',params={**general,'service':'pickup'}).json()
    assert pickup['period']['operational_day_start_minutes']==0
    owner=actor('owner').get('/api/dashboard',params=general).json()
    assert owner['summary']['orders']==2 and owner['period']['operational_day_start_minutes']==0
    with db.tx() as c:
        assert [c.execute('SELECT created_at FROM orders WHERE id=%s',(oid,)).fetchone()['created_at'] for oid in ids]==[datetime.fromisoformat(stamp) for stamp in stamps]
