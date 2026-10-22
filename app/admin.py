"""Scoped operational configuration and scheduled first-party banners."""
from __future__ import annotations

import hashlib
import io
import re
import math
from datetime import datetime

from fastapi import APIRouter, Depends, File, Header, Response, UploadFile
from PIL import Image, UnidentifiedImageError
from . import auth, db, catalog, email_delivery, service_hours, print_translations

router = APIRouter(prefix='/api')
ALERT_POLICY_DEFAULTS = {
    'first_after_seconds': 0, 'second_after_seconds': 30,
    'continuous_after_seconds': 60, 'first_ring_seconds': 1, 'second_ring_seconds': 1,
}


def alert_policy(conn, brand_id):
    row = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s', (brand_id,)).fetchone()
    return ALERT_POLICY_DEFAULTS | ((row['settings'].get('alert_policy') or {}) if row else {})


@router.get('/operations/alert-policy')
def read_alert_policy(user=Depends(auth.require_roles('owner','admin','delivery_manager','delivery_staff','branch_manager','driver'))):
    with db.tx() as conn:
        return {'mandatory': True, 'policy': alert_policy(conn, user['brand_id'])}


@router.patch('/admin/alert-policy')
def update_alert_policy(body: dict, user=Depends(auth.require_roles('owner')),
                        idem: str | None=Header(None, alias='Idempotency-Key')):
    # Dedicated owner-only route; delegated appearance/configuration grants do not apply.
    if set(body) != set(ALERT_POLICY_DEFAULTS) or any(type(v) is not int for v in body.values()):
        raise db.error(422, 'ALERT_POLICY_INVALID', 'أدخل جميع توقيتات الجرس بأرقام صحيحة.')
    first, second, continuous = (body[k] for k in ('first_after_seconds','second_after_seconds','continuous_after_seconds'))
    if (not 0 <= first <= 300 or not first < second <= 600 or
            not second < continuous <= 1800 or
            not all(1 <= body[k] <= 10 for k in ('first_ring_seconds','second_ring_seconds')) or
            first + 2 * body['first_ring_seconds'] + .3 > second or
            second + 2 * body['second_ring_seconds'] + .3 > continuous):
        raise db.error(422, 'ALERT_POLICY_INVALID', 'رتب أوقات التنبيه دون تداخل؛ مدة الدقة من ثانية إلى عشر ثوانٍ.')
    with db.tx() as conn:
        auth.require_owner_step_up(conn, user)
        def perform():
            row = conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s FOR UPDATE', (user['brand_id'],)).fetchone()
            settings = row['settings'] | {'alert_policy': body}
            conn.execute('UPDATE app_settings SET settings=%s,updated_at=now() WHERE brand_id=%s', (db.json(settings), user['brand_id']))
            db.audit(conn, user['id'], 'alerts.policy.updated', 'brand', user['brand_id'],
                     {'before': row['settings'].get('alert_policy', ALERT_POLICY_DEFAULTS), 'after': body})
            return {'mandatory': True, 'policy': body}
        return db.idempotent(conn, user['id'], idem, 'alerts.policy.update', body, perform)


owner = auth.require_roles('owner')
branch_admin = auth.require_permission('branches_manage')
product_admin = auth.require_permission('products_manage')
banner_admin = auth.require_permission('banners_manage')
appearance_admin = auth.require_permission('appearance_manage')
DELIVERY_CITIES = {'حفرالباطن', 'الذيبية', 'القيصومة', 'الخفجي', 'رفحاء', 'قبه'}


def city(value):
    normalized = re.sub(r'\s+', '', catalog.normalize_city(str(value)))
    return 'رفحاء' if normalized == 'رفحا' else normalized


def scoped(conn, table, record_id, user):
    if table not in {'branches', 'products', 'users', 'banners', 'assets'}:
        raise ValueError('unsupported scoped table')
    row = conn.execute(f'SELECT * FROM {table} WHERE id=%s AND brand_id=%s FOR UPDATE', (record_id, user['brand_id'])).fetchone()
    if not row:
        raise db.error(404, 'RECORD_NOT_FOUND', 'السجل غير موجود ضمن صلاحياتك.')
    if table == 'branches' and user['role'] == 'admin' and record_id not in user['branch_ids']:
        raise db.error(404, 'RECORD_NOT_FOUND', 'الفرع خارج نطاقك المخول.')
    return row


def money(value, field):
    if type(value) is not int or not 0 <= value <= 100_000_000:
        raise db.error(422, 'MONEY_INVALID', f'{field}: أدخل المبلغ بالهللات كعدد صحيح غير سالب.')
    return value


def text(value, maximum, field):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        raise db.error(422, 'TEXT_INVALID', f'{field}: قيمة غير صالحة.')
    return value.strip()


def keys(body, allowed):
    if not isinstance(body, dict) or set(body) - set(allowed):
        raise db.error(422, 'CONFIGURATION_FIELDS_INVALID', 'تحتوي البيانات حقولًا غير مسموحة.')


def branch_ids(conn, values, brand):
    if not isinstance(values, list) or len(values) > 500 or any(not isinstance(x, str) for x in values):
        raise db.error(422, 'BRANCH_SCOPE_INVALID', 'نطاق الفروع غير صحيح.')
    if len(set(values)) != len(values):
        raise db.error(422, 'BRANCH_SCOPE_INVALID', 'أزل الفروع المكررة.')
    for value in values:
        # Brand membership is validated below; delegated branch scope is
        # enforced at each caller as user-aware configuration.
        if not conn.execute('SELECT 1 FROM branches WHERE id=%s AND brand_id=%s', (value, brand)).fetchone():
            raise db.error(422, 'BRANCH_SCOPE_INVALID', 'أحد الفروع خارج البراند.')
    return values


def product_scope(settings, user):
    if user['role'] != 'admin':
        return
    scope = settings.get('available_branch_ids')
    if not isinstance(scope,list) or not scope or any(not isinstance(value,str) for value in scope) or not set(scope) <= set(user['branch_ids']):
        raise db.error(403, 'PRODUCT_SCOPE_FORBIDDEN', 'إدارة الصنف خارج فروعك المخولة تحتاج المالك.')


def validate_option_scope(conn, groups, user):
    for group in groups:
        for value in group['values']:
            for field in ('available_branch_ids', 'unavailable_branch_ids'):
                if field in value and value[field] is not None:
                    scope = branch_ids(conn, value[field], user['brand_id'])
                    if user['role'] == 'admin' and not set(scope) <= set(user['branch_ids']):
                        raise db.error(403, 'PRODUCT_SCOPE_FORBIDDEN', 'توفر الخيار خارج فروعك المخولة.')


def coordinate(value):
    if not isinstance(value, dict) or set(value) != {'lat', 'lng'}:
        raise db.error(422, 'COORDINATE_INVALID', 'الموقع يحتاج خط عرض وطول فعليين.')
    if any(type(value[k]) not in {int, float} or not math.isfinite(value[k]) for k in ('lat', 'lng')) or not -90 <= value['lat'] <= 90 or not -180 <= value['lng'] <= 180:
        raise db.error(422, 'COORDINATE_INVALID', 'الموقع غير صحيح.')
    return value


