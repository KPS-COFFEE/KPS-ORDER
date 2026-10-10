"""Canonical, server-priced catalog and explicitly configured branch availability."""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit, parse_qs, urlencode, unquote

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from . import auth, db

router = APIRouter(prefix='/api')
RIYADH = ZoneInfo('Asia/Riyadh')
DELIVERY_CITIES = {'حفر الباطن', 'الذيبية', 'القيصومة', 'الخفجي', 'رفحاء', 'قبه'}


def normalize_city(value: str) -> str:
    compact = re.sub(r'\s+', '', value or '')
    return {'حفرالباطن': 'حفر الباطن', 'رفحا': 'رفحاء', 'رفحاء': 'رفحاء', 'قبة': 'قبه'}.get(compact, (value or '').strip())


def _hours_open(hours, now=None) -> bool:
    if not isinstance(hours, list) or not hours:
        return False
    now = (now or datetime.now(RIYADH)).astimezone(RIYADH)
    minute = now.hour * 60 + now.minute
    for row in hours:
        if not isinstance(row, dict) or not isinstance(row.get('days'), list):
            continue
        try:
            start = _minute(row['start'])
            end = _minute(row['end'])
        except (KeyError, ValueError, TypeError):
            continue
        if start == end and now.weekday() in row['days']:
            return True  # Explicit equal endpoints mean a configured 24-hour day.
        if start < end and now.weekday() in row['days'] and start <= minute < end:
            return True
        if start > end:
            if now.weekday() in row['days'] and minute >= start:
                return True
            if (now - timedelta(days=1)).weekday() in row['days'] and minute < end:
                return True
    return False


