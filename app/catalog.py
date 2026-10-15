"""Canonical, server-priced catalog and explicitly configured branch availability."""
from __future__ import annotations

import math
import hashlib
import json
import re
import ipaddress
import unicodedata
from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit, parse_qs, urlencode, unquote, urljoin

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from .responses import DatabaseJSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from . import auth, db, service_hours
from .service_hours import closed_delivery_message, hours_status

router = APIRouter(prefix='/api')
RIYADH = ZoneInfo('Asia/Riyadh')
DELIVERY_CITIES = {'حفر الباطن', 'الذيبية', 'القيصومة', 'الخفجي', 'رفحاء', 'قبه'}


# This owner decision applies once to the exact imported menu, never future edits.
FINAL_MENU_APPROVAL_ID = 'owner-final-2026-10-10'
FINAL_MENU_MANIFEST_SHA256 = '57d67963b0ac752a179ffe60ce25f81fe06ab1b6c19a161769bd39fd4cd870f6'
KHALIYA_SOURCES = {'classic': 'kps-34', 'pecan': 'kps-33'}


def is_khaliya(product):
    return product['brand_id'] == 'kps' and product['id'] in KHALIYA_SOURCES.values()


def khaliya_branch(conn, lock=''):
    # Canonical branch number plus brand/city; never trust a customer-supplied target.
    return conn.execute("SELECT * FROM branches WHERE brand_id='kps' AND branch_no=2" + lock).fetchone()


def is_khaliya_branch(branch):
    return (branch and branch['brand_id'] == 'kps' and branch.get('branch_no') == 2
            and normalize_city(branch['city']) == 'حفر الباطن')


def customer_quote_view(quote):
    result = dict(quote)
    result.pop('channel_commercial', None)
    routing = result.pop('internal_routing', None)
    if routing:
        result.update(routing['selected_branch'])
    return result


def commercial_snapshot(product: dict) -> dict:
    settings = product['settings']
    return {**{key: product[key] for key in ('id', 'brand_id', 'name', 'category', 'price_halalas')},
            'source_id': settings.get('source_id', product['id']),
            'option_groups': settings.get('option_groups') or [], 'variants': settings.get('variants') or []}


def commercial_digest(product: dict) -> str:
    return db.digest(commercial_snapshot(product))


def source_approved(product: dict) -> bool:
    settings = product['settings']
    if settings.get('source_verified') is not True:
        return False
    approval = settings.get('menu_approval')
    # Legacy explicit owner approvals and other brands keep their existing path.
    if not approval:
        return True
    expected = settings.get('owner_approved_digest') or approval.get('effective_commercial_digest')
    return expected == commercial_digest(product)


def apply_final_catalog_approval(conn):
    """One-time, content-matched owner approval; called after safe initial import.

    The source manifest hash is pinned independently of the current mutable seed.
    Every mismatch is recorded and never automatically approved on a later boot.
    No operational branch settings, history, images or confirmed orders are reset.
    """
    manifest_bytes = (db.ROOT / 'data/final_menu_approval.json').read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != FINAL_MENU_MANIFEST_SHA256:
        raise RuntimeError('The approved owner menu manifest changed; a new explicit decision is required.')
    manifest = json.loads(manifest_bytes)
    for entry in manifest['products']:
        conn.execute('INSERT INTO catalog_owner_approvals(decision_id,product_id,brand_id,source_commit,source_html_sha256,source_digest,source_snapshot) '
                     'VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                     (FINAL_MENU_APPROVAL_ID, entry['product_id'], entry['brand_id'], manifest['source_commit'],
                      manifest['source_html_sha256'], entry['source_digest'], db.json(entry['source_snapshot'])))
    approvals = conn.execute("SELECT * FROM catalog_owner_approvals WHERE decision_id=%s ORDER BY product_id FOR UPDATE",
                             (FINAL_MENU_APPROVAL_ID,)).fetchall()
    for approval in approvals:
        if approval['evaluated_at'] is not None:
            continue
        product = conn.execute('SELECT * FROM products WHERE id=%s AND brand_id=%s FOR UPDATE',
                               (approval['product_id'], approval['brand_id'])).fetchone()
        if not product:
            continue  # Empty-schema restores evaluate only after their rows exist.
        result = 'skipped_mismatch'
        if product:
            snapshot = commercial_snapshot(product) | {key: product['settings'].get(key) for key in ('image_url', 'source_url')}
            if db.digest(snapshot) == approval['source_digest'] and snapshot == approval['source_snapshot']:
                settings = product['settings'] | {
                    'source_verified': True,
                    'menu_approval': {'decision_id': FINAL_MENU_APPROVAL_ID,
                                      'source_commit': approval['source_commit'],
                                      'source_html_sha256': approval['source_html_sha256'],
                                      'source_digest': approval['source_digest'],
                                      'effective_commercial_digest': commercial_digest(product)}}
                conn.execute('UPDATE products SET settings=%s,updated_at=now() WHERE id=%s', (db.json(settings), product['id']))
                db.audit(conn, None, 'catalog.owner_source_approval', 'product', product['id'],
                         {'decision_id': FINAL_MENU_APPROVAL_ID, 'source_digest': approval['source_digest']})
                result = 'approved'
        conn.execute('UPDATE catalog_owner_approvals SET evaluated_at=now(),result=%s WHERE decision_id=%s AND product_id=%s',
                     (result, FINAL_MENU_APPROVAL_ID, approval['product_id']))
    parent_approval = conn.execute('SELECT * FROM catalog_owner_approvals WHERE decision_id=%s AND product_id=%s FOR UPDATE',
                                   (FINAL_MENU_APPROVAL_ID, 'kps-33')).fetchone()
    if not parent_approval or parent_approval['transform_evaluated_at'] is not None:
        return
    parent = conn.execute("SELECT * FROM products WHERE id='kps-33' AND brand_id='kps' FOR UPDATE").fetchone()
    classic = conn.execute("SELECT * FROM products WHERE id='kps-34' AND brand_id='kps' FOR UPDATE").fetchone()
    classic_approval = conn.execute('SELECT result FROM catalog_owner_approvals WHERE decision_id=%s AND product_id=%s',
                                    (FINAL_MENU_APPROVAL_ID, 'kps-34')).fetchone()
    if not parent or not classic:
        return
    transformed = 'skipped_mismatch'
    if (parent and classic and parent_approval['result'] == 'approved' and classic_approval
            and classic_approval['result'] == 'approved' and source_approved(parent) and source_approved(classic)):
        # Prices derive from the two exact approved source rows; no new price is introduced.
        settings = parent['settings'] | {'unified_khaliya': True, 'pending_owner_image': False,
            'image_url': '/assets/khaliya-owner-20261010.webp',
            'owner_image_approval': {'approved_at': '2026-10-10', 'source_sha256': '4bffccdcf1a7e9667c92176f2f70675978f38704c4a04c05731383447c6547e7',
                                     'original_url': '/assets/khaliya-owner-20261010.jpeg'},
            'available_cities': ['حفر الباطن'],
            'option_groups': [{'code': 'TYPE', 'name': 'اختر النوع', 'type': 'select', 'required': True,
                'min_select': 1, 'max_select': 1, 'values': [
                    {'code': 'classic', 'name': 'كلاسيك', 'source_product_id': classic['id'],
                     'source_price_halalas': classic['price_halalas'],
                     'price_delta_halalas': classic['price_halalas'] - parent['price_halalas']},
                    {'code': 'pecan', 'name': 'بيكان', 'source_product_id': parent['id'],
                     'source_price_halalas': parent['price_halalas'], 'price_delta_halalas': 0}]}]}
        settings.pop('owner_approved_digest', None)
        merged = dict(parent, name='الخلية', settings=settings)
        settings['menu_approval'] = settings['menu_approval'] | {'effective_commercial_digest': commercial_digest(merged),
                                                              'amendment': 'one-card-required-classic-pecan'}
        conn.execute('UPDATE products SET name=%s,settings=%s,updated_at=now() WHERE id=%s',
                     (merged['name'], db.json(settings), parent['id']))
        archived_settings = classic['settings'] | {'purchasable': False, 'merged_into': parent['id'], 'merged_type': 'classic'}
        conn.execute('UPDATE products SET settings=%s,updated_at=now() WHERE id=%s', (db.json(archived_settings), classic['id']))
        db.audit(conn, None, 'catalog.khaliya_unified', 'product', parent['id'],
                 {'source_products': KHALIYA_SOURCES, 'owner_image': 'received_and_applied', 'source_image_sha256': settings['owner_image_approval']['source_sha256'], 'history_preserved': True})
        transformed = 'applied'
    conn.execute('UPDATE catalog_owner_approvals SET transform_evaluated_at=now(),transform_result=%s WHERE decision_id=%s AND product_id=%s',
                 (transformed, FINAL_MENU_APPROVAL_ID, 'kps-33'))


