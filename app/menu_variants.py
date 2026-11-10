"""Explicit Enbishan size/flavour prices; legacy KPS pricing is unchanged."""
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from . import db


class VariantPrice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    options: dict[str, str]
    price_halalas: StrictInt = Field(ge=1, le=10000000)
    compare_at_halalas: StrictInt | None = Field(default=None, ge=1, le=10000000)

    @model_validator(mode='after')
    def valid_reference_price(self):
        if self.compare_at_halalas is not None and self.compare_at_halalas <= self.price_halalas:
            raise ValueError('The before price must exceed the selling price.')
        return self


def validated(product):
    raw = product.get('settings', {}).get('variant_price_matrix')
    if raw is None:
        return None
    if product.get('brand_id') != 'enbishan' or not isinstance(raw, list) or not 1 <= len(raw) <= 1024:
        raise ValueError('An explicit bounded Enbishan variant matrix is required.')
    groups = product['settings'].get('option_groups') or []
    choices = {}
    for group in groups:
        if (not isinstance(group, dict) or group.get('min_select', 1) != 1
                or group.get('max_select', 1) != 1 or group.get('required', True) is not True):
            raise ValueError('Priced variant groups require exactly one choice.')
        code = group.get('code')
        if not isinstance(code, str) or not code or code in choices:
            raise ValueError('Variant group codes must be unique.')
        values = group.get('values') or []
        codes = [v.get('code') for v in values]
        if not codes or len(codes) != len(set(codes)) or any(not isinstance(c, str) or not c for c in codes):
            raise ValueError('Variant option codes must be unique.')
        if any(v.get('price_delta_halalas', 0) != 0 for v in values):
            raise ValueError('Use one final price per combination; additive deltas cannot be combined with it.')
        choices[code] = set(codes)
    rows = [VariantPrice.model_validate(row) for row in raw]
    seen = set()
    for row in rows:
        signature = tuple(sorted(row.options.items()))
        if (set(row.options) != set(choices) or signature in seen
                or any(value not in choices[code] for code, value in row.options.items())):
            raise ValueError('Duplicate or unknown variant combination.')
        seen.add(signature)
    if type(product.get('price_halalas')) is not int or product['price_halalas'] != min(r.price_halalas for r in rows):
        raise ValueError('The displayed starting price must equal the lowest variant price.')
    return rows


def selected_price(product, options):
    try:
        rows = validated(product)
    except (ValueError, TypeError, KeyError):
        raise db.error(409, 'PRODUCT_VARIANTS_UNCONFIGURED', 'أسعار أحجام ونكهات الصنف بحاجة إلى مراجعة الإدارة.') from None
    if rows is None:
        return None
    selected = {key: value[0] if isinstance(value, list) and len(value) == 1 else value
                for key, value in options.items()}
    row = next((r for r in rows if r.options == selected), None)
    if row is None:
        raise db.error(409, 'PRODUCT_VARIANT_UNAVAILABLE', 'هذا الحجم والنكهة غير متاحين معًا؛ اختر تركيبًا متاحًا.')
    return row.model_dump(exclude_none=True)
