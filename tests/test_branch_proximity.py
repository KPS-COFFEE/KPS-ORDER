"""Geographic evidence stays bounded and missing coordinates never become zero."""
import pytest
from fastapi import HTTPException, Response
from app import branch_proximity as proximity
from test_operations import ops


def branch(identifier, lng, *, city='حفر الباطن', brand='kps', verified=True):
    return dict(id=identifier, name=identifier, brand_id=brand, city=city,
                settings={'location': {'lat':28.4,'lng':lng}, 'location_verified':verified})


def test_geographic_ranking_excludes_other_brand_city_and_unverified_coordinates():
    point={'lat':28.4,'lng':46.0}
    source=branch('source',46.04)
    result=proximity.comparison(point,source,[source,branch('near',46.01),
        branch('wrong-city',46.00001,city='رفحاء'),branch('wrong-brand',46.00001,brand='enbishan'),
        branch('unverified',46.00001,verified=False)])
    assert result['nearest_branch_id']=='near'
    assert 900 < result['nearest_distance_meters'] < 1100
    assert result['distance_to_customer_meters'] > result['nearest_distance_meters']
    assert result['is_nearest'] is False


@pytest.mark.parametrize('point',[None,{}, {'lat':0,'lng':None}])
def test_missing_customer_location_does_not_invent_distance(point):
    source=branch('source',46)
    result=proximity.comparison(point,source,[source])
    assert result['distance_to_customer_meters'] is None
    assert result['nearest_branch_id'] is None
    assert result['is_nearest'] is None


def test_report_does_not_allow_driver_or_customer_even_with_report_permission(monkeypatch):
    monkeypatch.setattr(proximity.auth,'has_permission',lambda *_:True)
    for role in ['driver','customer','delivery_staff','admin']:
        with pytest.raises(HTTPException) as error:
            proximity.report(Response(),user={'role':role})
        assert error.value.status_code==403


@pytest.mark.postgres
def test_daily_report_is_read_only_scoped_and_separates_missing_printing(ops):
    actor,post,order,_=ops
    oid=order('delivery')
    order('pickup')
    for role in ['owner','manager']:
        response=actor(role).get('/api/branch-proximity')
        assert response.status_code==200,response.text
        rows=response.json()['rows']
        assert [row['id'] for row in rows]==[oid]
        assert rows[0]['preparation_branch_name']
        assert rows[0]['print_branch_name'] is None
        assert rows[0]['distance_to_customer_meters'] is None
        assert response.headers['cache-control']=='private, no-store'
    assert actor('other').get('/api/branch-proximity').json()['rows']==[]
    assert actor('driver').get('/api/branch-proximity').status_code==403
