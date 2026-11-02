"""Verified storefront pricing retains the principal across the gift quote route."""
from contextlib import nullcontext
from copy import deepcopy
import pytest
from app import auth, catalog, customer_benefits as benefits, db, first_order_gift
from test_customer_engagement_closure import rule
from tests.test_core import _body


@pytest.mark.parametrize('brand', ['kps', 'enbishan'])
def test_verified_gift_quote_applies_real_customer_offer_without_reserving(brand, monkeypatch):
    principal = {'id': 'SYNTHETIC-' + brand, 'role': 'customer', 'brand_id': brand}
    definition = benefits.Rule(**rule()).model_dump(mode='json')
    calls = []
    class Rows:
        def __init__(self, value): self.value = value
        def fetchone(self): return self.value
        def fetchall(self): return self.value
    class Connection:
        def execute(self, sql, args=None):
            calls.append((sql, args))
            assert sql.startswith('SELECT '), 'A quote cannot reserve or write financial state'
            if 'FROM app_settings' in sql: return Rows({'settings': {}})
            if 'FROM customer_wallets' in sql: return Rows({'balance_halalas': 0})
            if 'FROM benefit_programs' in sql: return Rows(None)
            if 'FROM benefit_rules' in sql:
                assert args == (brand,)
                return Rows([{'id': 'SYNTHETIC-offer', 'code': 'SYNTHETIC', 'definition': definition}])
            if 'FROM orders' in sql: return Rows({'n': 0})
            if 'FROM benefit_order_states' in sql: return Rows({'customer': 0, 'total': 0})
            pytest.fail('Unexpected query: ' + sql)
    conn = Connection()
    monkeypatch.setattr(db, 'tx', lambda **kwargs: nullcontext(conn))
    monkeypatch.setattr(benefits.activation, 'enabled', lambda feature, scope=None: feature == 'customer_benefits')
    verified = []
    monkeypatch.setattr(auth, 'assert_customer_order_eligible', lambda c, user, **kwargs: verified.append(user))
    monkeypatch.setattr(first_order_gift, 'prepare', lambda *args: None)
    base = {'brand_id': brand, 'subtotal_halalas': 1000, 'delivery_fee_halalas': 500,
            'discount_halalas': 0, 'total_halalas': 1500,
            'items': [{'product_id': 'coffee', 'unit_price_halalas': 1000, 'quantity': 1, 'line_total_halalas': 1000}]}
    def priced(c, body, **kwargs):
        assert kwargs.get('reserve_order_id') is None and kwargs.get('order_id') is None
        return benefits.apply(c, body, deepcopy(base), kwargs.get('user'))
    monkeypatch.setattr(catalog, 'quote_order', priced)
    body = catalog.QuoteBody.model_validate({**_body(product='coffee'), 'brand_id': brand,
                                             'benefit_code': 'SYNTHETIC'})
    result = first_order_gift.gift_quote(body, user=principal)['quote']
    assert result['discount_halalas'] == 500 and result['total_halalas'] == 1000
    assert result['benefits']['rule_id'] == 'SYNTHETIC-offer'
    assert len(verified) == 2 and all(user is principal for user in verified)
    assert not any('FOR UPDATE' in sql for sql, _ in calls)
