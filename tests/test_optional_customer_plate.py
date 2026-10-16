"""Optional customer plates do not change staff fleet identity or stored history."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from pydantic import ValidationError
from app import catalog, db
from app.branch_printing import receipt
from test_hardening_orders import checkout, payload

CASES = [('', ''), ('   ', ''), ('123', '123'), ('0123456', '0123456'),
         ('١٢٣٤٥٦', '123456'), ('۰۱۲۳۴۵', '012345'), ('١2۳٤', '1234'),
         ('ABC 1234', 'ABC 1234'), ('ح م د ١٢٣٤', 'ح م د 1234'),
         ('Dubai A / ١٢٣٤٥', 'Dubai A / 12345'), ('12-34567', '12-34567')]

@pytest.mark.parametrize('entered,expected', CASES)
def test_plate_optional_and_flexible(entered, expected):
    assert catalog.Car(color='Pearl', model='Corolla', plate=entered).plate == expected

def test_plate_can_be_omitted():
    assert catalog.Car(color='أسود', model='Camry').plate == ''

@pytest.mark.parametrize('plate', ['x'*31, '<script>', '123\x00', '123\n456',
                                    '123\u202e', '😀', 'a\\b', '123\t4'])
def test_plate_input_limits_and_controls(plate):
    with pytest.raises(ValidationError):
        catalog.Car(color='Black', plate=plate)

@pytest.mark.postgres
@pytest.mark.parametrize('entered,expected', CASES + [(None, '')])
def test_quote_and_order_persist_optional_plate_and_replay(checkout, entered, expected):
    client, user = checkout
    body = payload() | {'service':'car'}
    body['customer']['car'] = {'model':'Corolla','color':'لؤلؤي خاص'}
    if entered is not None: body['customer']['car']['plate'] = entered
    quote = client.post('/api/quote', json=body)
    assert quote.status_code == 200, quote.text
    key = uuid4().hex
    response = client.post('/api/orders',json=body,headers={'Idempotency-Key':key})
    assert response.status_code == 200, response.text
    oid = response.json()['order']['id']
    replay = client.post('/api/orders',json=body,headers={'Idempotency-Key':key})
    assert replay.status_code == 200 and replay.json()['order']['id'] == oid
    read = client.get('/api/orders/'+oid).json()['order']
    assert read['customer']['car'] == {'model':'Corolla','color':'لؤلؤي خاص','plate':expected}
    # Reading a historical plate must not normalize or rewrite its saved text.
    historical = {'model':'Old','color':'Historical Pearl','plate':'١٢٣٤٥ / A'}
    with db.tx() as c:
        c.execute('UPDATE orders SET customer=%s WHERE id=%s',
                  (db.json({**read['customer'],'car':historical}),oid))
    assert client.get('/api/orders/'+oid).json()['order']['customer']['car'] == historical
    with db.tx() as c:
        assert c.execute('SELECT customer FROM orders WHERE id=%s',(oid,)).fetchone()['customer']['car'] == historical

@pytest.mark.parametrize('language', ['ar','en'])
@pytest.mark.parametrize('kind', ['order','preparation'])
def test_missing_plate_prints_informational_text_without_changing_history(language,kind):
    order = {'service':'car','customer':{'car':{'model':'Corolla','color':'Pearl'}},
             'snapshot':{'items':[]},'order_no':42,'created_at':datetime.now(timezone.utc),
             'payment_method':'card','payment_status':'unpaid'}
    original=deepcopy(order)
    lines=receipt(order,{'name':'Test'},kind,language)['lines']
    values=[line['value'] for line in lines if 'value' in line]
    assert ('لم يُدخل رقم اللوحة' if language=='ar' else 'No plate entered') in values
    assert order == original
