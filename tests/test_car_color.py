"""Car color remains plain canonical data, including historical free text."""
from uuid import uuid4
from datetime import datetime, timezone
from copy import deepcopy
import pytest
from pydantic import ValidationError
from app import catalog, db
from app.branch_printing import receipt
from test_hardening_orders import checkout, payload

COLORS = ['أبيض','أسود','فضي','رمادي','رصاصي','بيج','ذهبي','بني',
          'أحمر','عنابي','أزرق','كحلي','أخضر','زيتي','أصفر','برتقالي']

@pytest.mark.parametrize('color', COLORS + ['لؤلؤي خاص', 'Pearl White / historical'])
def test_canonical_color_preserves_actual_text(color):
    car = catalog.Car.model_validate({'model':'Corolla','plate':'ABC 1234','color':'  '+color+'  '})
    assert car.color == color and car.model == 'Corolla' and car.plate == 'ABC 1234'

@pytest.mark.parametrize('color', ['', '   ', '\t\n', 'x'*41, 'red\x00'])
def test_invalid_color_rejected_on_server(color):
    with pytest.raises(ValidationError):
        catalog.Car.model_validate({'plate':'ABC 1234','color':color})

@pytest.mark.postgres
@pytest.mark.parametrize('color', COLORS + ['لؤلؤي خاص', 'Old free-text color'])
def test_each_color_persists_and_remains_readable(checkout, color):
    client, user = checkout
    # The existing fixture explicitly enables only its isolated synthetic brand.
    body = payload() | {'service':'car'}
    body['customer']['car'] = {'model':'Corolla','plate':'ABC 1234','color':color}
    response = client.post('/api/orders', json=body, headers={'Idempotency-Key':uuid4().hex})
    assert response.status_code == 200, response.text
    oid = response.json()['order']['id']
    with db.tx() as conn:
        saved = conn.execute('SELECT customer FROM orders WHERE id=%s', (oid,)).fetchone()
    assert saved['customer']['car'] == body['customer']['car']
    read = client.get('/api/orders/'+oid)
    assert read.status_code == 200
    assert read.json()['order']['customer']['car'] == body['customer']['car']


@pytest.mark.parametrize('language', ['ar', 'en'])
@pytest.mark.parametrize('kind', ['order', 'preparation'])
def test_print_records_preserve_exact_vehicle_and_exclude_other_services(language, kind):
    car = {'model': 'Corolla', 'plate': 'ABC 1234', 'color': 'Historical Pearl'}
    order = {'service': 'car', 'customer': {'car': car}, 'snapshot': {'items': []},
             'order_no': 42, 'created_at': datetime.now(timezone.utc),
             'payment_method': 'card', 'payment_status': 'unpaid'}
    original = deepcopy(order)
    result = receipt(order, {'name': 'Test branch'}, kind, language)
    values = [line['value'] for line in result['lines'] if 'value' in line]
    assert all(value in values for value in car.values())
    assert order == original
    for service in ['delivery', 'pickup']:
        result = receipt({**order, 'service': service}, {'name': 'Test branch'}, kind, language)
        assert not any(value in str(result) for value in car.values())