def hours(value):
    if not isinstance(value, list) or not value or len(value) > 28:
        raise db.error(422, 'HOURS_INVALID', 'أدخل ساعات العمل الفعلية.')
    for row in value:
        if (not isinstance(row, dict) or not {'days', 'start', 'end'} <= set(row)
                or set(row) - {'days', 'start', 'end', 'enabled'}):
            raise db.error(422, 'HOURS_INVALID', 'صيغة ساعات العمل غير صحيحة.')
        if 'enabled' in row and type(row['enabled']) is not bool:
            raise db.error(422, 'HOURS_INVALID', 'تفعيل الفترة يجب أن يكون صحيحًا أو خطأ.')
        if not isinstance(row['days'], list) or not row['days'] or any(type(d) is not int or not 0 <= d <= 6 for d in row['days']):
            raise db.error(422, 'HOURS_INVALID', 'الأيام من الاثنين 0 إلى الأحد 6.')
        # An explicitly disabled future period may be prepared without invented
        # times. It cannot admit orders or shifts until valid times are supplied.
        if row.get('enabled') is False and row['start'] in (None, '') and row['end'] in (None, ''):
            continue
        for field in ('start', 'end'):
            if not isinstance(row[field], str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', row[field]):
                raise db.error(422, 'HOURS_INVALID', 'الوقت بصيغة HH:MM وتوقيت الرياض.')
    return value


def validate_options(groups, variants=None):
    if not isinstance(groups,list) or len(groups)>30:
        raise db.error(422,'OPTIONS_INVALID','مجموعات الخيارات غير صحيحة.')
    seen=set()
    for group in groups:
        if not isinstance(group,dict):
            raise db.error(422,'OPTIONS_INVALID','مجموعة الخيارات تحتاج حقولًا واضحة.')
        code=group.get('code') or group.get('id')
        if not isinstance(code,str) or not 1<=len(code)<=80 or code in seen:
            raise db.error(422,'OPTIONS_INVALID','معرف مجموعة الخيارات مفقود أو مكرر.')
        seen.add(code);text(group.get('name',''),120,'اسم الخيار')
        if 'required' in group and type(group['required']) is not bool:
            raise db.error(422,'OPTIONS_INVALID','تحديد الخيار الإلزامي غير صحيح.')
        minimum=group.get('min_select',1 if group.get('required',True) else 0);maximum=group.get('max_select',1)
        if type(minimum) is not int or type(maximum) is not int or not 0<=minimum<=maximum<=30:
            raise db.error(422,'OPTIONS_INVALID','حدود الاختيار غير صحيحة.')
        values=group.get('values')
        if not isinstance(values,list) or not values or len(values)>100 or maximum>len(values):
            raise db.error(422,'OPTIONS_INVALID','حدد قيمًا فعلية كافية للخيارات.')
        codes=set()
        for value in values:
            if not isinstance(value,dict):
                raise db.error(422,'OPTIONS_INVALID','قيمة الخيار غير صحيحة.')
            value_code=value.get('code') or value.get('id')
            if not isinstance(value_code,str) or not 1<=len(value_code)<=80 or value_code in codes:
                raise db.error(422,'OPTIONS_INVALID','معرف قيمة الخيار مفقود أو مكرر.')
            codes.add(value_code);text(value.get('name',''),120,'اسم قيمة الخيار')
            money(value.get('price_delta_halalas',0),'فرق سعر الخيار')
            if 'enabled' in value and type(value['enabled']) is not bool:
                raise db.error(422,'OPTIONS_INVALID','تفعيل قيمة الخيار غير صحيح.')
            for field in ('available_branch_ids','unavailable_branch_ids'):
                if field in value and value[field] is not None and (
                        not isinstance(value[field],list) or len(value[field])>500 or
                        any(not isinstance(branch,str) for branch in value[field]) or
                        len(value[field])!=len(set(value[field]))):
                    raise db.error(422,'BRANCH_SCOPE_INVALID','نطاق توفر الخيار غير صحيح.')
    if variants is not None:
        if not isinstance(variants,list) or len(variants)>100:
            raise db.error(422,'VARIANTS_INVALID','قائمة الألوان غير صحيحة.')
        seen=set()
        for variant in variants:
            if not isinstance(variant,dict):
                raise db.error(422,'VARIANTS_INVALID','بيانات اللون غير صحيحة.')
            code=variant.get('sku') or variant.get('code') or variant.get('id')
            if not isinstance(code,str) or not 1<=len(code)<=120 or code in seen:
                raise db.error(422,'VARIANTS_INVALID','معرف اللون مفقود أو مكرر.')
            seen.add(code)
            if 'hex' in variant and not re.fullmatch(r'#[A-Fa-f0-9]{6}',str(variant['hex'])):
                raise db.error(422,'VARIANTS_INVALID','لون العرض غير صحيح.')


def validate_branch_settings(settings, branch_city, *, brand_id='kps', branch_no=None):
    allowed = {'active','pickup_enabled','car_enabled','delivery_enabled','payment_methods','opening_hours','delivery_hours',
               'coverage','location','location_verified','map_url','map_url_verified','place_id','location_source','delivery_fee_halalas','minimum_order_halalas','ordering_verified','operational_data_verified','online_payment_seller'}
    keys(settings, allowed)
    if settings.get('online_payment_seller') is not None:
        from .payment_gateways import IDENTIFIER
        if not isinstance(settings['online_payment_seller'], str) or not IDENTIFIER.fullmatch(settings['online_payment_seller']):
            raise db.error(422, 'PAYMENT_SELLER_INVALID', 'اختر معرف منشأة صحيحًا.')
    for field in ('active','pickup_enabled','car_enabled','delivery_enabled','ordering_verified','operational_data_verified','location_verified','map_url_verified'):
        if field in settings and type(settings[field]) is not bool:
            raise db.error(422, 'BOOLEAN_INVALID', 'إعداد التفعيل يجب أن يكون صحيحًا أو خطأ.')
    for field in ('opening_hours','delivery_hours'):
        if settings.get(field) not in (None, []):
            hours(settings[field])
    for field in ('delivery_fee_halalas','minimum_order_halalas'):
        if settings.get(field) is not None:
            money(settings[field], field)
    if settings.get('location') is not None:
        coordinate(settings['location'])
    if settings.get('map_url') is not None and not catalog.safe_map_url(settings['map_url']):
        raise db.error(422,'MAP_URL_NOT_ALLOWED','اختر رابط Google Maps معتمدًا دون روابط خارجية.')
    if settings.get('place_id') is not None and not re.fullmatch(r'[A-Za-z0-9_-]{10,250}', str(settings['place_id'])):
        raise db.error(422,'PLACE_ID_INVALID','معرف Google Maps غير صحيح.')
    if settings.get('location_source') is not None:
        text(settings['location_source'], 1000, 'مصدر تحقق موقع الفرع')
    if 'payment_methods' in settings and (not isinstance(settings['payment_methods'], list) or
            any(not isinstance(value, str) or value not in {'cash','card'} for value in settings['payment_methods'])):
        raise db.error(422, 'PAYMENT_METHOD_INVALID', 'طرق الدفع المسموحة كاش وشبكة عند الاستلام.')
    coverage = settings.get('coverage')
    if coverage is not None:
        if not isinstance(coverage, dict) or coverage.get('type') not in {'polygon','radius'}:
            raise db.error(422, 'COVERAGE_INVALID', 'أدخل نطاق الخدمة الفعلي.')
        if coverage['type'] == 'radius':
            keys(coverage, {'type','center','radius_meters'})
            coordinate(coverage.get('center'))
            if type(coverage.get('radius_meters')) not in {int,float} or not 0 < coverage['radius_meters'] <= 200_000:
                raise db.error(422, 'COVERAGE_INVALID', 'نطاق الخدمة غير صحيح.')
        else:
            keys(coverage, {'type','coordinates'})
            points = coverage.get('coordinates')
            if not isinstance(points, list) or not 3 <= len(points) <= 1000:
                raise db.error(422, 'COVERAGE_INVALID', 'نطاق الخدمة يحتاج ثلاث نقاط فعلية على الأقل.')
            for point in points:
                if not isinstance(point,list) or len(point)!=2:
                    raise db.error(422, 'COVERAGE_INVALID', 'النقطة بصيغة [خط الطول، خط العرض].')
                coordinate({'lng':point[0],'lat':point[1]})
    if settings.get('delivery_enabled') and not catalog.delivery_city_eligible({'brand_id': brand_id, 'branch_no': branch_no, 'city': branch_city}):
        raise db.error(422, 'DELIVERY_CITY_FORBIDDEN', 'التوصيل غير معتمد لهذا الفرع ومدينة البراند.')


@router.get('/admin/config')
def configuration(user=Depends(auth.require_configuration())):
    with db.tx() as conn:
        result = {'brand': conn.execute('SELECT * FROM brands WHERE id=%s',(user['brand_id'],)).fetchone(),
                  'branches': conn.execute('SELECT * FROM branches WHERE brand_id=%s ORDER BY branch_no',(user['brand_id'],)).fetchall(),
                  'products': conn.execute('SELECT * FROM products WHERE brand_id=%s ORDER BY category,name',(user['brand_id'],)).fetchall(),
                  'users': conn.execute('SELECT id,username,display_name,role,brand_id,branch_ids,phone,active FROM users WHERE brand_id=%s ORDER BY role,display_name',(user['brand_id'],)).fetchall(),
                  'banners': conn.execute('SELECT * FROM banners WHERE brand_id=%s ORDER BY sort_order,created_at',(user['brand_id'],)).fetchall(),
                  'settings': conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(user['brand_id'],)).fetchone()['settings']}
    with db.tx() as conn:
        identity = auth.channel_readiness(conn, user['brand_id'])
        result['otp_configured'] = any(identity[c]['configured'] for c in ('sms','whatsapp'))
    if user['role'] == 'admin':
        result['branches'] = [b for b in result['branches'] if b['id'] in user['branch_ids']]
        result['users'] = []
        if not auth.has_permission(user, 'products_manage'):
            # Names/categories support the existing banner destination editor.
            result['products'] = [{k:p[k] for k in ('id','name','category')} for p in result['products']] if auth.has_permission(user,'banners_manage') else []
        else:
            result['products'] = [p for p in result['products']
                                  if p['settings'].get('available_branch_ids') and
                                  set(p['settings']['available_branch_ids']) <= set(user['branch_ids'])]
        if not auth.has_permission(user, 'banners_manage'):
            result['banners'] = []
        else:
            allowed_branches = set(user['branch_ids'])
            result['banners'] = [b for b in result['banners']
                                 if b['branch_ids'] and set(b['branch_ids']) <= allowed_branches]
        result['settings'] = {k:v for k,v in result['settings'].items() if k in {'theme','homepage_presentation'}} if auth.has_permission(user,'appearance_manage') else {}
        brand_services = result['brand']['settings'].get('services', {})
        result['brand'] = {k:result['brand'][k] for k in ('id','name')}
        if auth.has_permission(user, 'brand_services_manage'):
            result['brand']['settings'] = {'services': brand_services}
    for product in result['products']:
        if 'settings' in product:
            product['print_labels_en']=print_translations.labels(product)
    return result


