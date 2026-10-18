"""Reviewed Enbishan menu imports create drafts and never enable ordering."""
from decimal import Decimal, InvalidOperation
import hashlib
import re
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field
from . import auth, db, menu_variants

router = APIRouter(prefix='/api/admin/enbishan-menu')
LABEL_LIMITS = {'product_code': 100, 'name': 200, 'name_en': 200, 'category': 100,
                'category_en': 100, 'size': 100, 'flavour': 100, 'image': 500}


def label(value, field):
    if value is None:
        return ''
    value = str(value).strip()
    if len(value) > LABEL_LIMITS[field] or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid menu field: ' + field)
    return value


def halalas(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('A selling price is required.')
    raw = str(value).strip().translate(str.maketrans('٠١٢٣٤٥٦٧٨٩٫', '0123456789.'))
    try:
        number = Decimal(raw)
        if not number.is_finite() or number <= 0 or number > 100000 or number * 100 != (number * 100).to_integral_value():
            raise ValueError
        return int(number * 100)
    except (ValueError, InvalidOperation, OverflowError):
        raise ValueError('Prices must be explicit SAR amounts with at most two decimals.') from None


def stable_id(prefix, value):
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:24]


def prepare_rows(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 5000:
        raise ValueError('Supply between 1 and 5000 menu rows.')
    grouped = {}
    unresolved_images = set()
    for number, source in enumerate(rows, 2):
        if not isinstance(source, dict) or set(source) - (set(LABEL_LIMITS) | {'price', 'price_before'}):
            raise ValueError('Unknown menu columns on row ' + str(number))
        row = {key: label(source.get(key), key) for key in LABEL_LIMITS}
        if not row['product_code'] or not row['name'] or not row['category']:
            raise ValueError('Product code, name and category are required on row ' + str(number))
        row['price_halalas'] = halalas(source.get('price'))
        before = source.get('price_before')
        row['compare_at_halalas'] = None if before in (None, '') else halalas(before)
        if row['compare_at_halalas'] is not None and row['compare_at_halalas'] <= row['price_halalas']:
            raise ValueError('The before price must exceed the selling price on row ' + str(number))
        grouped.setdefault(row['product_code'], []).append(row)
    products = []
    for code, values in sorted(grouped.items()):
        first = values[0]
        for row in values:
            if any(row[k] != first[k] for k in ('name', 'name_en', 'category', 'category_en', 'image')):
                raise ValueError('Conflicting product descriptions for code ' + code)
        axes = [key for key in ('size', 'flavour') if any(row[key] for row in values)]
        groups = []
        for axis in axes:
            if any(not row[axis] for row in values):
                raise ValueError('Every variant must specify ' + axis + ' for ' + code)
            groups.append({'code': axis.upper(), 'name': 'الحجم' if axis == 'size' else 'النكهة',
                           'name_en': 'Size' if axis == 'size' else 'Flavour', 'required': True,
                           'min_select': 1, 'max_select': 1, 'type': 'select',
                           'values': [{'code': stable_id('v-', v), 'name': v, 'price_delta_halalas': 0}
                                      for v in sorted({r[axis] for r in values})]})
        image = first['image']
        # Uploaded branded assets only. Local filenames/URLs remain explicit
        # inputs for the asset-upload step; this importer never fetches URLs.
        image_url = image if re.fullmatch(r'/api/product-assets/[A-Za-z0-9_-]+', image) else None
        if image and image_url is None:
            unresolved_images.add(image)
        settings = {'enabled': False, 'lifecycle': 'draft', 'source_verified': False,
                    'source_id': code, 'option_groups': groups, 'image_url': image_url,
                    'name_en': first['name_en'], 'category_en': first['category_en']}
        matrix = [{'options': {a.upper(): stable_id('v-', row[a]) for a in axes},
                   'price_halalas': row['price_halalas'],
                   **({'compare_at_halalas': row['compare_at_halalas']} if row['compare_at_halalas'] is not None else {})}
                  for row in values]
        settings['variant_price_matrix'] = matrix
        product = {'id': stable_id('enbishan-menu-', code), 'brand_id': 'enbishan',
                   'name': first['name'], 'category': first['category'],
                   'price_halalas': min(r['price_halalas'] for r in values), 'settings': settings}
        menu_variants.validated(product)
        products.append(product)
    return {'brand_id': 'enbishan', 'products': products,
            'unresolved_images': sorted(unresolved_images), 'ordering_enabled': False,
            'missing_final_inputs': ['approved seller/VAT and branch operation configuration',
                                    'owner review and approval of the imported commercial values'],
            'catalog_digest': db.digest(products)}


class MenuInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    rows: list[dict] = Field(min_length=1, max_length=5000)


class ApplyInput(MenuInput):
    reviewed_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    expected_catalog_digest: str = Field(pattern=r'^[a-f0-9]{64}$')


def current_catalog(conn, lock=False):
    return conn.execute("SELECT id,name,category,price_halalas,settings,updated_at FROM products WHERE brand_id='enbishan' ORDER BY id" +
                        (' FOR UPDATE' if lock else '')).fetchall()


def prepare(body):
    try:
        return prepare_rows(body.rows)
    except ValueError as error:
        raise db.error(422, 'MENU_IMPORT_INVALID', str(error)) from None


def enbishan_owner(user=Depends(auth.require_roles('owner'))):
    if user['brand_id'] != 'enbishan':
        raise db.error(403, 'MENU_IMPORT_BRAND_FORBIDDEN', 'اختر نطاق عنبيشن المخول لحساب المالك أولًا.')
    return user


@router.post('/preview')
def preview(body: MenuInput, user=Depends(enbishan_owner)):
    result = prepare(body)
    with db.tx() as conn:
        if not db.rate_limit(conn, 'enbishan-menu-preview:' + user['id'], 20, 3600):
            raise db.error(429, 'MENU_IMPORT_RATE_LIMIT', 'انتظر قبل معاينة ملف آخر.')
        current = current_catalog(conn)
    return {**result, 'source_sha256': body.source_sha256, 'expected_catalog_digest': db.digest(current)}


@router.post('/apply')
def apply(body: ApplyInput, user=Depends(enbishan_owner), idem: str | None = Header(None, alias='Idempotency-Key')):
    prepared = prepare(body)
    if prepared['catalog_digest'] != body.reviewed_digest:
        raise db.error(409, 'MENU_REVIEW_CHANGED', 'تغيرت بيانات الملف منذ المعاينة.')
    with db.tx() as conn:
        auth.require_owner_sms_step_up(conn, user)
        def perform():
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('menu-import:enbishan',))
            current = current_catalog(conn, lock=True)
            if db.digest(current) != body.expected_catalog_digest:
                raise db.error(409, 'MENU_CATALOG_CHANGED', 'تغير المنيو. أعد المعاينة قبل الاستيراد.')
            existing = {row['id']: row for row in current}
            for product in prepared['products']:
                foreign = conn.execute("SELECT 1 FROM products WHERE id=%s AND brand_id<>'enbishan'", (product['id'],)).fetchone()
                if foreign:
                    raise db.error(409, 'MENU_SOURCE_ID_CONFLICT', 'مرجع الصنف مستخدم في نطاق آخر.')
                image = product['settings'].get('image_url')
                if image and not conn.execute("SELECT 1 FROM assets WHERE id=%s AND brand_id='enbishan' AND purpose='product'", (image.rsplit('/', 1)[1],)).fetchone():
                    raise db.error(422, 'MENU_IMAGE_SCOPE', 'ارفع صورة الصنف داخل نطاق عنبيشن أولًا.')
                previous = existing.get(product['id'])
                if previous and (previous['settings'].get('enabled') is not False or previous['settings'].get('lifecycle') != 'draft'):
                    raise db.error(409, 'MENU_ACTIVE_PRODUCT_LOCKED', 'تعديل صنف تشغيلي يتطلب إجراء الإدارة المعتمد؛ الاستيراد للمسودات فقط.')
                settings = {**product['settings'], 'import_source_sha256': body.source_sha256}
                conn.execute("INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,'enbishan',%s,%s,%s,%s) "
                             "ON CONFLICT(id) DO UPDATE SET name=excluded.name,category=excluded.category,price_halalas=excluded.price_halalas,settings=excluded.settings,updated_at=now() WHERE products.brand_id='enbishan'",
                             (product['id'], product['name'], product['category'], product['price_halalas'], db.json(settings)))
            db.audit(conn, user['id'], 'enbishan.menu.imported_draft', 'brand', 'enbishan',
                     {'source_sha256': body.source_sha256, 'catalog_digest': body.reviewed_digest, 'products': len(prepared['products'])})
            return {'imported_drafts': len(prepared['products']), 'ordering_enabled': False,
                    'unresolved_images': prepared['unresolved_images']}
        return db.idempotent(conn, user['id'], idem, 'enbishan.menu.import', body.model_dump(), perform)