def _minute(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise ValueError('Invalid hour')
    hour, minute = map(int, value.split(':'))
    return hour * 60 + minute


def branch_service(branch: dict, service: str, now=None, brand_settings=None) -> tuple[bool, str | None, str | None]:
    settings = branch['settings']
    service_key = 'car_pickup' if service == 'car' else service
    if (brand_settings or {}).get('services', {}).get(service_key, True) is not True:
        return False, 'BRAND_SERVICE_DISABLED', 'هذه الخدمة موقوفة من إدارة البراند.'
    if settings.get('active', True) is not True:
        return False, 'BRANCH_DISABLED', 'الفرع متوقف عن استقبال الطلبات الجديدة.'
    if service == 'delivery' and normalize_city(branch['city']) not in DELIVERY_CITIES:
        return False, 'DELIVERY_CITY_NOT_ELIGIBLE', 'التوصيل غير متاح من هذا الفرع. اختر الاستلام إن كان متاحًا.'
    if settings.get(service + '_enabled') is not True:
        return False, 'SERVICE_DISABLED', 'هذه الخدمة غير مفعلة لهذا الفرع.'
    if not (settings.get('operational_data_verified') is True or settings.get('ordering_verified') is True):
        return False, 'BRANCH_CONFIGURATION_UNVERIFIED', 'بيانات تشغيل هذا الفرع لم تُعتمد بعد.'
    if not settings.get('opening_hours'):
        return False, 'HOURS_UNCONFIGURED', 'ساعات استقبال هذا الفرع غير مهيأة.'
    if not _hours_open(settings['opening_hours'], now):
        return False, 'BRANCH_CLOSED', 'الفرع مغلق الآن لاستقبال الطلبات.'
    if not isinstance(settings.get('payment_methods'), list) or not settings['payment_methods']:
        return False, 'PAYMENT_UNCONFIGURED', 'طرق الدفع لهذا الفرع غير مهيأة.'
    if service == 'delivery':
        if not settings.get('delivery_hours'):
            return False, 'DELIVERY_HOURS_UNCONFIGURED', 'ساعات التوصيل لهذا الفرع غير مهيأة.'
        if not _hours_open(settings['delivery_hours'], now):
            return False, 'DELIVERY_CLOSED', 'التوصيل غير متاح الآن.'
        if not settings.get('coverage') or not location_verified(branch):
            return False, 'COVERAGE_UNCONFIGURED', 'نطاق التوصيل أو موقع الفرع غير مهيأ.'
        fee = settings.get('delivery_fee_halalas')
        if not isinstance(fee, int) or isinstance(fee, bool) or fee < 0:
            return False, 'DELIVERY_FEE_UNCONFIGURED', 'رسوم التوصيل لهذا الفرع غير مهيأة.'
    return True, None, None


def valid_location(location) -> bool:
    if not isinstance(location, dict):
        return False
    lat, lng = location.get('lat'), location.get('lng')
    if isinstance(lat, bool) or isinstance(lng, bool):
        return False
    return isinstance(lat, (int, float)) and isinstance(lng, (int, float)) and math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180


def covered(coverage: dict, location: dict) -> bool:
    """No implied city-wide delivery and no fabricated radius or branch coordinates."""
    if not isinstance(coverage, dict) or not valid_location(location):
        return False
    if coverage.get('type') == 'radius':
        center, radius = coverage.get('center'), coverage.get('radius_meters')
        if not valid_location(center) or not isinstance(radius, (int, float)) or isinstance(radius, bool) or not math.isfinite(radius) or radius <= 0:
            return False
        a, b = math.radians(location['lat']), math.radians(center['lat'])
        dl = math.radians(location['lng'] - center['lng'])
        distance = 2 * 6371000 * math.asin(min(1, math.sqrt(math.sin((a - b) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dl / 2) ** 2)))
        return distance <= radius
    if coverage.get('type') != 'polygon':
        return False
    points = coverage.get('coordinates')
    if not isinstance(points, list) or not 3 <= len(points) <= 1000:
        return False
    if any(not isinstance(p, list) or len(p) != 2 or not valid_location({'lng': p[0], 'lat': p[1]}) for p in points):
        return False
    x, y, inside = location['lng'], location['lat'], False
    for index, (a, b) in enumerate(points):
        c, d = points[(index + 1) % len(points)]
        # Include an address precisely on the approved boundary.
        cross = (x - a) * (d - b) - (y - b) * (c - a)
        if abs(cross) < 1e-10 and min(a, c) <= x <= max(a, c) and min(b, d) <= y <= max(b, d):
            return True
        if (b > y) != (d > y) and x < (c - a) * (y - b) / (d - b) + a:
            inside = not inside
    return inside


def product_availability(product: dict, branch: dict | None) -> tuple[bool, str | None]:
    settings = product['settings']
    if settings.get('lifecycle') in {'draft', 'paused'} or settings.get('enabled', True) is not True:
        return False, 'الصنف غير متاح حاليًا.'
    cities = settings.get('available_cities') or []
    # Additional hard guard preserves the explicitly identified original two source records.
    if product['brand_id'] == 'kps' and product['id'] in {'kps-33', 'kps-34'} and not settings.get('availability_policy_version'):
        cities = ['حفر الباطن']
    if cities and not branch:
        return False, 'حدد فرع التنفيذ أولًا للتحقق من توفر الصنف.'
    if branch:
        if branch['brand_id'] != product['brand_id']:
            return False, 'الصنف لا يتبع براند الفرع.'
        if cities and normalize_city(branch['city']) not in {normalize_city(city) for city in cities}:
            return False, 'متوفر في حفر الباطن فقط' if {normalize_city(c) for c in cities} == {'حفر الباطن'} else 'الصنف غير متاح في مدينة الفرع.'
        branch_ids = settings.get('available_branch_ids')
        if branch_ids is not None and branch['id'] not in branch_ids:
            return False, 'الصنف غير متاح في هذا الفرع.'
        if branch['id'] in (settings.get('unavailable_branch_ids') or []):
            return False, 'الصنف موقوف مؤقتًا في هذا الفرع.'
        overrides = branch['settings'].get('product_availability') or {}
        if overrides.get(product['id']) is False:
            return False, 'الصنف موقوف مؤقتًا في هذا الفرع.'
    return True, None


def public_product(product: dict, branch=None) -> dict:
    settings = product['settings']
    available, reason = product_availability(product, branch)
    result = {key: product[key] for key in ('id', 'brand_id', 'name', 'category', 'price_halalas')}
    result.update({key: settings.get(key) for key in ('image_url', 'description')})
    result.update(option_groups=settings.get('option_groups', []), variants=settings.get('variants', []),
                  available_cities=settings.get('available_cities', []), enabled=settings.get('enabled', True),
                  source_verified=settings.get('source_verified') is True, lifecycle=settings.get('lifecycle', 'active' if settings.get('enabled', True) else 'paused'), available=available, unavailable_reason=reason)
    return result


@router.get('/catalog')
def catalog(brand_id: str = Query(default='kps', max_length=80), branch_id: str | None = Query(default=None, max_length=80)):
    with db.tx() as conn:
        brand = conn.execute('SELECT id,name,settings FROM brands WHERE id=%s AND active', (brand_id,)).fetchone()
        if not brand:
            raise db.error(404, 'BRAND_NOT_FOUND', 'البراند غير متاح.')
        branch = None
        if branch_id:
            branch = conn.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s', (branch_id, brand_id)).fetchone()
            if not branch:
                raise db.error(404, 'BRANCH_NOT_FOUND', 'الفرع غير موجود.')
        products = conn.execute("SELECT * FROM products WHERE brand_id=%s AND coalesce(settings->>'lifecycle','active')<>'draft' ORDER BY id", (brand_id,)).fetchall()
        preferences = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s', (brand_id,)).fetchone()
    safe_brand = {'id': brand['id'], 'name': brand['name'], 'logo_url': brand['settings'].get('logo_url', '/assets/kps-logo-fb911a7b4bb4.png' if brand_id == 'kps' else None)}
    safe_brand['theme'] = {key: value for key, value in ((preferences['settings'] if preferences else {}).get('theme') or {}).items()
                           if key in {'accent', 'background', 'text', 'font'}}
    return {'brand': safe_brand, 'products': [public_product(p, branch) for p in products],
            'source_verified': bool(products) and all(p['settings'].get('source_verified') is True for p in products)}


@router.get('/branches')
def branches(brand_id: str = Query(default='kps', max_length=80)):
    with db.tx() as conn:
        if not conn.execute('SELECT 1 FROM brands WHERE id=%s AND active', (brand_id,)).fetchone():
            raise db.error(404, 'BRAND_NOT_FOUND', 'البراند غير متاح.')
        brand_settings = conn.execute('SELECT settings FROM brands WHERE id=%s', (brand_id,)).fetchone()['settings']
        rows = conn.execute('SELECT * FROM branches WHERE brand_id=%s ORDER BY branch_no', (brand_id,)).fetchall()
    result = []
    for branch in rows:
        item = {key: branch[key] for key in ('id', 'brand_id', 'branch_no', 'name', 'city')}
        item['location'] = branch['settings'].get('location') if location_verified(branch) else None
        item['directions_url'] = directions_url(branch)
        item['location_verified'] = location_verified(branch)
        item['payment_methods'] = branch['settings'].get('payment_methods') or []
        item['delivery_eligible'] = normalize_city(branch['city']) in DELIVERY_CITIES
        item['services'] = {}
        for service in ('pickup', 'car', 'delivery'):
            available, code, reason = branch_service(branch, service, brand_settings=brand_settings)
            item['services'][service] = {'available': available, 'code': code, 'reason': reason}
        result.append(item)
    return {'branches': result}


@router.get('/products/{product_id}/availability')
def availability(product_id: str, branch_id: str):
    with db.tx() as conn:
        product = conn.execute('SELECT * FROM products WHERE id=%s', (product_id,)).fetchone()
        branch = conn.execute('SELECT * FROM branches WHERE id=%s', (branch_id,)).fetchone()
        if not product or not branch or product['brand_id'] != branch['brand_id']:
            raise db.error(404, 'PRODUCT_NOT_FOUND', 'الصنف أو الفرع غير موجود.')
        available, reason = product_availability(product, branch)
        return {'available': available, 'reason': reason, 'source_verified': product['settings'].get('source_verified') is True}


class Location(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)


class Car(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plate: str = Field(min_length=1, max_length=30)
    color: str = Field(min_length=1, max_length=40)
    model: str = Field(default='', max_length=60)


class CustomerInfo(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, min_length=8, max_length=30)
    neighborhood: str = Field(default='', max_length=120)
    address: str = Field(default='', max_length=500)
    location: Location | None = None
    car: Car | None = None


class Unit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    options: dict[str, str | list[str]] = Field(default_factory=dict, max_length=15)


class OrderItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product_id: str = Field(min_length=1, max_length=80)
    quantity: StrictInt = Field(ge=1, le=50)
    units: list[Unit] | None = Field(default=None, max_length=50)
    note: str = Field(default='', max_length=300)


class QuoteBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    brand_id: str = Field(default='kps', min_length=1, max_length=80)
    branch_id: str = Field(min_length=1, max_length=80)
    service: Literal['pickup', 'car', 'delivery']
    payment_method: Literal['cash', 'card']
    items: list[OrderItem] = Field(min_length=1, max_length=50)
    customer: CustomerInfo
    notes: str = Field(default='', max_length=1000)
    expected_total_halalas: StrictInt | None = Field(default=None, ge=0, le=10000000)


def _price_units(product: dict, item: OrderItem) -> tuple[int, list]:
    groups = product['settings'].get('option_groups') or []
    if not isinstance(groups, list) or any(not isinstance(group, dict) for group in groups):
        raise db.error(409, 'PRODUCT_OPTIONS_UNCONFIGURED', 'خيارات هذا الصنف بحاجة إلى مراجعة الإدارة.')
    if item.units is None:
        if groups:
            raise db.error(422, 'UNIT_OPTIONS_REQUIRED', 'حدد الخيارات لكل وحدة قبل إضافة الصنف.')
        units = [Unit() for _ in range(item.quantity)]
    else:
        units = item.units
        if len(units) != item.quantity:
            raise db.error(422, 'UNIT_OPTIONS_COUNT', 'عدد اختيارات الوحدات لا يطابق الكمية.')
    known_groups = {str(group.get('code') or group.get('id')): group for group in groups}
    if len(known_groups) != len(groups):
        raise db.error(409, 'PRODUCT_OPTIONS_UNCONFIGURED', 'خيارات هذا الصنف بحاجة إلى مراجعة الإدارة.')
    snapshots, total = [], 0
    for unit in units:
        if set(unit.options) - set(known_groups):
            raise db.error(422, 'UNKNOWN_OPTION_GROUP', 'تم إرسال مجموعة خيارات غير معتمدة.')
        unit_price = product['price_halalas']
        options_snapshot = {}
        for code, group in known_groups.items():
            rows = group.get('values', [])
            if not isinstance(rows, list) or any(not isinstance(value, dict) for value in rows):
                raise db.error(409, 'PRODUCT_OPTIONS_UNCONFIGURED', 'خيارات هذا الصنف بحاجة إلى مراجعة الإدارة.')
            values = {str(v.get('code') or v.get('id')): v for v in rows}
            if len(values) != len(rows) or any('name' not in row for row in rows):
                raise db.error(409, 'PRODUCT_OPTIONS_UNCONFIGURED', 'خيارات هذا الصنف بحاجة إلى مراجعة الإدارة.')
            selected = unit.options.get(code, [])
            selected = [selected] if isinstance(selected, str) else selected
            minimum = group.get('min_select', 1 if group.get('required', True) else 0)
            maximum = group.get('max_select', 1)
            if type(minimum) is not int or type(maximum) is not int or not 0 <= minimum <= maximum <= 15:
                raise db.error(409, 'PRODUCT_OPTIONS_UNCONFIGURED', 'خيارات هذا الصنف بحاجة إلى مراجعة الإدارة.')
            if not minimum <= len(selected) <= maximum or len(set(selected)) != len(selected):
                label = 'اللون' if group.get('type') == 'color' or code == 'COLOR' else 'الخيار'
                raise db.error(422, 'OPTION_SELECTION_INVALID', f'حدد {label} لكل وحدة بصورة صحيحة.')
            choices = []
            for selected_code in selected:
                value = values.get(selected_code)
                if not value or value.get('enabled', True) is not True:
                    raise db.error(422, 'OPTION_VALUE_INVALID', 'الخيار المحدد غير متاح أو غير معتمد.')
                delta = value.get('price_delta_halalas', 0)
                if not isinstance(delta, int) or isinstance(delta, bool):
                    raise db.error(409, 'OPTION_PRICE_UNCONFIGURED', 'سعر الخيار غير مهيأ.')
                unit_price += delta
                choices.append({'code': selected_code, 'name': value['name'], 'price_delta_halalas': delta})
            options_snapshot[code] = {'name': group.get('name', code), 'choices': choices}
        if unit_price < 0:
            raise db.error(409, 'PRODUCT_PRICE_INVALID', 'سعر الصنف غير صالح.')
        snapshots.append({'options': unit.options, 'option_snapshot': options_snapshot, 'price_halalas': unit_price})
        total += unit_price
    return total, snapshots


def quote_order(conn, body: QuoteBody, for_update=False) -> dict:
    lock = ' FOR SHARE' if for_update else ''
    brand = conn.execute('SELECT id,settings FROM brands WHERE id=%s AND active' + lock, (body.brand_id,)).fetchone()
    branch = conn.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s' + lock, (body.branch_id, body.brand_id)).fetchone()
    if not brand or not branch:
        raise db.error(404, 'BRANCH_NOT_FOUND', 'الفرع أو البراند غير موجود.')
    available, code, reason = branch_service(branch, body.service, brand_settings=brand['settings'])
    if not available:
        raise db.error(409, code, reason)
    if body.payment_method not in branch['settings']['payment_methods']:
        raise db.error(409, 'PAYMENT_METHOD_UNAVAILABLE', 'طريقة الدفع غير متاحة لهذا الفرع.')
    if body.service == 'delivery':
        if not body.customer.location or not body.customer.address.strip():
            raise db.error(422, 'DELIVERY_ADDRESS_REQUIRED', 'حدد موقع وعنوان التوصيل.')
        if not covered(branch['settings']['coverage'], body.customer.location.model_dump()):
            raise db.error(409, 'OUTSIDE_COVERAGE', 'العنوان خارج نطاق توصيل هذا الفرع.')
    if body.service == 'car' and not body.customer.car:
        raise db.error(422, 'CAR_DETAILS_REQUIRED', 'أدخل بيانات السيارة قبل تأكيد الطلب.')
    snapshots, subtotal = [], 0
    if sum(item.quantity for item in body.items) > 100:
        raise db.error(422, 'ORDER_QUANTITY_LIMIT', 'الحد الأقصى مئة وحدة في الطلب.')
    for item in body.items:
        product = conn.execute('SELECT * FROM products WHERE id=%s AND brand_id=%s' + lock, (item.product_id, body.brand_id)).fetchone()
        if not product:
            raise db.error(422, 'PRODUCT_NOT_FOUND', 'أحد الأصناف لا يتبع هذا البراند أو غير موجود.')
        ok, reason = product_availability(product, branch)
        if not ok:
            raise db.error(409, 'PRODUCT_UNAVAILABLE', reason)
        if product['settings'].get('source_verified') is not True:
            raise db.error(409, 'CATALOG_SOURCE_UNVERIFIED', 'السعر وخيارات هذا الصنف لم تُعتمد من المصدر التشغيلي بعد.')
        amount, units = _price_units(product, item)
        subtotal += amount
        snapshots.append({'product_id': product['id'], 'source_id': product['settings'].get('source_id', product['id']),
                          'name': product['name'], 'category': product['category'], 'quantity': item.quantity,
                          'unit_price_halalas': product['price_halalas'], 'line_total_halalas': amount,
                          'units': units, 'note': item.note})
    minimum = branch['settings'].get('minimum_order_halalas')
    if body.service == 'delivery' and isinstance(minimum, int) and subtotal < minimum:
        raise db.error(409, 'MINIMUM_ORDER_NOT_MET', 'السلة أقل من الحد الأدنى المعتمد للتوصيل.')
    fee = branch['settings']['delivery_fee_halalas'] if body.service == 'delivery' else 0
    if subtotal + fee > 10000000:
        raise db.error(422, 'ORDER_VALUE_LIMIT', 'قيمة الطلب أعلى من الحد المسموح للموقع.')
    return {'brand_id': body.brand_id, 'branch_id': body.branch_id, 'branch_name': branch['name'],
            'branch_city': branch['city'], 'branch_location': branch['settings'].get('location'),
            'service': body.service, 'payment_method': body.payment_method, 'items': snapshots,
            'subtotal_halalas': subtotal, 'delivery_fee_halalas': fee, 'total_halalas': subtotal + fee,
            'discount_halalas': 0, 'tax': {'status': 'not_configured', 'amount_halalas': None},
            'notes': body.notes, 'currency': 'SAR', 'priced_at': datetime.now(RIYADH).isoformat()}


@router.post('/quote')
def quote(body: QuoteBody):
    with db.tx() as conn:
        return {'quote': quote_order(conn, body)}


class AvailabilityBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    available: bool
    reason: str = Field(default='', max_length=300)


@router.post('/branches/{branch_id}/products/{product_id}/availability')
def branch_product_availability(branch_id: str, product_id: str, body: AvailabilityBody,
                                user=Depends(auth.require_roles('owner', 'branch_manager')),
                                idempotency_key: str | None = Header(default=None)):
    if user['role'] != 'owner' and branch_id not in user['branch_ids']:
        raise db.error(404, 'BRANCH_NOT_FOUND', 'الفرع غير موجود ضمن صلاحياتك.')
    with db.tx() as conn:
        def perform():
            branch = conn.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s', (branch_id, user['brand_id'])).fetchone()
            product = conn.execute('SELECT * FROM products WHERE id=%s AND brand_id=%s FOR UPDATE', (product_id, user['brand_id'])).fetchone()
            if not branch or not product:
                raise db.error(404, 'PRODUCT_NOT_FOUND', 'الصنف أو الفرع غير موجود.')
            settings = product['settings']
            blocked = set(settings.get('unavailable_branch_ids') or [])
            if body.available:
                blocked.discard(branch_id)
            else:
                blocked.add(branch_id)
            settings['unavailable_branch_ids'] = sorted(blocked)
            conn.execute('UPDATE products SET settings=%s,updated_at=now() WHERE id=%s', (db.json(settings), product_id))
            db.audit(conn, user['id'], 'product.branch_availability', 'product', product_id,
                     {'branch_id': branch_id, 'available': body.available, 'reason': body.reason})
            # Enabling temporary stock never overrides permanent city restrictions.
            ok, reason = product_availability(dict(product, settings=settings), branch)
            return {'product_id': product_id, 'branch_id': branch_id, 'available': ok, 'reason': reason}
        return db.idempotent(conn, user['id'], idempotency_key, 'branch.product_availability:' + branch_id + ':' + product_id, body.model_dump(), perform)


MAP_HOSTS = {'www.google.com', 'google.com', 'maps.google.com', 'maps.app.goo.gl', 'goo.gl'}
DISTANCE_LABEL = 'مسافة مباشرة تقريبية، وليست مسافة الطريق'


def safe_map_url(value):
    """Validate only. Never resolve a pasted URL or contact an arbitrary host."""
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or parsed.hostname not in MAP_HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
            return None
        path = unquote(parsed.path)
        if any(segment in {'.','..'} for segment in path.split('/')) or '\\' in path:
            return None
        if parsed.hostname == 'maps.app.goo.gl':
            return value if re.fullmatch(r'/[A-Za-z0-9_-]+/?', path) else None
        if parsed.hostname == 'goo.gl':
            return value if re.fullmatch(r'/maps/[A-Za-z0-9_-]+/?', path) else None
        if not re.match(r'^/maps(?:/|$)', path) and not (parsed.hostname=='maps.google.com' and path in {'','/'}):
            return None
        return value
    except ValueError:
        return None


def location_verified(branch):
    settings = branch['settings']
    verified = settings.get('location_verified') is True if 'location_verified' in settings else (
           settings.get('operational_data_verified') is True or settings.get('ordering_verified') is True)
    return valid_location(settings.get('location')) and verified


def directions_url(branch):
    if location_verified(branch):
        point = branch['settings']['location']
        return 'https://www.google.com/maps/dir/?' + urlencode({'api': '1', 'destination': f"{point['lat']},{point['lng']}", 'travelmode': 'driving'})
    settings = branch['settings']
    return safe_map_url(settings.get('map_url')) if settings.get('map_url_verified') is True else None


def direct_distance(a, b):
    if not valid_location(a) or not valid_location(b):
        return None
    lat1, lat2 = math.radians(a['lat']), math.radians(b['lat'])
    dl = math.radians(b['lng']-a['lng'])
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dl/2)**2
    return 2*6371000*math.asin(min(1, math.sqrt(h)))


class MapURLBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1, max_length=2000)