@router.patch('/admin/branches/{record_id}')
def update_branch(record_id: str, body: dict, user=Depends(branch_admin), idem: str | None=Header(None,alias='Idempotency-Key')):
    keys(body, {'name','city','settings','reason'})
    reason = text(body.get('reason', ''), 500, 'سبب تعديل الفرع')
    with db.tx() as conn:
        def perform():
            row = scoped(conn,'branches',record_id,user)
            name = text(body.get('name',row['name']),200,'اسم الفرع')
            branch_city = text(body.get('city',row['city']),100,'المدينة')
            changes = body.get('settings',{})
            validate_branch_settings(changes,branch_city,brand_id=row['brand_id'],branch_no=row['branch_no'])
            if 'online_payment_seller' in changes:
                from . import invoice_profiles
                if user['role'] != 'owner':
                    raise db.error(403, 'OWNER_REQUIRED', 'تحديد منشأة الدفع من صلاحيات المالك فقط.')
                seller = changes['online_payment_seller']
                if seller is not None and seller not in invoice_profiles.settings(conn, user['brand_id']).get('entities', {}):
                    raise db.error(422, 'PAYMENT_SELLER_INVALID', 'المنشأة غير موجودة ضمن البراند.')
            settings = row['settings'] | changes
            if 'map_url' in changes and changes['map_url'] is None:
                settings['map_url_verified'] = False
            elif 'map_url' in changes and changes['map_url'] != row['settings'].get('map_url') and 'map_url_verified' not in changes:
                settings['map_url_verified'] = False
            if 'place_id' in changes and changes['place_id'] != row['settings'].get('place_id') and 'map_url_verified' not in changes:
                settings['map_url_verified'] = False
            if 'location' in changes and changes['location'] is None:
                settings['location_verified'] = False
            elif 'location' in changes and changes['location'] != row['settings'].get('location') and 'location_verified' not in changes:
                settings['location_verified'] = False
            if settings.get('delivery_enabled') and not catalog.delivery_city_eligible(dict(row, city=branch_city)):
                raise db.error(422,'DELIVERY_CITY_FORBIDDEN','أوقف التوصيل قبل نقل الفرع إلى مدينة غير معتمدة.')
            enabled = any(settings.get(f) for f in ('pickup_enabled','car_enabled','delivery_enabled'))
            if enabled and (not settings.get('ordering_verified') or not settings.get('opening_hours') or not settings.get('payment_methods')):
                raise db.error(422,'BRANCH_CONFIGURATION_INCOMPLETE','تحقق من التشغيل وساعات العمل وطرق الدفع قبل تفعيل الفرع.')
            if settings.get('delivery_enabled') and (any(not settings.get(k) for k in ('delivery_hours','coverage','location')) or settings.get('delivery_fee_halalas') is None):
                raise db.error(422,'DELIVERY_CONFIGURATION_INCOMPLETE','يلزم الموقع والتغطية والساعات والرسوم الفعلية قبل تفعيل التوصيل.')
            conn.execute('UPDATE branches SET name=%s,city=%s,settings=%s,updated_at=now() WHERE id=%s',(name,branch_city,db.json(settings),record_id))
            db.audit(conn,user['id'],'branch.updated','branch',record_id,{'fields':list(body),'settings_fields':list(changes),'reason':reason})
            return {'id':record_id,'name':name,'city':branch_city,'settings':settings}
        return db.idempotent(conn,user['id'],idem,'branch.update:'+record_id,body,perform)