def legacy_item_mappings(products: list[dict]) -> dict:
    if not any(p['id'] == 'kps-33' and p['settings'].get('unified_khaliya') for p in products):
        return {}
    return {source_id: {'product_id': 'kps-33', 'options': {'TYPE': variant},
                        'original_name': 'خلية كلاسيك' if variant == 'classic' else 'خلية بيكان'}
            for variant, source_id in KHALIYA_SOURCES.items()}


def normalize_city(value: str) -> str:
    compact = re.sub(r'\s+', '', value or '')
    return {'حفرالباطن': 'حفر الباطن', 'رفحا': 'رفحاء', 'رفحاء': 'رفحاء', 'قبة': 'قبه'}.get(compact, (value or '').strip())


def _hours_open(hours, now=None) -> bool:
    """Compatibility entry point for the shared Riyadh service-hours engine."""
    return service_hours.hours_open(hours, now)


def _minute(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
        raise ValueError('Invalid hour')
    hour, minute = map(int, value.split(':'))
    return hour * 60 + minute


def branch_service(branch: dict, service: str, now=None, brand_settings=None) -> tuple[bool, str | None, str | None]:
    now = now or service_hours.now_riyadh()
    settings = branch['settings']
    service_key = 'car_pickup' if service == 'car' else service
    if (brand_settings or {}).get('services', {}).get(service_key, True) is not True:
        return False, 'BRAND_SERVICE_DISABLED', 'هذه الخدمة موقوفة من إدارة البراند.'
    if settings.get('active', True) is not True:
        return False, 'BRANCH_DISABLED', 'الفرع متوقف عن استقبال الطلبات الجديدة.'
    if service == 'delivery' and normalize_city(branch['city']) not in DELIVERY_CITIES:
        return False, 'DELIVERY_CITY_NOT_ELIGIBLE', 'هذا الفرع غير مشمول بخدمة التوصيل حاليًا.'
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
            return False, 'DELIVERY_CLOSED', closed_delivery_message(settings['delivery_hours'], now)
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


def coverage_configured(coverage) -> bool:
    """Distinguish an unknown boundary from a known, out-of-range address."""
    if not isinstance(coverage, dict):
        return False
    if coverage.get('type') == 'radius':
        radius = coverage.get('radius_meters')
        return (valid_location(coverage.get('center')) and isinstance(radius, (int, float))
                and not isinstance(radius, bool) and math.isfinite(radius) and radius > 0)
    points = coverage.get('coordinates')
    return (coverage.get('type') == 'polygon' and isinstance(points, list)
            and 3 <= len(points) <= 1000
            and all(isinstance(p, list) and len(p) == 2
                    and valid_location({'lng': p[0], 'lat': p[1]}) for p in points))


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


def product_availability(product: dict, branch: dict | None, *, as_variant=False, service=None) -> tuple[bool, str | None]:
    settings = product['settings']
    if settings.get('purchasable') is False and not as_variant:
        return False, 'هذا الصنف أصبح ضمن الخلية. اختر النوع من البطاقة الموحدة.'
    if settings.get('lifecycle') in {'draft', 'paused'} or settings.get('enabled', True) is not True:
        return False, 'الصنف غير متاح حاليًا.'
    cities = settings.get('available_cities') or []
    # Additional hard guard preserves the explicitly identified original two source records.
    if product['brand_id'] == 'kps' and product['id'] in {'kps-33', 'kps-34'}:
        cities = ['حفر الباطن']
    if cities and not branch:
        return False, 'حدد فرع التنفيذ أولًا للتحقق من توفر الصنف.'
    if branch:
        if branch['brand_id'] != product['brand_id']:
            return False, 'الصنف لا يتبع براند الفرع.'
        if cities and normalize_city(branch['city']) not in {normalize_city(city) for city in cities}:
            return False, 'متوفر في حفر الباطن فقط' if {normalize_city(c) for c in cities} == {'حفر الباطن'} else 'الصنف غير متاح في مدينة الفرع.'
        if is_khaliya(product) and service in {'pickup', 'car'} and not is_khaliya_branch(branch):
            return False, 'الخلية متوفرة للاستلام من فرع 2 فقط.'
        branch_ids = settings.get('available_branch_ids')
        if branch_ids is not None and branch['id'] not in branch_ids:
            return False, 'الصنف غير متاح في هذا الفرع.'
        if branch['id'] in (settings.get('unavailable_branch_ids') or []):
            return False, 'الصنف موقوف مؤقتًا في هذا الفرع.'
        overrides = branch['settings'].get('product_availability') or {}
        if overrides.get(product['id']) is False:
            return False, 'الصنف موقوف مؤقتًا في هذا الفرع.'
    for group in settings.get('option_groups') or []:
        if not isinstance(group, dict):
            continue  # The pricing path diagnoses malformed legacy options.
        minimum = group.get('min_select', 1 if group.get('required', True) else 0)
        values = group.get('values') or []
        if type(minimum) is int and minimum > 0 and isinstance(values, list):
            if sum(option_value_availability(value, branch)[0] for value in values) < minimum:
                return False, 'الخيارات اللازمة للصنف غير متاحة حاليًا في هذا الفرع.'
    return True, None


def option_value_availability(value: dict, branch=None) -> tuple[bool, str | None]:
    """One stock policy for independent colors, sizes and flavors everywhere.

    Availability is separate from product enablement and historical order
    snapshots. A branch stop never pauses other options or other branches.
    """
    if not isinstance(value, dict) or value.get('enabled', True) is not True:
        return False, 'الخيار غير متاح حاليًا.'
    if branch:
        allowed = value.get('available_branch_ids')
        if allowed is not None and branch['id'] not in allowed:
            return False, 'الخيار غير متاح في هذا الفرع.'
        if branch['id'] in (value.get('unavailable_branch_ids') or []):
            return False, 'الخيار موقوف مؤقتًا في هذا الفرع.'
    return True, None


def availability_only_change(previous: dict, updated: dict) -> bool:
    """A stock edit preserves an existing approval of unchanged commercial terms."""
    def terms(product):
        snapshot = commercial_snapshot(product)
        snapshot['option_groups'] = [
            dict(group, values=[{k: v for k, v in value.items()
                                if k not in {'enabled', 'available_branch_ids', 'unavailable_branch_ids'}}
                               for value in group.get('values', [])])
            for group in snapshot['option_groups']]
        return snapshot
    return terms(previous) == terms(updated)


def public_product(product: dict, branch=None, service=None) -> dict:
    settings = product['settings']
    available, reason = product_availability(product, branch, service=service)
    result = {key: product[key] for key in ('id', 'brand_id', 'name', 'category', 'price_halalas')}
    result.update({key: settings.get(key) for key in ('image_url', 'description')})
    groups = []
    for group in settings.get('option_groups') or []:
        values = []
        for value in group.get('values') or []:
            ok, why = option_value_availability(value, branch)
            values.append(dict(value, available=ok, unavailable_reason=why))
        groups.append(dict(group, values=values))
    result.update(option_groups=groups, variants=settings.get('variants', []),
                  available_cities=['حفر الباطن'] if product['brand_id'] == 'kps' and product['id'] in {'kps-33', 'kps-34'} else settings.get('available_cities', []), enabled=settings.get('enabled', True),
                  source_verified=source_approved(product), pending_owner_image=settings.get('pending_owner_image') is True, unified_khaliya=settings.get('unified_khaliya') is True, lifecycle=settings.get('lifecycle', 'active' if settings.get('enabled', True) else 'paused'), available=available, unavailable_reason=reason)
    return result


@router.get('/catalog')
def catalog(brand_id: str = Query(default='kps', max_length=80), branch_id: str | None = Query(default=None, max_length=80),
            service: Literal['pickup', 'car', 'delivery'] | None = Query(default=None)):
    with db.tx(pipeline=True) as conn:
        # Independent, freshly scoped reads share one protocol flush. No menu
        # cache: availability and brand changes remain visible immediately.
        with conn.pipeline():
            brand_query = conn.execute('SELECT id,name,settings FROM brands WHERE id=%s AND active', (brand_id,))
            branch_query = conn.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s', (branch_id, brand_id)) if branch_id else None
            products_query = conn.execute("SELECT * FROM products WHERE brand_id=%s AND coalesce(settings->>'lifecycle','active')<>'draft' AND coalesce(settings->>'purchasable','true')<>'false' ORDER BY id", (brand_id,))
            preferences_query = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s', (brand_id,))
            classic_query = conn.execute("SELECT * FROM products WHERE id='kps-34' AND brand_id='kps'") if brand_id == 'kps' else None
        brand = brand_query.fetchone()
        if not brand:
            raise db.error(404, 'BRAND_NOT_FOUND', 'البراند غير متاح.')
        branch = branch_query.fetchone() if branch_query else None
        if branch_id and not branch:
            raise db.error(404, 'BRANCH_NOT_FOUND', 'الفرع غير موجود.')
        products = products_query.fetchall()
        preferences = preferences_query.fetchone()
        target = khaliya_branch(conn) if branch and brand_id == 'kps' and service == 'delivery' and normalize_city(branch['city']) == 'حفر الباطن' else None
        classic = classic_query.fetchone() if classic_query else None
        public_products = []
        for product in products:
            effective = target if target and is_khaliya(product) else branch
            item = public_product(product, effective, service)
            if branch and is_khaliya(product) and service == 'delivery' and normalize_city(branch['city']) == 'حفر الباطن':
                if not is_khaliya_branch(target):
                    item.update(available=False, unavailable_reason='الخلية غير متاحة حاليًا.')
                else:
                    ready, _, why = branch_service(target, service, brand_settings=brand['settings'])
                    if not ready:
                        item.update(available=False, unavailable_reason='الخلية غير متاحة حاليًا.')
            # Both pickup and delivery preserve the original classic stock control.
            if is_khaliya(product) and effective:
                for group in item['option_groups']:
                    for value in group['values']:
                        if value.get('code') == 'classic' and (not classic or not product_availability(classic, effective, as_variant=True)[0]):
                            value.update(available=False, unavailable_reason='الخيار غير متاح حاليًا.')
                    minimum = group.get('min_select', 1 if group.get('required', True) else 0)
                    if type(minimum) is int and sum(value['available'] for value in group['values']) < minimum and item['available']:
                        item.update(available=False, unavailable_reason='الخلية غير متاحة حاليًا.')
            public_products.append(item)

    safe_brand = {'id': brand['id'], 'name': brand['name'], 'logo_url': brand['settings'].get('logo_url', '/assets/kps-logo-fb911a7b4bb4.png' if brand_id == 'kps' else None)}
    safe_brand['theme'] = {key: value for key, value in ((preferences['settings'] if preferences else {}).get('theme') or {}).items()
                           if key in {'accent', 'background', 'text', 'font', 'profile'}}
    # This allowlisted projection already consists entirely of JSON values.
    # Serialize it once here instead of traversing it again on the event loop.
    return JSONResponse({'brand': safe_brand, 'products': public_products,
            'legacy_item_mappings': legacy_item_mappings(products),
            'source_verified': bool(public_products) and all(p['source_verified'] for p in public_products)})


@router.get('/branches')
def branches(brand_id: str = Query(default='kps', max_length=80)):
    with db.tx() as conn:
        if not conn.execute('SELECT 1 FROM brands WHERE id=%s AND active', (brand_id,)).fetchone():
            raise db.error(404, 'BRAND_NOT_FOUND', 'البراند غير متاح.')
        brand_settings = conn.execute('SELECT settings FROM brands WHERE id=%s', (brand_id,)).fetchone()['settings']
        rows = conn.execute('SELECT * FROM branches WHERE brand_id=%s ORDER BY branch_no', (brand_id,)).fetchall()
    result = []
    now = service_hours.now_riyadh()
    for branch in rows:
        item = {key: branch[key] for key in ('id', 'brand_id', 'branch_no', 'name', 'city')}
        item['location'] = branch['settings'].get('location') if location_verified(branch) else None
        item['directions_url'] = directions_url(branch)
        item['location_verified'] = location_verified(branch)
        item['payment_methods'] = branch['settings'].get('payment_methods') or []
        item['delivery_eligible'] = normalize_city(branch['city']) in DELIVERY_CITIES
        item['delivery_schedule'] = hours_status(branch['settings'].get('delivery_hours'), now)
        item['services'] = {}
        for service in ('pickup', 'car', 'delivery'):
            available, code, reason = branch_service(branch, service, now=now, brand_settings=brand_settings)
            item['services'][service] = {'available': available, 'code': code, 'reason': reason}
        result.append(item)
    return {'branches': result,
            'brand_services': {key: brand_settings.get('services', {}).get(key, True) is True
                               for key in ('delivery', 'pickup', 'car_pickup')}}


@router.get('/products/{product_id}/availability')
def availability(product_id: str, branch_id: str):
    with db.tx() as conn:
        product = conn.execute('SELECT * FROM products WHERE id=%s', (product_id,)).fetchone()
        branch = conn.execute('SELECT * FROM branches WHERE id=%s', (branch_id,)).fetchone()
        if not product or not branch or product['brand_id'] != branch['brand_id']:
            raise db.error(404, 'PRODUCT_NOT_FOUND', 'الصنف أو الفرع غير موجود.')
        available, reason = product_availability(product, branch)
        return {'available': available, 'reason': reason, 'source_verified': source_approved(product)}


class _CheckoutModel(BaseModel):
    """Reject text PostgreSQL cannot persist before a checkout transaction starts."""
    model_config = ConfigDict(extra='forbid')

    @field_validator('*', check_fields=False)
    @classmethod
    def persistable_text(cls, value):
        def check(item):
            if isinstance(item, str):
                if '\x00' in item:
                    raise ValueError('Text must not contain a NUL character.')
            elif isinstance(item, dict):
                for key, child in item.items():
                    check(key)
                    check(child)
            elif isinstance(item, (list, tuple)):
                for child in item:
                    check(child)
        check(value)
        return value


class Location(_CheckoutModel):
    model_config = ConfigDict(extra='forbid')
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False, strict=True)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False, strict=True)


