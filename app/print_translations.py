"""Editable, first-party English labels for the existing KPS menu."""
from . import db

DEFAULTS = {
    'كولد برو':'Cold Brew', 'قهوة اليوم بارد':'Iced Coffee of the Day',
    'قهوة اليوم حار':'Hot Coffee of the Day', 'كوكيز شوكليت':'Chocolate Cookies',
    'V60 حار':'Hot V60', 'V60 بارد':'Iced V60', 'ماء نوفا 330 مل':'Nova Water 330 ml',
    'آيس تي خوخ':'Peach Iced Tea', 'آيس كركديه':'Iced Hibiscus', 'موهيتو':'Mojito',
    'آيس شوكليت':'Iced Chocolate', 'سموذي شمام':'Melon Smoothie', 'ماتشا':'Matcha',
    'تيراميسو':'Tiramisu', 'سينابون':'Cinnamon Roll', 'ترايفل مانجو':'Mango Trifle',
    'كروسان رول':'Croissant Roll', 'كيكة دبي':'Dubai Cake', 'براونيز كراميل':'Caramel Brownies',
    'كرانشي':'Crunchy', 'كوكونت':'Coconut', 'كرانش شوكو':'Choco Crunch',
    'كوب إضافي':'Extra Cup', 'بوكس قهوة':'Coffee Box', 'بوكس كوكيز':'Cookies Box',
    'بوكس كرانشي':'Crunchy Box', 'بوكس سينابون':'Cinnamon Rolls Box',
    'بوكس براونيز':'Brownies Box', 'الخلية':'Khaliya', 'خلية بيكان':'Pecan Khaliya',
    'خلية كلاسيك':'Classic Khaliya', 'كوب حراري':'Thermal Cup',
    'نكهة الموهيتو':'Mojito Flavor', 'لون الكوب الحراري':'Thermal Cup Color',
    'توت أسود':'Blackberry', 'توت أزرق':'Blueberry', 'باشن فروت':'Passion Fruit',
    'رمان':'Pomegranate', 'توت أحمر':'Raspberry', 'فراولة':'Strawberry',
    'وردي':'Pink', 'أسود':'Black', 'أزرق':'Blue', 'موف':'Mauve', 'أوف وايت':'Off White',
    'اختر النوع':'Type', 'كلاسيك':'Classic', 'بيكان':'Pecan',
}


def labels(product):
    settings=product.get('settings') or {}
    names=[product['name']]
    for group in settings.get('option_groups') or []:
        names.append(group['name'])
        names.extend(value['name'] for value in group.get('values') or [])
    saved=settings.get('print_labels_en') or {}
    return {name:saved.get(name) or DEFAULTS.get(name,'') for name in dict.fromkeys(names)}


def validate(value):
    if not isinstance(value,dict) or len(value)>500 or any(
        not isinstance(k,str) or not 1<=len(k)<=200 or not isinstance(v,str) or len(v)>300
        or any(ord(c)<32 for c in v) for k,v in value.items()
    ):
        raise db.error(422,'PRINT_TRANSLATIONS_INVALID','أدخل ترجمات نصية صحيحة للأصناف والخيارات.')
    return {k:v.strip() for k,v in value.items()}


def for_order(conn,order):
    """Use the same scoped labels in browser receipts and branch print jobs."""
    items=order['snapshot'].get('items',[])
    products=conn.execute('SELECT id,name,settings FROM products WHERE brand_id=%s AND id=ANY(%s)',
                          (order['brand_id'],[i.get('product_id') for i in items if i.get('product_id')])).fetchall()
    configured={p['id']:((p['settings'].get('print_labels_en') or {}) | labels(p)) for p in products}
    result={}
    for item in items:
        names=[item['name']]
        for unit in item.get('units',[]):
            for group in (unit.get('option_snapshot') or {}).values():
                names.append(group.get('name',''))
                names.extend(c.get('name',c.get('code','')) for c in group.get('choices',[]))
        saved=configured.get(item.get('product_id'),{})
        target=result.setdefault(item.get('product_id',''),{})
        target.update({name:saved.get(name) or DEFAULTS.get(name) or name for name in names})
    return result