@router.patch('/admin/products/{record_id}')
def update_product(record_id: str, body: dict, user=Depends(product_admin), idem: str | None=Header(None,alias='Idempotency-Key')):
    keys(body, {'name','category','price_halalas','settings'})
    with db.tx() as conn:
        def perform():
            row = scoped(conn,'products',record_id,user)
            product_scope(row['settings'],user)
            changes = body.get('settings',{})
            keys(changes, {'enabled','source_verified','available_cities','unavailable_branch_ids','option_groups','variants','variant_price_matrix','name_en','category_en','description_en','image_url','image_asset_id','description','lifecycle','available_branch_ids','print_labels_en'})
            if 'print_labels_en' in changes:
                changes['print_labels_en']=print_translations.validate(changes['print_labels_en'])
            if 'lifecycle' in changes and changes['lifecycle'] not in {'draft','active','paused'}:
                raise db.error(422,'PRODUCT_LIFECYCLE_INVALID','حالة الصنف مسودة أو مفعل أو موقوف.')
            if 'lifecycle' in changes:
                changes['enabled']=changes['lifecycle']=='active'
            elif 'enabled' in changes:
                changes['lifecycle']='active' if changes['enabled'] is True else 'paused'
            if 'available_branch_ids' in changes and changes['available_branch_ids'] is not None:
                branch_ids(conn,changes['available_branch_ids'],user['brand_id'])
            for key in ('enabled','source_verified'):
                if key in changes and type(changes[key]) is not bool:
                    raise db.error(422,'BOOLEAN_INVALID','قيمة التفعيل غير صحيحة.')
            if 'unavailable_branch_ids' in changes:
                branch_ids(conn,changes['unavailable_branch_ids'],user['brand_id'])
            if 'available_cities' in changes and (not isinstance(changes['available_cities'],list) or any(not isinstance(x,str) or len(x)>100 for x in changes['available_cities'])):
                raise db.error(422,'CITY_POLICY_INVALID','سياسة توفر المدن غير صحيحة.')
            if 'available_cities' in changes:
                changes['availability_policy_version']=int(row['settings'].get('availability_policy_version',0))+1
            if changes.get('image_asset_id'):
                asset=scoped(conn,'assets',changes['image_asset_id'],user)
                if asset['purpose']!='product':
                    raise db.error(422,'PRODUCT_IMAGE_INVALID','اختر صورة منتج مرفوعة فعليًا.')
                changes['image_url']='/api/product-assets/'+asset['id']
            if 'image_url' in changes and not re.fullmatch(r'(?:/assets/[A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|webp|svg)|/api/product-assets/asset_[a-f0-9]{32})',changes['image_url']):
                raise db.error(422,'IMAGE_URL_INVALID','اختر صورة محلية معتمدة.')
            if changes.get('image_url','').startswith('/api/product-assets/'):
                asset=scoped(conn,'assets',changes['image_url'].rsplit('/',1)[1],user)
                if asset['purpose']!='product':
                    raise db.error(422,'PRODUCT_IMAGE_INVALID','صورة المنتج غير صحيحة.')
            for key in ('option_groups','variants'):
                if key in changes and (not isinstance(changes[key],list) or len(changes[key])>30):
                    raise db.error(422,'OPTIONS_INVALID','خيارات الصنف غير صحيحة.')
            validate_options(changes.get('option_groups',row['settings'].get('option_groups',[])),changes.get('variants',row['settings'].get('variants',[])))
            validate_option_scope(conn,changes.get('option_groups',row['settings'].get('option_groups',[])),user)
            if 'description' in changes:
                changes['description']=text(changes['description'],2000,'وصف الصنف')
            for field, maximum in [('name_en',200),('category_en',100),('description_en',2000)]:
                if field in changes:
                    changes[field]=text(changes[field],maximum,field)
            settings = row['settings'] | changes
            product_scope(settings,user)
            if settings.get('lifecycle') == 'active' and settings.get('source_verified') is not True:
                raise db.error(422,'PRODUCT_SOURCE_UNVERIFIED','اعتمد السعر والصورة والخيارات قبل تفعيل الصنف.')
            name = text(body.get('name',row['name']),200,'اسم الصنف')
            category = text(body.get('category',row['category']),150,'التصنيف')
            price = money(body.get('price_halalas',row['price_halalas']),'سعر الصنف')
            updated = dict(row, name=name, category=category, price_halalas=price, settings=settings)
            if 'variant_price_matrix' in settings:
                from .menu_variants import validated
                try:
                    validated(updated)
                except (ValueError, TypeError, KeyError):
                    raise db.error(422,'PRODUCT_VARIANTS_INVALID','راجع أسعار الأحجام والنكهات والسعر قبل وبعد.') from None
                if changes.get('source_verified') is True:
                    auth.require_owner_sms_step_up(conn,user)
                elif settings.get('owner_approved_digest') != catalog.commercial_digest(updated):
                    settings['source_verified'] = False
                    settings['enabled'] = False
                    settings['lifecycle'] = 'draft'
            if changes.get('source_verified') is True:
                settings['owner_approved_digest'] = catalog.commercial_digest(updated)
            elif catalog.source_approved(row) and catalog.availability_only_change(row,updated):
                # Operational stock changes keep the approved price/identity.
                settings['owner_approved_digest'] = catalog.commercial_digest(updated)
            elif settings.get('menu_approval') and not catalog.source_approved(updated):
                settings['source_verified'] = False
            if 'image_url' in changes:
                settings['pending_owner_image'] = False
            conn.execute('UPDATE products SET name=%s,category=%s,price_halalas=%s,settings=%s,updated_at=now() WHERE id=%s',(name,category,price,db.json(settings),record_id))
            db.audit(conn,user['id'],'product.updated','product',record_id,{'fields':list(body),'settings_fields':list(changes)})
            return {'id':record_id,'name':name,'category':category,'price_halalas':price,'settings':settings}
        return db.idempotent(conn,user['id'],idem,'product.update:'+record_id,body,perform)


@router.post('/admin/users')
def create_user(body: dict, user=Depends(owner), idem: str | None=Header(None,alias='Idempotency-Key')):
    raise db.error(422,'STAFF_EMAIL_INVITATION_REQUIRED','أنشئ الموظف بدعوة بريد فردية من شاشة الموظفين.')

@router.patch('/admin/users/{record_id}')
def update_user(record_id: str, body: dict, user=Depends(owner), idem: str | None=Header(None,alias='Idempotency-Key')):
    keys(body, {'display_name','role','branch_ids','active','password','permissions'})
    with db.tx() as conn:
        def perform():
            row = scoped(conn,'users',record_id,user)
            permissions=auth.validate_admin_permissions(body.get('permissions',row.get('permissions') or []))
            if permissions and row['role'] != 'admin':
                raise db.error(422,'ADMIN_ROLE_REQUIRED','صلاحيات الإدارة مخصصة لدور أدمن فقط.')
            if 'permissions' in body:
                auth.require_owner_step_up(conn,user)
            if row['role']=='customer':
                raise db.error(403,'CUSTOMER_ACCOUNT_PROTECTED','لا يمكن تحويل حساب عميل إلى موظف.')
            role = body.get('role',row['role'])
            active = body.get('active',row['active'])
            if role not in auth.ROLES-{'customer'} or type(active) is not bool:
                raise db.error(422,'USER_CONFIGURATION_INVALID','إعدادات الموظف غير صحيحة.')
            if role != row['role'] and role != 'owner':
                raise db.error(403,'STAFF_ROLE_INVITATION_REQUIRED','تغيير دور الموظف يحتاج مسار دعوة مخول.')
            if role == 'owner' and row['role'] != 'owner':
                raise db.error(403,'OWNER_INVITATION_REQUIRED','إضافة مالك تحتاج دعوة بريد واعتماد المالك الحالي.')
            sms_approved = auth.staff_sms_account_approved(row)
            if active and not row['active'] and sms_approved:
                auth.require_owner_step_up(conn,user)
            if active and (row.get('invitation_status') not in {'active','paused'} or not (row.get('email_verified_at') or sms_approved)) and not (row['active'] and row.get('identity_kind')=='legacy'):
                raise db.error(403,'STAFF_EMAIL_VERIFICATION_REQUIRED','يلزم تحقق الموظف من بريده قبل التفعيل.')
            conn.execute('SELECT id FROM users WHERE brand_id=%s AND role=%s AND active ORDER BY id FOR UPDATE',(user['brand_id'],'owner')).fetchall()
            if row['role']=='owner' and (role!='owner' or not active):
                other = conn.execute('SELECT 1 FROM users WHERE brand_id=%s AND role=%s AND active AND id<>%s',(user['brand_id'],'owner',record_id)).fetchone()
                if not other:
                    raise db.error(409,'LAST_OWNER_PROTECTED','لا يمكن تعطيل آخر مالك مخوّل.')
            scope = branch_ids(conn,body.get('branch_ids',row['branch_ids']),user['brand_id'])
            if role!='owner' and not scope:
                raise db.error(422,'BRANCH_SCOPE_REQUIRED','حدد الفروع المصرح بها للموظف.')
            auth.ensure_safe_staff_scope_change(conn,row,scope,active)
            if role != row['role'] and conn.execute("SELECT 1 FROM shifts WHERE user_id=%s AND status<>'closed'",(record_id,)).fetchone():
                raise db.error(409,'OPEN_SHIFT_ROLE_PROTECTED','أغلق أو سلّم مسؤولية الشفت قبل تغيير الدور.')
            hashed = auth.hash_password(body['password']) if body.get('password') else row['password_hash']
            name = text(body.get('display_name',row['display_name']),200,'الاسم')
            status = 'active' if active and (row.get('email_verified_at') or sms_approved) else 'paused' if not active and row.get('invitation_status')=='active' else row.get('invitation_status')
            updated = conn.execute('UPDATE users SET display_name=%s,role=%s,branch_ids=%s,active=%s,password_hash=%s,invitation_status=%s,updated_at=now() WHERE id=%s RETURNING id,username,display_name,role,brand_id,branch_ids,phone,active',(name,role,db.json(scope),active,hashed,status,record_id)).fetchone()
            conn.execute('UPDATE users SET permissions=%s WHERE id=%s',(db.json(permissions),record_id))
            updated['permissions']=permissions
            conn.execute('UPDATE sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL',(record_id,))
            conn.execute('UPDATE identity_tokens SET revoked_at=now() WHERE user_id=%s AND consumed_at IS NULL AND revoked_at IS NULL',(record_id,))
            conn.execute("UPDATE email_outbox SET status='cancelled',updated_at=now() WHERE dedupe_key IN (SELECT 'identity:'||id FROM identity_tokens WHERE user_id=%s) AND status IN ('pending','failed')",(record_id,))
            db.audit(conn,user['id'],'user.updated','user',record_id,{'fields':list(body),**({'permissions':permissions} if 'permissions' in body else {})})
            return updated
        safe_body=body | ({'password':hashlib.sha256(body['password'].encode()).hexdigest()} if 'password' in body else {})
        return db.idempotent(conn,user['id'],idem,'user.update:'+record_id,safe_body,perform)