class Car(_CheckoutModel):
    model_config = ConfigDict(extra='forbid')
    plate: str = Field(min_length=1, max_length=30)
    color: str = Field(min_length=1, max_length=40)
    model: str = Field(default='', max_length=60)

    @field_validator('plate', 'color')
    @classmethod
    def car_identification_required(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Car plate and color are required.')
        return value


class CustomerInfo(_CheckoutModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, min_length=8, max_length=30)
    neighborhood: str = Field(default='', max_length=120)
    address: str = Field(default='', max_length=500)
    location: Location | None = None
    car: Car | None = None

    @field_validator('name')
    @classmethod
    def customer_name_required(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('Customer name is required.')
        return value


class Unit(_CheckoutModel):
    model_config = ConfigDict(extra='forbid')
    options: dict[str, str | list[str]] = Field(default_factory=dict, max_length=15)


class OrderItem(_CheckoutModel):
    model_config = ConfigDict(extra='forbid')
    product_id: str = Field(min_length=1, max_length=80)
    quantity: StrictInt = Field(ge=1, le=50)
    units: list[Unit] | None = Field(default=None, max_length=50)
    note: str = Field(default='', max_length=300)


class QuoteBody(_CheckoutModel):
    model_config = ConfigDict(extra='forbid')
    brand_id: str = Field(default='kps', min_length=1, max_length=80)
    branch_id: str = Field(min_length=1, max_length=80)
    service: Literal['pickup', 'car', 'delivery']
    payment_method: Literal['cash', 'card', 'split']
    items: list[OrderItem] = Field(min_length=1, max_length=50)
    customer: CustomerInfo
    notes: str = Field(default='', max_length=1000)
    expected_total_halalas: StrictInt | None = Field(default=None, ge=0, le=10000000)

    @model_validator(mode='before')
    @classmethod
    def delivery_neighborhood_error_contract(cls, value):
        # Preserve the existing delivery error code while rejecting a NUL
        # before nested text validation or any PostgreSQL statement executes.
        if isinstance(value, dict) and value.get('service') == 'delivery':
            customer = value.get('customer')
            neighborhood = customer.get('neighborhood') if isinstance(customer, dict) else None
            if isinstance(neighborhood, str) and '\x00' in neighborhood:
                raise db.error(422, 'DELIVERY_NEIGHBORHOOD_REQUIRED', 'أدخل اسم حي التوصيل بصورة واضحة.')
        return value


def _price_units(product: dict, item: OrderItem, branch=None) -> tuple[int, list]:
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
                ok, why = option_value_availability(value, branch)
                if not ok:
                    raise db.error(409, 'PRODUCT_VARIANT_UNAVAILABLE', why)
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
    with conn.pipeline():
        brand_query = conn.execute('SELECT id,settings FROM brands WHERE id=%s AND active' + lock, (body.brand_id,))
        branch_query = conn.execute('SELECT * FROM branches WHERE id=%s AND brand_id=%s' + lock, (body.branch_id, body.brand_id))
    brand, branch = brand_query.fetchone(), branch_query.fetchone()
    if not brand or not branch:
        raise db.error(404, 'BRANCH_NOT_FOUND', 'الفرع أو البراند غير موجود.')
    available, code, reason = branch_service(branch, body.service, brand_settings=brand['settings'])
    if not available:
        raise db.error(409, code, reason)
    selected_branch = branch
    contains_khaliya = body.brand_id == 'kps' and any(item.product_id in KHALIYA_SOURCES.values() for item in body.items)
    if contains_khaliya and body.service == 'delivery' and normalize_city(branch['city']) == 'حفر الباطن':
        branch = khaliya_branch(conn, lock)
        if not is_khaliya_branch(branch):
            raise db.error(409, 'PRODUCT_UNAVAILABLE', 'الخلية غير متاحة حاليًا.')
        ready, code, reason = branch_service(branch, body.service, brand_settings=brand['settings'])
        if not ready:
            raise db.error(409, code, reason)
    required_methods={'cash','card'} if body.payment_method=='split' else {body.payment_method}
    if not all(required_methods.issubset(b['settings']['payment_methods']) for b in (selected_branch, branch)):
        raise db.error(409, 'PAYMENT_METHOD_UNAVAILABLE', 'طريقة الدفع غير متاحة لهذا الفرع.')
    if body.service == 'delivery':
        if not body.customer.location or not body.customer.address.strip():
            raise db.error(422, 'DELIVERY_ADDRESS_REQUIRED', 'حدد موقع وعنوان التوصيل.')
        neighborhood = body.customer.neighborhood.strip()
        if '\x00' in neighborhood or sum(character.isalnum() for character in neighborhood) < 2:
            raise db.error(422, 'DELIVERY_NEIGHBORHOOD_REQUIRED', 'أدخل اسم حي التوصيل بصورة واضحة.')
        if not covered(selected_branch['settings']['coverage'], body.customer.location.model_dump()):
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
        ok, reason = product_availability(product, branch, service=body.service)
        if not ok:
            raise db.error(409, 'PRODUCT_UNAVAILABLE', reason)
        if not source_approved(product):
            raise db.error(409, 'CATALOG_SOURCE_UNVERIFIED', 'السعر وخيارات هذا الصنف لم تُعتمد من المصدر التشغيلي بعد.')
        amount, units = _price_units(product, item, branch)
        subtotal += amount
        if product['settings'].get('unified_khaliya'):
            grouped, checked_sources = {}, set()
            for unit in units:
                selected = unit['options'].get('TYPE')
                selected = selected[0] if isinstance(selected, list) and len(selected) == 1 else selected
                if selected not in KHALIYA_SOURCES:
                    raise db.error(422, 'KHALIYA_TYPE_REQUIRED', 'اختر النوع: كلاسيك أو بيكان.')
                source_id = KHALIYA_SOURCES[selected]
                # Preserve stock stops configured on the former classic source row.
                if source_id != product['id'] and source_id not in checked_sources:
                    variant_source = conn.execute('SELECT * FROM products WHERE id=%s AND brand_id=%s' + lock,
                                                  (source_id, body.brand_id)).fetchone()
                    if not variant_source or not product_availability(variant_source, branch, as_variant=True)[0]:
                        raise db.error(409, 'PRODUCT_VARIANT_UNAVAILABLE', 'نوع الخلية المحدد غير متاح في هذا الفرع.')
                    checked_sources.add(source_id)
                unit['source_id'] = source_id
                grouped.setdefault(selected, []).append(unit)
            for selected, selected_units in grouped.items():
                snapshots.append({'product_id': product['id'], 'source_id': KHALIYA_SOURCES[selected], 'variant_code': selected,
                                  'name': product['name'] + ' — ' + ('كلاسيك' if selected == 'classic' else 'بيكان'),
                                  'category': product['category'], 'quantity': len(selected_units),
                                  'unit_price_halalas': selected_units[0]['price_halalas'],
                                  'line_total_halalas': sum(unit['price_halalas'] for unit in selected_units),
                                  'units': selected_units, 'note': item.note})
        else:
            snapshots.append({'product_id': product['id'], 'source_id': product['settings'].get('source_id', product['id']),
                              'name': product['name'], 'category': product['category'], 'quantity': item.quantity,
                              'unit_price_halalas': product['price_halalas'], 'line_total_halalas': amount,
                              'units': units, 'note': item.note})
    minimum = max((b['settings'].get('minimum_order_halalas') for b in (selected_branch, branch)
                   if type(b['settings'].get('minimum_order_halalas')) is int), default=0)
    if body.service == 'delivery' and isinstance(minimum, int) and subtotal < minimum:
        raise db.error(409, 'MINIMUM_ORDER_NOT_MET', 'السلة أقل من الحد الأدنى المعتمد للتوصيل.')
    fee = selected_branch['settings']['delivery_fee_halalas'] if body.service == 'delivery' else 0
    if subtotal + fee > 10000000:
        raise db.error(422, 'ORDER_VALUE_LIMIT', 'قيمة الطلب أعلى من الحد المسموح للموقع.')
    result = {'brand_id': body.brand_id, 'branch_id': branch['id'], 'branch_no': branch['branch_no'], 'branch_name': branch['name'],
            'branch_city': branch['city'], 'branch_location': branch['settings'].get('location'),
            'service': body.service, 'payment_method': body.payment_method, 'items': snapshots,
            'subtotal_halalas': subtotal, 'delivery_fee_halalas': fee, 'total_halalas': subtotal + fee,
            'discount_halalas': 0, 'tax': {'status': 'not_configured', 'amount_halalas': None},
            'notes': body.notes, 'currency': 'SAR', 'priced_at': datetime.now(RIYADH).isoformat()}
    if selected_branch['id'] != branch['id']:
        result['internal_routing'] = {'reason': 'khaliya_branch_2', 'selected_branch': {
            'branch_id': selected_branch['id'], 'branch_no': selected_branch['branch_no'],
            'branch_name': selected_branch['name'], 'branch_city': selected_branch['city'],
            'branch_location': selected_branch['settings'].get('location')}}
    return result



@router.post('/quote')
def quote(body: QuoteBody, request: Request = None):
    with db.tx(pipeline=True) as conn:
        result = {'quote': customer_quote_view(quote_order(conn, body))}
    return DatabaseJSONResponse(result) if request is not None else result


class AvailabilityBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    available: bool
    reason: str = Field(default='', max_length=300)


@router.post('/branches/{branch_id}/products/{product_id}/availability')
def branch_product_availability(branch_id: str, product_id: str, body: AvailabilityBody,
                                user=Depends(auth.current_user),
                                idempotency_key: str | None = Header(default=None)):
    if user['role'] not in ('owner','branch_manager') and not auth.has_permission(user,'products_manage'):
        raise db.error(403, 'ROLE_FORBIDDEN', 'يلزم تفويض إدارة الأصناف.')
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


def safe_campaign_url(value):
    """Validate a client navigation link without resolving or fetching it."""
    def invalid():
        raise db.error(422, 'CAMPAIGN_URL_INVALID', 'أدخل رابط إعلان HTTPS صحيحًا دون بيانات تسجيل دخول.')
    if not isinstance(value, str) or not value or len(value) > 2000:
        invalid()
    decoded = unquote(value)
    if ('\\' in decoded or any(c.isspace() for c in value)
            or any(unicodedata.category(c).startswith('C') for c in decoded)):
        invalid()
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if parsed.scheme != 'https' or not host or parsed.username is not None or parsed.password is not None:
            invalid()
        if port is not None and not 1 <= port <= 65535:
            invalid()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            encoded_host = host.encode('idna').decode('ascii')
            if len(encoded_host) > 253 or '.' not in encoded_host or any(
                    not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label)
                    for label in encoded_host.split('.')):
                invalid()
    except (ValueError, UnicodeError):
        invalid()
    return value


def map_route_destination(path):
    """Read an explicit final route stop, never the origin or camera center."""
    path = unquote(path)
    if not path.startswith('/maps/dir/'):
        return None
    stops = []
    for segment in path[len('/maps/dir/'):].split('/'):
        if segment.startswith(('@', 'data=')):
            break
        stops.append(segment)
    while stops and not stops[-1]:
        stops.pop()
    if len(stops) < 2:
        return None
    destination = stops[-1]
    if not re.fullmatch(r'-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?', destination):
        return None
    lat, lng = map(float, destination.split(','))
    return destination if valid_location({'lat': lat, 'lng': lng}) else None


def safe_map_url(value):
    """Accept a branch destination, never a review link or a map viewport.

    Short Google Maps links still require an owner's explicit verification;
    validation never follows them or contacts a pasted host.
    """
    if (not isinstance(value, str) or len(value) > 2000 or any(c.isspace() for c in value)
            or any(unicodedata.category(c).startswith('C') for c in unquote(value))):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.hostname not in MAP_HOSTS
                or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)):
            return None
        path = unquote(parsed.path)
        if any(segment in {'.','..'} for segment in path.split('/')) or '\\' in unquote(value):
            return None
        query = parse_qs(parsed.query)
        if re.search(r'/(?:reviews?|writereview)(?:/|$)', path, re.IGNORECASE) or any(
                'review' in key.lower() or key.lower() == 'lrd' for key in query):
            return None
        if parsed.hostname == 'maps.app.goo.gl':
            return value if re.fullmatch(r'/[A-Za-z0-9_-]+/?', path) else None
        if parsed.hostname == 'goo.gl':
            return value if re.fullmatch(r'/maps/[A-Za-z0-9_-]+/?', path) else None
        if not re.match(r'^/maps(?:/|$)', path) and not (parsed.hostname=='maps.google.com' and path in {'','/'}):
            return None
        if map_route_destination(path):
            return value
        # A real pin, Place ID or CID identifies a destination. @lat,lng only
        # describes the camera center and a free text search can select a wrong
        # namesake branch, so neither is accepted as verified branch evidence.
        place_id = (query.get('destination_place_id') or query.get('query_place_id') or [None])[0]
        if place_id and re.fullmatch(r'[A-Za-z0-9_-]{10,250}', place_id):
            return value
        if any(re.fullmatch(r'\d{1,25}', candidate) for candidate in query.get('cid', [])):
            return value
        for candidate in (query.get('destination') or []) + (query.get('query') or []) + (query.get('q') or []):
            if re.fullmatch(r'place_id:[A-Za-z0-9_-]{10,250}', candidate):
                return value
            if re.fullmatch(r'-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?', candidate):
                lat, lng = map(float, candidate.split(','))
                if valid_location({'lat': lat, 'lng': lng}):
                    return value
        pin = re.search(r'!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)', path)
        if pin and valid_location({'lat': float(pin.group(1)), 'lng': float(pin.group(2))}):
            return value
        if re.search(r'!1s(?:0x[0-9a-fA-F]+:0x[0-9a-fA-F]+|ChI[A-Za-z0-9_-]{7,247})(?:!|$)', path):
            return value
        return None
    except ValueError:
        return None


