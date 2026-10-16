"""Car color remains plain canonical data, including historical free text."""
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app import catalog, db
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
def test_each_color_persists_and_remains_readable(checkout):
    client, user = checkout
    # The existing fixture explicitly enables only its isolated synthetic brand.
    for color in COLORS + ['لؤلؤي خاص', 'Old free-text color']:
        body = payload() | {'service':'car'}
        body['customer']['car'] = {'model':'Corolla','plate':'ABC 1234','color':color}
        response = client.post('/api/orders', json=body, headers={'Idempotency-Key':uuid4().hex})
        assert response.status_code == 200, response.text
        oid = response.json()['id']
        with db.tx() as conn:
            saved = conn.execute('SELECT customer FROM orders WHERE id=%s', (oid,)).fetchone()
        assert saved['customer']['car'] == body['customer']['car']
        assert client.get('/api/orders/'+oid).status_code == 200