def homepage_presentation_config(existing=None, changes=None, brand_id=None):
    """Owner-controlled, text-only customer presentation; safe for other brands."""
    defaults = {
        'ticker_enabled': False,
        'ticker_lines': ['الأماكن والأوقات تجمّلها القهوة دائمًا', 'Speciality Coffee'] if brand_id == 'kps' else [],
        'ticker_seconds': 28,
        'quote_enabled': False,
        'quote_text': '',
    }
    current = existing if isinstance(existing, dict) else {}
    if changes is not None:
        keys(changes, defaults)
    result = defaults | current | (changes or {})
    keys(result, defaults)
    if type(result['ticker_enabled']) is not bool or type(result['quote_enabled']) is not bool:
        raise db.error(422, 'PRESENTATION_INVALID', 'فعّل أو أوقف المحتوى بقيمة صحيحة.')
    if type(result['ticker_seconds']) is not int or not 14 <= result['ticker_seconds'] <= 90:
        raise db.error(422, 'TICKER_SPEED_INVALID', 'مدة حركة الشريط بين 14 و90 ثانية.')
    lines = result['ticker_lines']
    if not isinstance(lines, list) or len(lines) > 8:
        raise db.error(422, 'TICKER_LINES_INVALID', 'يمكن إدخال حتى ثماني عبارات قصيرة.')
    if any(not isinstance(line, str) or not line.strip() or len(line) > 140 or '\x00' in line for line in lines):
        raise db.error(422, 'TICKER_LINES_INVALID', 'كل عبارة يجب أن تكون نصًا واضحًا لا يتجاوز 140 حرفًا.')
    result['ticker_lines'] = [line.strip() for line in lines]
    if result['ticker_enabled'] and not result['ticker_lines']:
        raise db.error(422, 'TICKER_LINES_REQUIRED', 'أضف عبارة واحدة على الأقل أو أوقف الشريط.')
    result['quote_text'] = text(result['quote_text'], 240, 'العبارة الأدبية')
    if result['quote_enabled'] and not result['quote_text']:
        raise db.error(422, 'HERO_QUOTE_REQUIRED', 'أدخل العبارة الأدبية المعتمدة قبل عرضها.')
    return result


@router.patch('/admin/settings')
def update_settings(body: dict, user=Depends(appearance_admin), idem: str | None=Header(None,alias='Idempotency-Key')):
    if {'receipt_occasion_text', 'receipt_occasion_style'} & set(body) and user['role'] != 'owner':
        raise db.error(403,'ROLE_FORBIDDEN','عبارة مناسبات الفاتورة للمالك فقط.')
    if user['role']=='admin' and set(body)-{'theme','homepage_presentation'}:
        raise db.error(403,'ROLE_FORBIDDEN','إعدادات التشغيل الحساسة للمالك فقط.')
    keys(body, {'operational_day_start','late_after_minutes','theme','guest_operational_confirmation_above_halalas','homepage_presentation','delivery_workflow','receipt_occasion_text','receipt_occasion_style'})
    if 'receipt_occasion_style' in body:
        style = body['receipt_occasion_style']
        if not isinstance(style, dict):
            raise db.error(422,'RECEIPT_STYLE_INVALID','تنسيق العبارة غير صحيح.')
        keys(style, {'font', 'size', 'bold', 'align'})
        if (style.get('font') not in ('Arial','Tahoma','Times New Roman') or
            type(style.get('size')) is not int or not 12 <= style['size'] <= 32 or
            type(style.get('bold')) is not bool or style.get('align') not in ('right','center','left')):
            raise db.error(422,'RECEIPT_STYLE_INVALID','اختر خطًا معتمدًا وحجمًا من 12 إلى 32 ومحاذاة صحيحة.')
    if 'receipt_occasion_text' in body:
        text = body['receipt_occasion_text']
        if not isinstance(text, str) or len(text) > 240 or len(text.splitlines()) > 6 or any(ord(c) < 32 and c not in '\n\r' for c in text):
            raise db.error(422,'RECEIPT_TEXT_INVALID','عبارة المناسبات حتى 240 حرفًا و6 أسطر.')
        body['receipt_occasion_text'] = text.strip()
    if 'delivery_workflow' in body and body['delivery_workflow'] not in ('manager_dispatch','branch_fulfillment','branch_without_manager'):
        raise db.error(422,'DELIVERY_WORKFLOW_INVALID','اختر وضع تشغيل التوصيل المعتمد.')
    if 'operational_day_start' in body and not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',str(body['operational_day_start'])):
        raise db.error(422,'OPERATIONAL_DAY_INVALID','وقت بداية اليوم التشغيلي بصيغة HH:MM.')
    if 'late_after_minutes' in body and (type(body['late_after_minutes']) is not int or not 1<=body['late_after_minutes']<=1440):
        raise db.error(422,'DELAY_SETTING_INVALID','حد التأخير من 1 إلى 1440 دقيقة.')
    if 'guest_operational_confirmation_above_halalas' in body and body['guest_operational_confirmation_above_halalas'] is not None:
        money(body['guest_operational_confirmation_above_halalas'],'حد طلب الاتصال التشغيلي')
    if 'theme' in body:
        theme=body['theme']; keys(theme,{'accent','background','text','font','profile'})
        if 'profile' in theme and (not isinstance(theme['profile'],str) or theme['profile'] not in {'CHARCOAL','ORIGINAL_Light'}):
            raise db.error(422,'THEME_INVALID','اختر فحمي أو التصميم السابق.')
        for key in {'accent','background','text'} & set(theme):
            if not re.fullmatch(r'#[A-Fa-f0-9]{6}',str(theme[key])):
                raise db.error(422,'THEME_INVALID','اللون بصيغة آمنة #RRGGBB.')
        if theme.get('font','Tahoma') not in {'Tahoma','Arial','sans-serif','Noto Kufi Arabic'}:
            raise db.error(422,'THEME_INVALID','اختر خطًا معتمدًا.')
    with db.tx() as conn:
        def perform():
            row=conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s FOR UPDATE',(user['brand_id'],)).fetchone()
            settings=row['settings']|body
            if 'theme' in body:
                settings['theme']=(row['settings'].get('theme') or {})|body['theme']
            if 'homepage_presentation' in body:
                settings['homepage_presentation'] = homepage_presentation_config(row['settings'].get('homepage_presentation'), body['homepage_presentation'], user['brand_id'])
            conn.execute('UPDATE app_settings SET settings=%s,updated_at=now() WHERE brand_id=%s',(db.json(settings),user['brand_id']))
            db.audit(conn,user['id'],'settings.updated','brand',user['brand_id'],{'fields':list(body)})
            return {'settings':settings}
        return db.idempotent(conn,user['id'],idem,'settings.update',body,perform)