def location_verified(branch):
    settings = branch['settings']
    verified = settings.get('location_verified') is True if 'location_verified' in settings else (
           settings.get('operational_data_verified') is True or settings.get('ordering_verified') is True)
    return valid_location(settings.get('location')) and verified


def directions_url(branch):
    settings = branch['settings']
    place_id = settings.get('place_id')
    if (settings.get('map_url_verified') is True and isinstance(place_id, str)
            and re.fullmatch(r'[A-Za-z0-9_-]{10,250}', place_id)):
        destination = branch.get('name') or place_id
        if location_verified(branch):
            point = settings['location']
            destination = f"{point['lat']},{point['lng']}"
        return 'https://www.google.com/maps/dir/?' + urlencode(
            {'api': '1', 'destination': destination, 'destination_place_id': place_id, 'travelmode': 'driving'})
    if location_verified(branch):
        point = settings['location']
        return 'https://www.google.com/maps/dir/?' + urlencode({'api': '1', 'destination': f"{point['lat']},{point['lng']}", 'travelmode': 'driving'})
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
    if urlsplit(value).hostname in {'maps.app.goo.gl', 'goo.gl'}:
        value = resolve_short_map_url(value)
    parsed = urlsplit(value)
    query = parse_qs(parsed.query)
    candidate = (query.get('destination') or query.get('query') or query.get('q') or [None])[0]
    if candidate is None:
        candidate = map_route_destination(parsed.path)
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


