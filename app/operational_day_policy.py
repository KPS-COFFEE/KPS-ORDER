"""Morning-first presentation without rewriting closed financial evidence."""
from copy import deepcopy


def present_dates(value):
    if isinstance(value, list):
        return [present_dates(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: present_dates(item) for key, item in value.items()}
    if result.get('reporting_operational_date') and result.get('kind') in ('driver', 'manager'):
        result['original_operational_date'] = result.get('original_operational_date', result.get('delivery_operational_date'))
        result['delivery_operational_date'] = result['reporting_operational_date']
    return result


def report_snapshot(c, snapshot):
    """Call after report scope authorization; only the date label is projected."""
    shift = snapshot.get('shift') or {}
    if not shift.get('id') or not shift.get('brand_id'):
        return snapshot
    row = c.execute('SELECT reporting_operational_date FROM shifts WHERE id=%s AND brand_id=%s',
                    (shift['id'], shift['brand_id'])).fetchone()
    if not row or not row['reporting_operational_date']:
        return snapshot
    day = row['reporting_operational_date'].isoformat()
    if day == (shift.get('delivery_operational_date') or snapshot.get('operational_date')):
        return snapshot
    result = deepcopy(snapshot)
    result['original_operational_date'] = snapshot.get('operational_date')
    result['operational_date'] = day
    result['shift']['original_operational_date'] = shift.get('delivery_operational_date')
    result['shift']['delivery_operational_date'] = day
    result['operational_date_correction'] = {
        'decision': 'owner-morning-then-evening-20261025',
        'original_day': snapshot.get('operational_date'), 'corrected_day': day,
    }
    return result