@router.post('/admin/assets')
async def upload_asset(file: UploadFile=File(...), purpose: str='banner', user=Depends(auth.require_configuration()), idem: str | None=Header(None,alias='Idempotency-Key')):
    if purpose not in {'banner','product'}:
        raise db.error(422,'ASSET_PURPOSE_INVALID','نوع الصورة غير صحيح.')
    if not auth.has_permission(user,'banners_manage' if purpose=='banner' else 'products_manage'):
        raise db.error(403,'ROLE_FORBIDDEN','رفع الصورة يحتاج صلاحية نوعها.')
    contents=await file.read(5*1024*1024+1)
    if len(contents)>5*1024*1024 or not contents:
        raise db.error(413,'IMAGE_SIZE_INVALID','الصورة يجب ألا تتجاوز 5 ميجابايت.')
    try:
        with Image.open(io.BytesIO(contents)) as image:
            if image.format not in {'PNG','JPEG','WEBP'} or image.width>8000 or image.height>8000 or image.width*image.height>24_000_000:
                raise ValueError('unsupported image')
            image.load(); image.thumbnail((2400,1600))
            image=image.convert('RGB'); output=io.BytesIO(); image.save(output,format='JPEG',quality=88,optimize=True)
            processed=output.getvalue()
    except (UnidentifiedImageError,OSError,ValueError,Image.DecompressionBombError):
        raise db.error(422,'IMAGE_TYPE_INVALID','ارفع صورة PNG أو JPEG أو WEBP سليمة.') from None
    digest=hashlib.sha256(processed).hexdigest()
    with db.tx() as conn:
        def perform():
            identifier=db.new_id('asset_')
            conn.execute('INSERT INTO assets(id,brand_id,owner_id,purpose,mime_type,filename,contents,sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(identifier,user['brand_id'],user['id'],purpose,'image/jpeg',purpose+'.jpg',processed,digest))
            db.audit(conn,user['id'],purpose+'.asset.created','asset',identifier,{'sha256':digest,'bytes':len(processed)})
            return {'id':identifier,'preview_url':'/api/admin/assets/'+identifier,'mime_type':'image/jpeg'}
        return db.idempotent(conn,user['id'],idem,purpose+'.asset.create',{'sha256':digest},perform)


@router.get('/admin/assets/{record_id}')
def preview_asset(record_id:str,user=Depends(auth.require_configuration())):
    with db.tx() as conn:
        row=scoped(conn,'assets',record_id,user)
        if not auth.has_permission(user,'banners_manage' if row['purpose']=='banner' else 'products_manage'):
            raise db.error(403,'ROLE_FORBIDDEN','عرض الصورة يحتاج صلاحية نوعها.')
    return Response(bytes(row['contents']),media_type=row['mime_type'],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


def banner_fields(conn,body,user,existing=None):
    keys(body,{'title','body','button_label','destination','desktop_asset_id','mobile_asset_id','branch_ids','active','sort_order','starts_at','ends_at','cities','rotation_ms'})
    row={'cities':[], 'rotation_ms':6500} | dict(existing or {'title':'','body':'','button_label':'','destination':{},'desktop_asset_id':None,'mobile_asset_id':None,'branch_ids':[],'active':False,'sort_order':0,'starts_at':None,'ends_at':None}) | body
    for key,limit in [('title',160),('body',320),('button_label',64)]:
        row[key]=text(row[key],limit,key)
    if not row['title'] or type(row['active']) is not bool or type(row['sort_order']) is not int or not 0<=row['sort_order']<=10_000:
        raise db.error(422,'BANNER_INVALID','العنوان والتفعيل والترتيب غير صحيحين.')
    row['branch_ids']=branch_ids(conn,row['branch_ids'],user['brand_id'])
    if user['role']=='admin':
        if not user['branch_ids']:
            raise db.error(403,'BANNER_SCOPE_FORBIDDEN','منح فروع للإدارة شرط إدارة الإعلان.')
        if existing and (not existing['branch_ids'] or not set(existing['branch_ids']) <= set(user['branch_ids'])):
            raise db.error(403,'BANNER_SCOPE_FORBIDDEN','الإعلان خارج نطاق فروعك المخولة.')
        if not row['branch_ids']:
            row['branch_ids']=user['branch_ids']
        if not set(row['branch_ids']) <= set(user['branch_ids']):
            raise db.error(403,'BANNER_SCOPE_FORBIDDEN','اختر الفروع المخولة فقط.')
    if not isinstance(row['cities'],list) or len(row['cities'])>100 or any(not isinstance(c,str) for c in row['cities']):
        raise db.error(422,'BANNER_CITIES_INVALID','مدن الإعلان غير صحيحة.')
    known={catalog.normalize_city(r['city']) for r in conn.execute('SELECT city FROM branches WHERE brand_id=%s',(user['brand_id'],)).fetchall()}
    row['cities']=list(dict.fromkeys(catalog.normalize_city(c) for c in row['cities']))
    if set(row['cities'])-known:
        raise db.error(422,'BANNER_CITIES_INVALID','اختر مدينة مسجلة في فروع البراند.')
    if type(row['rotation_ms']) is not int or not 4000<=row['rotation_ms']<=90000:
        raise db.error(422,'BANNER_SPEED_INVALID','مدة ظهور الإعلان بين 4 و90 ثانية.')
    for key in ('desktop_asset_id','mobile_asset_id'):
        if row[key]:
            asset=scoped(conn,'assets',row[key],user)
            if asset['purpose']!='banner':
                raise db.error(422,'BANNER_ASSET_INVALID','الصورة ليست مخصصة للعروض.')
    destination=row['destination']
    keys(destination,{'kind','id'})
    if destination:
        if destination.get('kind')=='product':
            scoped(conn,'products',destination.get('id'),user)
        elif destination.get('kind')=='category':
            if not conn.execute('SELECT 1 FROM products WHERE category=%s AND brand_id=%s',(destination.get('id'),user['brand_id'])).fetchone():
                raise db.error(422,'BANNER_DESTINATION_INVALID','التصنيف غير موجود.')
        elif destination.get('kind')=='link':
            catalog.safe_campaign_url(destination.get('id'))
        else:
            raise db.error(422,'BANNER_DESTINATION_INVALID','اختر صنفًا أو تصنيفًا داخل المتجر أو رابط HTTPS صحيحًا.')
    for key in ('starts_at','ends_at'):
        if row[key] is not None:
            try:
                value=datetime.fromisoformat(row[key].replace('Z','+00:00')) if isinstance(row[key],str) else row[key]
                if value.tzinfo is None:
                    raise ValueError('timezone required')
                row[key]=value
            except (ValueError,AttributeError,TypeError):
                raise db.error(422,'BANNER_TIME_INVALID','أرسل وقتًا مع المنطقة الزمنية؛ الإدارة تعرض توقيت الرياض.') from None
    if row['starts_at'] and row['ends_at'] and row['ends_at']<=row['starts_at']:
        raise db.error(422,'BANNER_TIME_INVALID','نهاية الإعلان يجب أن تأتي بعد بدايته.')
    return {key:row[key] for key in ('title','body','button_label','destination','desktop_asset_id','mobile_asset_id','branch_ids','active','sort_order','starts_at','ends_at','cities','rotation_ms')}


@router.post('/admin/banners')
def create_banner(body:dict,user=Depends(banner_admin),idem:str|None=Header(None,alias='Idempotency-Key')):
    with db.tx() as conn:
        def perform():
            row=banner_fields(conn,body,user); identifier=db.new_id('banner_')
            conn.execute('INSERT INTO banners(id,brand_id,title,body,button_label,destination,desktop_asset_id,mobile_asset_id,branch_ids,active,sort_order,starts_at,ends_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(identifier,user['brand_id'],row['title'],row['body'],row['button_label'],db.json(row['destination']),row['desktop_asset_id'],row['mobile_asset_id'],db.json(row['branch_ids']),row['active'],row['sort_order'],row['starts_at'],row['ends_at']))
            conn.execute('UPDATE banners SET cities=%s,rotation_ms=%s WHERE id=%s',(db.json(row['cities']),row['rotation_ms'],identifier))
            db.audit(conn,user['id'],'banner.created','banner',identifier,{'fields':list(body)})
            return {'id':identifier,**row}
        return db.idempotent(conn,user['id'],idem,'banner.create',body,perform)


@router.patch('/admin/banners/{record_id}')
def update_banner(record_id:str,body:dict,user=Depends(banner_admin),idem:str|None=Header(None,alias='Idempotency-Key')):
    with db.tx() as conn:
        def perform():
            existing=scoped(conn,'banners',record_id,user); row=banner_fields(conn,body,user,existing)
            conn.execute('UPDATE banners SET title=%s,body=%s,button_label=%s,destination=%s,desktop_asset_id=%s,mobile_asset_id=%s,branch_ids=%s,active=%s,sort_order=%s,starts_at=%s,ends_at=%s,updated_at=now() WHERE id=%s',(row['title'],row['body'],row['button_label'],db.json(row['destination']),row['desktop_asset_id'],row['mobile_asset_id'],db.json(row['branch_ids']),row['active'],row['sort_order'],row['starts_at'],row['ends_at'],record_id))
            conn.execute('UPDATE banners SET cities=%s,rotation_ms=%s WHERE id=%s',(db.json(row['cities']),row['rotation_ms'],record_id))
            db.audit(conn,user['id'],'banner.updated','banner',record_id,{'fields':list(body)})
            return {'id':record_id,**row}
        return db.idempotent(conn,user['id'],idem,'banner.update:'+record_id,body,perform)


def active_banners(conn,brand_id,branch_id,city_name=None):
    if branch_id and not conn.execute('SELECT 1 FROM branches WHERE id=%s AND brand_id=%s',(branch_id,brand_id)).fetchone():
        raise db.error(404,'BRANCH_NOT_FOUND','الفرع غير موجود ضمن البراند.')
    if branch_id:
        city_name=conn.execute('SELECT city FROM branches WHERE id=%s AND brand_id=%s',(branch_id,brand_id)).fetchone()['city']
    if city_name:
        city_name=catalog.normalize_city(city_name)
    return conn.execute("SELECT * FROM banners WHERE brand_id=%s AND active AND (starts_at IS NULL OR starts_at<=now()) AND (ends_at IS NULL OR ends_at>now()) AND (branch_ids='[]'::jsonb OR branch_ids ? %s) AND (cities='[]'::jsonb OR cities ? %s) ORDER BY sort_order,created_at LIMIT 20",(brand_id,branch_id or '',city_name or '')).fetchall()


@router.get('/banners')
def public_banners(brand_id:str='kps',branch_id:str|None=None,city:str|None=None):
    with db.tx() as conn:
        rows=active_banners(conn,brand_id,branch_id,city)
        configuration=conn.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(brand_id,)).fetchone()
        presentation=homepage_presentation_config((configuration or {}).get('settings',{}).get('homepage_presentation'),brand_id=brand_id)
    def asset_url(identifier):
        from urllib.parse import urlencode
        return f'/api/banner-assets/{identifier}?'+urlencode({'brand_id':brand_id,**({'branch_id':branch_id} if branch_id else {}),**({'city':city} if city else {})}) if identifier else None
    return {'banners':[{'id':row['id'],'title':row['title'],'body':row['body'],'button_label':row['button_label'],'destination':row['destination'],'rotation_ms':row['rotation_ms'],'desktop_image_url':asset_url(row['desktop_asset_id']),'mobile_image_url':asset_url(row['mobile_asset_id'])} for row in rows],
            'presentation':{'ticker_enabled':presentation['ticker_enabled'],
                            'ticker_lines':presentation['ticker_lines'] if presentation['ticker_enabled'] else [],
                            'ticker_seconds':presentation['ticker_seconds'],
                            'quote_text':presentation['quote_text'] if presentation['quote_enabled'] else ''}}


@router.get('/banner-assets/{record_id}')
def public_banner_asset(record_id:str,brand_id:str='kps',branch_id:str|None=None,city:str|None=None):
    with db.tx() as conn:
        banners=active_banners(conn,brand_id,branch_id,city)
        if not any(record_id in {row['desktop_asset_id'],row['mobile_asset_id']} for row in banners):
            raise db.error(404,'BANNER_ASSET_UNAVAILABLE','الصورة غير متاحة.')
        asset=conn.execute("SELECT contents,mime_type FROM assets WHERE id=%s AND brand_id=%s AND purpose='banner'",(record_id,brand_id)).fetchone()
        if not asset:
            raise db.error(404,'BANNER_ASSET_UNAVAILABLE','الصورة غير متاحة.')
    return Response(bytes(asset['contents']),media_type=asset['mime_type'],headers={'Cache-Control':'public, max-age=60','X-Content-Type-Options':'nosniff'})


@router.post('/admin/branches')
def create_branch(body:dict,user=Depends(owner),idem:str|None=Header(None,alias='Idempotency-Key')):
    keys(body,{'branch_no','name','city','settings','source_id'})
    branch_no=body.get('branch_no')
    if type(branch_no) is not int or not 1<=branch_no<=10000:
        raise db.error(422,'BRANCH_NUMBER_INVALID','أدخل رقم التشغيل الفعلي للفرع.')
    name=text(body.get('name'),200,'اسم الفرع'); branch_city=text(body.get('city'),100,'المدينة')
    if not name or not branch_city:
        raise db.error(422,'BRANCH_IDENTITY_REQUIRED','اسم الفرع ومدينته مطلوبان.')
    supplied_settings=body.get('settings',{})
    if supplied_settings.get('online_payment_seller') is not None:
        raise db.error(422, 'INITIAL_PAYMENT_SELLER_PENDING', 'اعتمد منشأة الدفع بعد إنشاء الفرع.')
    validate_branch_settings(supplied_settings,branch_city,brand_id=user['brand_id'],branch_no=branch_no)
    if any(supplied_settings.get(key) for key in ('pickup_enabled','car_enabled','delivery_enabled')):
        raise db.error(422,'INITIAL_BRANCH_DISABLED','أنشئ الفرع متوقفًا ثم راجع بيانات تشغيله قبل التفعيل.')
    with db.tx() as conn:
        def perform():
            if conn.execute('SELECT 1 FROM branches WHERE brand_id=%s AND branch_no=%s',(user['brand_id'],branch_no)).fetchone():
                raise db.error(409,'BRANCH_NUMBER_EXISTS','رقم الفرع التشغيلي موجود؛ حدّث سجله دون إعادة إنشائه.')
            identifier=db.new_id('branch_')
            source_id=text(body.get('source_id','admin:'+identifier),200,'مرجع المصدر')
            if conn.execute('SELECT 1 FROM branches WHERE brand_id=%s AND source_id=%s',(user['brand_id'],source_id)).fetchone():
                raise db.error(409,'BRANCH_SOURCE_EXISTS','مرجع المصدر موجود لفرع آخر.')
            settings={'pickup_enabled':False,'car_enabled':False,'delivery_enabled':False,'ordering_verified':False}\
                     |({'opening_hours':service_hours.DEFAULT_KPS_BRANCH_OPENING_HOURS} if user['brand_id']=='kps' and 'opening_hours' not in supplied_settings else {})\
                     |supplied_settings
            row=conn.execute('INSERT INTO branches(id,branch_no,source_id,brand_id,name,city,settings) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *',(identifier,branch_no,source_id,user['brand_id'],name,branch_city,db.json(settings))).fetchone()
            db.audit(conn,user['id'],'branch.created','branch',identifier,{'branch_no':branch_no,'source_id':source_id})
            return {'branch':row}
        return db.idempotent(conn,user['id'],idem,'branch.create',body,perform)


@router.post('/admin/products')
def create_product(body:dict,user=Depends(product_admin),idem:str|None=Header(None,alias='Idempotency-Key')):
    keys(body,{'name','category','price_halalas','source_id','settings'})
    name=text(body.get('name'),200,'اسم الصنف');category=text(body.get('category'),150,'التصنيف')
    price=money(body.get('price_halalas'),'سعر الصنف')
    if not name or not category:
        raise db.error(422,'PRODUCT_IDENTITY_REQUIRED','اسم الصنف وتصنيفه مطلوبان.')
    settings=body.get('settings',{})
    keys(settings,{'description','option_groups','variants','available_cities','unavailable_branch_ids','available_branch_ids','image_url','image_asset_id'})
    validate_options(settings.get('option_groups',[]),settings.get('variants',[]))
    product_scope(settings,user)
    if 'description' in settings:
        settings['description']=text(settings['description'],2000,'وصف الصنف')
    if 'available_cities' in settings and (not isinstance(settings['available_cities'],list) or any(not isinstance(x,str) or len(x)>100 for x in settings['available_cities'])):
        raise db.error(422,'CITY_POLICY_INVALID','سياسة توفر المدن غير صحيحة.')
    with db.tx() as conn:
        def perform():
            identifier=db.new_id('product_');source_id=text(body.get('source_id','admin:'+identifier),200,'مرجع المصدر')
            if conn.execute("SELECT 1 FROM products WHERE brand_id=%s AND settings->>'source_id'=%s",(user['brand_id'],source_id)).fetchone():
                raise db.error(409,'PRODUCT_SOURCE_EXISTS','الصنف موجود بهذا المرجع؛ حدّث سجله دون إعادة إنشائه.')
            if 'available_branch_ids' in settings and settings['available_branch_ids'] is not None:
                branch_ids(conn,settings['available_branch_ids'],user['brand_id'])
            if 'unavailable_branch_ids' in settings:
                branch_ids(conn,settings['unavailable_branch_ids'],user['brand_id'])
            validate_option_scope(conn,settings.get('option_groups',[]),user)
            if settings.get('image_asset_id'):
                asset=scoped(conn,'assets',settings['image_asset_id'],user)
                if asset['purpose']!='product':
                    raise db.error(422,'PRODUCT_IMAGE_INVALID','اختر صورة منتج مرفوعة فعليًا.')
                settings['image_url']='/api/product-assets/'+asset['id']
            elif settings.get('image_url') and not re.fullmatch(r'/assets/[A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|webp|svg)',settings['image_url']):
                raise db.error(422,'IMAGE_URL_INVALID','اختر صورة محلية معتمدة.')
            configured=settings|{'enabled':False,'lifecycle':'draft','source_verified':False,'source_id':source_id}
            row=conn.execute('INSERT INTO products(id,brand_id,name,category,price_halalas,settings) VALUES(%s,%s,%s,%s,%s,%s) RETURNING *',(identifier,user['brand_id'],name,category,price,db.json(configured))).fetchone()
            db.audit(conn,user['id'],'product.created','product',identifier,{'source_id':source_id,'price_halalas':price})
            return {'product':row}
        return db.idempotent(conn,user['id'],idem,'product.create',body,perform)


@router.get('/product-assets/{record_id}')
def public_product_asset(record_id:str,preview:bool=False):
    with db.tx() as conn:
        row=conn.execute("SELECT a.contents,a.mime_type,a.brand_id FROM assets a JOIN products p ON p.brand_id=a.brand_id JOIN brands b ON b.id=p.brand_id WHERE a.id=%s AND a.purpose='product' AND p.settings->>'image_url'=%s AND COALESCE((p.settings->>'enabled')::boolean,true) AND b.active LIMIT 1",(record_id,'/api/product-assets/'+record_id)).fetchone()
        if not row:
            raise db.error(404,'PRODUCT_ASSET_UNAVAILABLE','الصورة غير متاحة.')
        from .storefront_scope import require_brand
        require_brand(row['brand_id'])
    if preview:
        from .image_previews import thumbnail,response
        result=response(thumbnail(bytes(row['contents'])))
        result.headers['Cache-Control']='public, max-age=60'
        return result
    return Response(bytes(row['contents']),media_type=row['mime_type'],headers={'Cache-Control':'public,max-age=60','X-Content-Type-Options':'nosniff'})


@router.post('/admin/brands')
def create_brand(body:dict,user=Depends(owner),idem:str|None=Header(None,alias='Idempotency-Key')):
    keys(body,{'id','name','owner_email','owner_display_name'})
    brand_id=text(body.get('id'),60,'معرف البراند'); name=text(body.get('name'),200,'اسم البراند')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,59}',brand_id) or not name:
        raise db.error(422,'BRAND_IDENTITY_INVALID','أدخل هوية البراند بشكل صحيح.')
    email=email_delivery.normalize_email(text(body.get('owner_email',''),254,'بريد مالك البراند'))
    display_name=text(body.get('owner_display_name',''),200,'اسم المسؤول')
    with db.tx() as conn:
        auth.require_owner_step_up(conn,user)
        def perform():
            if conn.execute('SELECT 1 FROM brands WHERE id=%s',(brand_id,)).fetchone():
                raise db.error(409,'BRAND_OR_USERNAME_EXISTS','البراند موجود؛ لا يُعاد إنشاؤه.')
            conn.execute('INSERT INTO brands(id,name) VALUES(%s,%s)',(brand_id,name))
            conn.execute('INSERT INTO app_settings(brand_id,settings) VALUES(%s,%s)',(brand_id,db.json({})))
            conn.execute("INSERT INTO identity_channels(brand_id,channel,state,enabled) VALUES(%s,'guest','active',true),(%s,'email','testing',true),(%s,'sms','unconfigured',false),(%s,'whatsapp','unconfigured',false)",(brand_id,brand_id,brand_id,brand_id))
            identifier=db.new_id('user_')
            conn.execute("INSERT INTO users(id,display_name,role,brand_id,email,active,identity_kind,invitation_status) VALUES(%s,%s,'owner',%s,%s,false,'pending_email','pending_email')",(identifier,display_name,brand_id,email))
            challenge=auth._create_email_token(conn,brand=brand_id,email=email,purpose='add_owner',user_id=identifier,created_by=user['id'])
            db.audit(conn,user['id'],'brand.created','brand',brand_id,{'owner_user_id':identifier,'invitation_status':'pending_email'})
            return {'brand_id':brand_id,'name':name,'owner_user_id':identifier,'invitation_status':'pending_email','challenge_id':challenge,'customer_path':'/?brand_id='+brand_id}
        return db.idempotent(conn,user['id'],idem,'brand.create',body,perform)


@router.patch('/admin/brand-services')
def brand_services(body: dict, user=Depends(auth.require_permission('brand_services_manage')), idem: str | None=Header(None,alias='Idempotency-Key')):
    keys(body, {'services', 'reason'})
    services = body.get('services')
    keys(services, {'delivery', 'pickup', 'car_pickup'})
    if not services or any(type(value) is not bool for value in services.values()):
        raise db.error(422, 'SERVICE_POLICY_INVALID', 'اختر خدمات البراند المراد تفعيلها أو إيقافها.')
    reason = text(body.get('reason', ''), 500, 'سبب تعديل الخدمات')
    if not reason:
        raise db.error(422, 'SERVICE_POLICY_REASON_REQUIRED', 'اكتب سبب تعديل الخدمات.')
    with db.tx() as conn:
        def perform():
            row = conn.execute('SELECT settings FROM brands WHERE id=%s FOR UPDATE', (user['brand_id'],)).fetchone()
            previous = row['settings'].get('services', {})
            settings = row['settings'] | {'services': previous | services}
            conn.execute('UPDATE brands SET settings=%s WHERE id=%s', (db.json(settings), user['brand_id']))
            db.audit(conn, user['id'], 'brand.services.updated', 'brand', user['brand_id'],
                     {'previous': previous, 'services': settings['services'], 'reason': reason})
            return {'services': settings['services'], 'existing_orders_preserved': True}
        return db.idempotent(conn, user['id'], idem, 'brand.services.update', body, perform)


@router.post('/admin/delivery-only')
def delivery_only(body: dict, user=Depends(owner), idem: str | None=Header(None,alias='Idempotency-Key')):
    keys(body, {'branch_ids', 'confirm', 'reason'})
    if type(body.get('confirm', False)) is not bool:
        raise db.error(422, 'CONFIRMATION_INVALID', 'راجع أثر التغيير ثم أكد الحفظ.')
    reason = text(body.get('reason', ''), 500, 'سبب تعديل الخدمات')
    with db.tx() as conn:
        scope = branch_ids(conn, body.get('branch_ids', []), user['brand_id'])
        if not scope:
            raise db.error(422, 'BRANCH_SCOPE_REQUIRED', 'حدد الفروع المراد تعديل خدماتها.')
        brand = conn.execute('SELECT settings FROM brands WHERE id=%s FOR SHARE', (user['brand_id'],)).fetchone()
        rows = [scoped(conn, 'branches', bid, user) for bid in sorted(scope)]
        warnings = []
        for row in rows:
            available, code, why = catalog.branch_service(row, 'delivery', brand_settings=brand['settings'])
            if not available:
                warnings.append({'branch_id': row['id'], 'branch_no': row['branch_no'], 'name': row['name'], 'code': code,
                                 'reason': why, 'will_accept_new_orders': False})
        preview = {'branch_ids': scope, 'disabled_services': ['pickup', 'car_pickup'], 'delivery_will_be_enabled': False,
                   'warnings': warnings, 'existing_orders_preserved': True}
        if not body.get('confirm'):
            return dict(preview, saved=False, confirmation_required=True)
        if not reason:
            raise db.error(422, 'SERVICE_POLICY_REASON_REQUIRED', 'اكتب سبب تعديل الخدمات.')
        def perform():
            for row in rows:
                settings = row['settings'] | {'pickup_enabled': False, 'car_enabled': False}
                conn.execute('UPDATE branches SET settings=%s,updated_at=now() WHERE id=%s', (db.json(settings), row['id']))
                db.audit(conn, user['id'], 'branch.delivery_only', 'branch', row['id'],
                         {'previous': {key: row['settings'].get(key) for key in ('pickup_enabled','car_enabled','delivery_enabled')},
                          'reason': reason, 'delivery_preserved': row['settings'].get('delivery_enabled', False)})
            return dict(preview, saved=True, confirmation_required=False)
        return db.idempotent(conn, user['id'], idem, 'branches.delivery_only', body, perform)