def resolve_short_map_url(value):
    """Follow only Google Maps redirects; never fetch arbitrary URLs or page bodies."""
    import httpx
    try:
        with httpx.Client(timeout=3.0, follow_redirects=False, trust_env=False) as client:
            for _ in range(4):
                if not safe_map_url(value):
                    raise db.error(422, 'MAP_REDIRECT_NOT_ALLOWED', 'الرابط لا يشير إلى موقع Google Maps موثوق.')
                if urlsplit(value).hostname not in {'maps.app.goo.gl', 'goo.gl'}:
                    return value
                with client.stream('GET', value, headers={'User-Agent':'Mozilla/5.0'}) as response:
                    if response.status_code not in (301,302,303,307,308) or not response.headers.get('location'):
                        break
                    value = urljoin(value, response.headers['location'])
            if safe_map_url(value) and urlsplit(value).hostname not in {'maps.app.goo.gl','goo.gl'}:
                return value
    except httpx.HTTPError:
        pass
    raise db.error(422, 'MAP_SHORT_LINK_UNAVAILABLE', 'تعذر فتح الرابط المختصر. افتحه في خرائط جوجل وانسخ رابط الدبوس الكامل، أو استخدم الإحداثيات الصحيحة.')


class NearestBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    location: Location
    product_ids: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(default_factory=list, max_length=50)
    items: list[OrderItem] = Field(default_factory=list, max_length=50)


