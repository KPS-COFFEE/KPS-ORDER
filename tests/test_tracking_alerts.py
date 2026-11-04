from datetime import datetime, timezone, timedelta
from app.tracking_alerts import movement_alerts

NOW=datetime(2026,10,26,12,tzinfo=timezone.utc)


def point(age,lng=45.,accuracy=10):
    return {'lat':28.,'lng':lng,'accuracy_m':accuracy,
            'captured_at':NOW-timedelta(seconds=age)}


def test_long_stop_requires_continuous_fresh_accurate_observations():
    points=[point(age) for age in (280,220,160,100,40)]
    assert [a['kind'] for a in movement_alerts(points,None,None,NOW)]==['long_stop']
    assert movement_alerts([point(300),point(1)],None,None,NOW)==[]
    assert movement_alerts([point(age,accuracy=150) for age in (280,220,160,100,40)],None,None,NOW)==[]
    assert movement_alerts([point(age) for age in (600,500,400)],None,None,NOW)==[]


def test_progress_deviation_is_explicitly_not_claimed_road_corridor():
    alerts=movement_alerts([point(240,45.001),point(120,45.015),point(1,45.04)],
                           {'lat':28.,'lng':45.},None,NOW)
    drift=next(a for a in alerts if a['kind']=='route_progress_deviation')
    assert drift['increase_m']>2000
    assert drift['basis']=='distance_to_next_stop_increasing_not_road_corridor'
    assert movement_alerts([point(240,45.04),point(1,45.001)],{'lat':28.,'lng':45.},None,NOW)==[]


def test_late_arrival_does_not_treat_old_uploaded_points_as_live():
    expected=NOW-timedelta(minutes=8)
    assert movement_alerts([point(600),point(500)],None,expected,NOW)==[]
    alerts=movement_alerts([point(50),point(1)],None,expected,NOW)
    assert [a['kind'] for a in alerts]==['arrival_delayed']
    assert alerts[0]['delay_seconds']==480