@router.post('/location/parse-map-url')
def parse_map_url(body: MapURLBody):
    value = safe_map_url(body.url)
    if not value:
        raise db.error(422, 'MAP_URL_NOT_ALLOWED', 'اختر دبوسًا أو رابط Google Maps معتمدًا.')
    parsed = urlsplit(value)
    query = parse_qs(parsed.query)
    candidate = (query.get('destination') or query.get('query') or query.get('q') or [None])[0]
    if candidate is None:
        # @lat,lng is merely a viewport center, not a confirmed destination pin.
        match = re.search(r'!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)', parsed.path)
        if match:
            candidate = match.group(1)+','+match.group(2)
    if not candidate or not re.fullmatch(r'-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?', candidate):
        raise db.error(422, 'MAP_PIN_REQUIRED', 'تعذر استخراج دبوس موثوق من الرابط دون فتحه. حدد الموقع مباشرة.')
    lat, lng = map(float, candidate.split(','))
    point = {'lat': lat, 'lng': lng}
    if not valid_location(point):
        raise db.error(422, 'COORDINATE_INVALID', 'الموقع غير صحيح.')
    return {'location': point, 'source': 'customer_selected', 'location_is_device_verified': False}


class NearestBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    location: Location
    product_ids: list[str] = Field(default_factory=list, max_length=50)