class PublicNearestBody(NearestBody):
    brand_id: str = Field(default='kps', min_length=1, max_length=80)
    service: Literal['pickup', 'car', 'delivery']


def _nearest_catalog(conn, brand_id, body):
    """Load only brand-scoped rows, including the existing option source rows."""
    requested = set(body.product_ids) | {item.product_id for item in body.items}
    if len(requested) > 50 or sum(item.quantity for item in body.items) > 100:
        raise db.error(422, 'ORDER_QUANTITY_LIMIT', 'السلة أكبر من الحد المسموح للتحقق من الفروع.')
    products = conn.execute('SELECT * FROM products WHERE brand_id=%s AND id=ANY(%s)',
                            (brand_id, sorted(requested))).fetchall() if requested else []
    if requested != {row['id'] for row in products}:
        raise db.error(422, 'PRODUCT_NOT_FOUND', 'صنف خارج البراند أو غير موجود.')
    by_id = {product['id']: product for product in products}
    selected = {}
    for item in body.items:
        _, units = _price_units(by_id[item.product_id], item)
        selected.setdefault(item.product_id, []).extend(units)
    source_ids = set()
    for product in products:
        if product['settings'].get('unified_khaliya'):
            source_ids.update(KHALIYA_SOURCES.values())
        for group in product['settings'].get('option_groups') or []:
            if not isinstance(group, dict):
                continue
            for value in group.get('values') or []:
                if isinstance(value, dict) and isinstance(value.get('source_product_id'), str):
                    source_ids.add(value['source_product_id'])
    source_ids -= set(by_id)
    if source_ids:
        sources = conn.execute('SELECT * FROM products WHERE brand_id=%s AND id=ANY(%s)',
                               (brand_id, sorted(source_ids))).fetchall()
        by_id.update({row['id']: row for row in sources})
    return products, by_id, selected


