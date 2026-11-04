"""Preserve the public FastAPI JSON contract without a second full traversal."""
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
import json
from uuid import UUID

from fastapi.encoders import jsonable_encoder
import pytest
from starlette.responses import JSONResponse

from app import responses


class State(Enum):
    READY = 'ready'


@pytest.mark.parametrize('payload', [
    {'order': {'id': 'TEST-order', 'created_at': datetime(2026, 10, 13, 12, 34, 56, 123, timezone.utc),
               'delivery_operational_date': date(2026, 10, 13), 'completed_at': None,
               'snapshot': {'items': [{'name': 'اختبار', 'price_halalas': 1250, 'units': [1, 2]}]},
               'active': True}, 'events': [{'created_at': datetime(2026, 10, 13)}]},
    {'values': [Decimal('1.25'), Decimal('2'), UUID('00000000-0000-0000-0000-000000000001'), State.READY]},
    {'quote': {'priced_at': '2026-10-13T15:34:56+03:00', 'price_halalas': 1250, 'address': 'الحي / الفرع'}},
])
def test_database_response_is_byte_identical_to_the_existing_fastapi_contract(payload):
    expected = JSONResponse(jsonable_encoder(payload))
    actual = responses.DatabaseJSONResponse(payload)
    assert actual.body == expected.body
    assert actual.headers == expected.headers
    assert actual.status_code == expected.status_code == 200


def test_only_non_json_database_scalars_use_the_conversion_hook(monkeypatch):
    calls = []
    encode = responses.jsonable_encoder

    def observed(value):
        calls.append(value)
        return encode(value)

    monkeypatch.setattr(responses, 'jsonable_encoder', observed)
    stamp = datetime(2026, 10, 13, tzinfo=timezone.utc)
    content = {'created_at': stamp, 'items': [{'name': 'TEST', 'price_halalas': 1250} for _ in range(34)]}
    result = responses.DatabaseJSONResponse(content)
    assert json.loads(result.body)['created_at'] == stamp.isoformat()
    assert calls == [stamp]


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_values_still_fail_closed(invalid):
    with pytest.raises(ValueError):
        responses.DatabaseJSONResponse({'invalid': invalid})