@router.post('/operations/nearest-branches')
def nearest_branches(body: NearestBody, user=Depends(auth.require_roles('owner', 'delivery_manager', 'delivery_staff'))):
    point = body.location.model_dump()
    with db.tx() as conn:
        brand = conn.execute('SELECT settings FROM brands WHERE id=%s AND active', (user['brand_id'],)).fetchone()
        if not brand:
            raise db.error(404, 'BRAND_NOT_FOUND', 'البراند غير موجود.')
        rows = conn.execute('SELECT * FROM branches WHERE brand_id=%s ORDER BY branch_no', (user['brand_id'],)).fetchall()
        products = conn.execute('SELECT * FROM products WHERE brand_id=%s AND id=ANY(%s)', (user['brand_id'], body.product_ids)).fetchall() if body.product_ids else []
        if set(body.product_ids) != {row['id'] for row in products}:
            raise db.error(422, 'PRODUCT_NOT_FOUND', 'صنف خارج البراند أو غير موجود.')
    eligible, excluded = [], []
    for branch in rows:
        if user['role'] != 'owner' and branch['id'] not in user['branch_ids']:
            continue
        available, code, reason = branch_service(branch, 'delivery', brand_settings=brand['settings'])
        if available and not location_verified(branch):
            available, code, reason = False, 'BRANCH_LOCATION_UNVERIFIED', 'دبوس الفرع غير معتمد.'
        if available and not covered(branch['settings']['coverage'], point):
            available, code, reason = False, 'OUTSIDE_COVERAGE', 'العنوان خارج نطاق خدمة الفرع.'
        missing = []
        for product in products:
            ok, why = product_availability(product, branch)
            if not ok or product['settings'].get('source_verified') is not True:
                missing.append({'product_id': product['id'], 'reason': why or 'بيانات الصنف لم تعتمد.'})
        if available and missing:
            available, code, reason = False, 'PRODUCT_UNAVAILABLE', 'بعض أصناف السلة غير متاحة في الفرع.'
        item = {key: branch[key] for key in ('id', 'branch_no', 'name', 'city')}
        item.update(available=available, code=code, reason=reason, unavailable_products=missing,
                    directions_url=directions_url(branch), distance_label=DISTANCE_LABEL,
                    delivery_fee_halalas=branch['settings'].get('delivery_fee_halalas'),
                    location_verified=location_verified(branch), covered=covered(branch['settings'].get('coverage'),point),
                    services={service:{'available':state[0],'code':state[1],'reason':state[2]}
                              for service in ('pickup','car','delivery')
                              for state in [branch_service(branch,service,brand_settings=brand['settings'])]})
        if available:
            item['direct_distance_meters'] = round(direct_distance(point, branch['settings']['location']), 1)
            eligible.append(item)
        else:
            item['direct_distance_meters'] = None
            excluded.append(item)
    eligible.sort(key=lambda row: (row['direct_distance_meters'], row['branch_no']))
    return {'branches': eligible, 'excluded': excluded, 'distance_label': DISTANCE_LABEL,
            'location_source': 'customer_selected', 'routing_provider': None, 'automatically_changes_order': False}