def _option_available(value, branch, product, by_id):
    if not option_value_availability(value, branch)[0]:
        return False
    source_id = value.get('source_product_id')
    if product['settings'].get('unified_khaliya'):
        source_id = KHALIYA_SOURCES.get(value.get('code')) or source_id
    if not source_id or source_id == product['id']:
        return True
    source = by_id.get(source_id)
    return bool(source and source_approved(source) and product_availability(source, branch, as_variant=True)[0])


def _nearest_unavailable_products(products, by_id, selected, branch, service=None):
    missing = []
    for product in products:
        ok, why = product_availability(product, branch, service=service)
        code = 'PRODUCT_UNAVAILABLE'
        if ok and not source_approved(product):
            ok, why, code = False, 'بيانات سعر وخيارات الصنف لم تعتمد.', 'CATALOG_SOURCE_UNVERIFIED'
        groups = product['settings'].get('option_groups') or []
        if ok and not isinstance(groups, list):
            ok, why = False, 'خيارات الصنف غير مهيأة.'
        if ok:
            for group in groups:
                values = group.get('values') if isinstance(group, dict) else None
                if not isinstance(values, list):
                    ok, why = False, 'خيارات الصنف غير مهيأة.'
                    break
                group_code = str(group.get('code') or group.get('id'))
                if product['id'] in selected:
                    codes = set()
                    for unit in selected[product['id']]:
                        options = unit['options'].get(group_code, [])
                        codes.update([options] if isinstance(options, str) else options)
                    options = {str(value.get('code') or value.get('id')): value for value in values if isinstance(value, dict)}
                    if any(not _option_available(options.get(choice), branch, product, by_id) for choice in codes):
                        ok, why, code = False, 'نوع أو خيار الصنف المحدد غير متاح في هذا الفرع.', 'PRODUCT_VARIANT_UNAVAILABLE'
                        break
                else:
                    minimum = group.get('min_select', 1 if group.get('required', True) else 0)
                    if type(minimum) is not int or sum(_option_available(value, branch, product, by_id) for value in values) < minimum:
                        ok, why, code = False, 'الخيارات اللازمة للصنف غير متاحة في هذا الفرع.', 'PRODUCT_VARIANT_UNAVAILABLE'
                        break
        if not ok:
            missing.append({'product_id': product['id'], 'reason': why, 'code': code})
    return missing


def rank_nearest_branches(rows, point, service, brand_settings, products=(), by_id=None, selected=None):
    """One qualification path for the public store and scoped delivery desk."""
    eligible, excluded = [], []
    now = service_hours.now_riyadh()
    target = next((row for row in rows if is_khaliya_branch(row)), None)
    contains_khaliya = any(is_khaliya(product) for product in products)
    for branch in rows:
        available, code, reason = branch_service(branch, service, now=now, brand_settings=brand_settings)
        if available and not location_verified(branch):
            available, code, reason = False, 'BRANCH_LOCATION_UNVERIFIED', 'دبوس الفرع غير معتمد.'
        # Geographic exclusion takes precedence over temporary service/stock
        # states, but missing boundaries must never masquerade as known ones.
        coverage = branch['settings'].get('coverage')
        if service == 'delivery' and coverage_configured(coverage) and not covered(coverage, point):
            available, code, reason = False, 'OUTSIDE_COVERAGE', 'العنوان خارج نطاق توصيل هذا الفرع.'
        execution = branch
        if service == 'delivery' and contains_khaliya and normalize_city(branch['city']) == 'حفر الباطن':
            if not target:
                available, code, reason = False, 'PRODUCT_UNAVAILABLE', 'الخلية غير متاحة حاليًا.'
            else:
                execution = target
                ready, target_code, target_reason = branch_service(target, service, now=now, brand_settings=brand_settings)
                if available and not ready:
                    available, code, reason = False, target_code, target_reason
        missing = _nearest_unavailable_products(products, by_id or {}, selected or {}, execution, service)
        if available and missing:
            available, code, reason = False, 'PRODUCT_UNAVAILABLE', 'بعض أصناف أو خيارات السلة غير متاحة في الفرع.'
        item = {key: branch[key] for key in ('id', 'branch_no', 'name', 'city')}
        item.update(available=available, code=code, reason=reason, unavailable_products=missing,
                    directions_url=directions_url(branch), distance_label=DISTANCE_LABEL,
                    delivery_fee_halalas=branch['settings'].get('delivery_fee_halalas'),
                    delivery_schedule=hours_status(branch['settings'].get('delivery_hours'), now),
                    location_verified=location_verified(branch), covered=covered(branch['settings'].get('coverage'), point),
                    services={kind: {'available': state[0], 'code': state[1], 'reason': state[2]}
                              for kind in ('pickup', 'car', 'delivery')
                              for state in [branch_service(branch, kind, now=now, brand_settings=brand_settings)]})
        if available:
            item['direct_distance_meters'] = round(direct_distance(point, branch['settings']['location']), 1)
            eligible.append(item)
        else:
            item['direct_distance_meters'] = None
            excluded.append(item)
    eligible.sort(key=lambda row: (row['direct_distance_meters'], row['branch_no']))
    return {'branches': eligible, 'excluded': excluded, 'distance_label': DISTANCE_LABEL,
            'location_source': 'customer_selected', 'routing_provider': None, 'automatically_changes_order': False}


@router.post('/branches/nearest')
def public_nearest_branches(body: PublicNearestBody):
    # Same-origin protection is applied by the normal API middleware. This is
    # read-only public catalog information and does not create a guest session.
    with db.tx() as conn:
        brand = conn.execute('SELECT settings FROM brands WHERE id=%s AND active', (body.brand_id,)).fetchone()
        if not brand:
            raise db.error(404, 'BRAND_NOT_FOUND', 'البراند غير متاح.')
        rows = conn.execute('SELECT * FROM branches WHERE brand_id=%s ORDER BY branch_no', (body.brand_id,)).fetchall()
        products, by_id, selected = _nearest_catalog(conn, body.brand_id, body)
    return rank_nearest_branches(rows, body.location.model_dump(), body.service, brand['settings'], products, by_id, selected)


@router.post('/operations/nearest-branches')
def nearest_branches(body: NearestBody, user=Depends(auth.require_roles('owner', 'delivery_manager', 'delivery_staff'))):
    point = body.location.model_dump()
    with db.tx() as conn:
        brand = conn.execute('SELECT settings FROM brands WHERE id=%s AND active', (user['brand_id'],)).fetchone()
        if not brand:
            raise db.error(404, 'BRAND_NOT_FOUND', 'البراند غير موجود.')
        rows = conn.execute('SELECT * FROM branches WHERE brand_id=%s ORDER BY branch_no', (user['brand_id'],)).fetchall()
        products, by_id, selected = _nearest_catalog(conn, user['brand_id'], body)
    scoped_rows = [branch for branch in rows if user['role'] == 'owner' or branch['id'] in user['branch_ids']]
    return rank_nearest_branches(scoped_rows, point, 'delivery', brand['settings'], products, by_id, selected)
