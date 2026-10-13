"""Public notice before collection; facts come from owner-controlled seller data."""
from html import escape
from typing import Literal
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from . import auth, db, invoice_profiles
router=APIRouter()
PRIVACY_AR='نحفظ اسمك ورقم جوالك وعناوينك وطلباتك لتقديم الخدمة وتسهيل طلباتك القادمة. نشارك البيانات الضرورية فقط مع مزودي الخدمات المرتبطة بطلبك، ونحافظ على خصوصيتك ولا نبيع بياناتك. يمكنك طلب تعديل بياناتك أو حذفها وفق الأنظمة. تبقى جلسة دخولك لمدة تصل إلى 30 يومًا، مع الاحتفاظ بالسجلات المطلوبة نظامًا.'
TERMS_AR='يجب تقديم بيانات صحيحة لإتمام الطلب. تخضع الطلبات لتوفر المنتجات والخدمة، وتظهر الأسعار والرسوم قبل التأكيد. يمكن تقديم الشكاوى وطلبات الإلغاء والاسترجاع وفق حقوق المستهلك.'
PRIVACY_EN='We store your name, mobile number, addresses and orders to provide our service and make future orders easier. We share only necessary information with service providers involved in your order, protect your privacy and do not sell your data. You may request correction or deletion as permitted by law. Your sign-in session lasts up to 30 days, while legally required records are retained.'
TERMS_EN='Provide accurate information to complete your order. Orders depend on product and service availability. Prices and fees are shown before confirmation. Complaints, cancellation requests and refund requests are handled according to consumer rights.'

class LegalDetails(BaseModel):
    model_config=ConfigDict(extra='forbid')
    controller_name:str=Field('',max_length=200)
    registration_number:str=Field('',max_length=80)
    business_address:str=Field('',max_length=500)
    contact:str=Field('',max_length=200)
    retention_policy:str=Field('',max_length=2000)
    transfer_safeguards:str=Field('',max_length=2000)

@router.get('/api/customer/legal')
def details(brand_id:str='kps'):
    with db.tx() as c:
        row=c.execute('SELECT settings FROM app_settings WHERE brand_id=%s',(brand_id,)).fetchone()
        config=(row or {}).get('settings',{}).get('customer_legal',{})
        entities=invoice_profiles.settings(c,brand_id)['entities']
        return {'details':config,'sellers':list(entities.values()),'complete':all(config.get(k) for k in LegalDetails.model_fields)}

@router.put('/api/customer/legal')
def save(body:LegalDetails,user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        c.execute("UPDATE app_settings SET settings=jsonb_set(settings,'{customer_legal}',%s),updated_at=now() WHERE brand_id=%s",(db.json(body.model_dump()),user['brand_id']))
        db.audit(c,user['id'],'customer.legal.updated','brand',user['brand_id'])
        return {'ok':True}

@router.get('/privacy',response_class=HTMLResponse)
def privacy(brand_id:str='kps',lang:Literal['ar','en']='ar'):
    return page('privacy',brand_id,lang)

@router.get('/terms',response_class=HTMLResponse)
def terms(brand_id:str='kps',lang:Literal['ar','en']='ar'):
    return page('terms',brand_id,lang)

def page(kind,brand,lang):
    info=details(brand);config=info['details'];en=lang=='en'
    title=('Privacy policy' if kind=='privacy' else 'Terms and conditions') if en else ('سياسة الخصوصية' if kind=='privacy' else 'الشروط والأحكام')
    paras=[PRIVACY_EN if en else PRIVACY_AR] if kind=='privacy' else [TERMS_EN if en else TERMS_AR]
    if kind=='privacy':
        paras += ([
          'We process contact, delivery location, order, support-message and optional photo data to fulfil requests, provide support and meet legal obligations. Security logs and essential session cookies protect access. Optional marketing is separate and is not required to order.',
          'Necessary information is shared with the fulfilling branch, delivery team, hosting provider and mobile verification provider. Order browsing never grants access from an unverified phone number.',
          'The current test service is hosted by Render in Frankfurt, Germany, outside Saudi Arabia. Approval of applicable transfer safeguards and the retention schedule is required before official launch.',
          'Use Contact us → Privacy / personal data to request access, a copy, correction or deletion, or to raise a privacy complaint. Requests are reviewed by the owner after suitable identity checks. Required financial and legal records may be retained; signing out does not delete your profile. You may complain to the competent authority, SDAIA.',
          'The verified mobile session expires 30 days after successful verification. Visiting or ordering does not extend it. A changed mobile number requires new verification.'
        ] if en else [
          'نعالج بيانات التواصل والموقع اللازم للتوصيل والطلبات ورسائل الدعم والصور الاختيارية لتنفيذ طلبك وخدمتك والوفاء بالالتزامات النظامية. تستخدم سجلات الأمان وملفات الجلسة الضرورية لحماية الدخول. التسويق اختياري ومستقل عن الطلب.',
          'تتاح البيانات الضرورية للفرع المنفذ وفريق التوصيل ومزود الاستضافة ومزود التحقق بالجوال. رقم جوال غير موثق لا يمنح الوصول إلى سجلات أي عميل.',
          'الخدمة التجريبية الحالية مستضافة لدى Render في فرانكفورت، ألمانيا، خارج المملكة العربية السعودية. يلزم اعتماد ضمانات النقل المطبقة وجدول الاحتفاظ قبل الإطلاق الرسمي.',
          'يمكنك من «تواصل معنا ← الخصوصية والبيانات الشخصية» طلب الاطلاع على بياناتك أو نسخة منها أو تصحيحها أو حذفها أو تقديم شكوى خصوصية. يراجع المالك الطلب بعد التحقق المناسب من الهوية. قد تبقى السجلات المالية والنظامية المطلوبة؛ تسجيل الخروج لا يحذف ملفك. يحق لك تقديم شكوى إلى الجهة المختصة، سدايا.',
          'تنتهي جلسة الجوال الموثق بعد 30 يومًا من نجاح التحقق. لا تمددها زيارة الموقع أو الطلب. تغيير الجوال يتطلب تحققًا جديدًا.'
        ])
    else:
        paras += ([
          'The final summary shows items, service, branch, total and delivery fees before confirmation. Payment is collected on arrival under the selected method. Availability and coverage are checked by the server.',
          'Contact us to request cancellation, a change, return or compensation. A request is not an automatic cancellation or payout; the team reviews fulfilment status and applicable consumer rights, including the nature of prepared food. This notice does not waive mandatory consumer rights.'
        ] if en else [
          'يعرض ملخص الطلب الأصناف والخدمة والفرع والإجمالي ورسوم التوصيل قبل التأكيد. الدفع عند الاستلام حسب الطريقة المختارة. يتحقق الخادم من التوفر ونطاق الخدمة.',
          'لطلب الإلغاء أو التعديل أو الاسترجاع أو التعويض استخدم «تواصل معنا». الطلب لا ينفذ إلغاءً أو صرفًا تلقائيًا؛ يراجع الفريق حالة التجهيز وحقوق المستهلك المطبقة وطبيعة الأغذية المحضرة. لا تحد هذه الشروط من حقوقك النظامية الإلزامية.'
        ])
    sellers='<br>'.join(escape(x.get('legal_name',''))+((' — VAT number: ' if en else ' — الرقم الضريبي: ')+escape(x['vat_number']) if x.get('vat_number') else '') for x in info['sellers'])
    labels={'controller_name':('Data controller','جهة التحكم بالبيانات'),'registration_number':('Registration','السجل التجاري'),'business_address':('Business address','عنوان المنشأة'),'contact':('Contact','التواصل'),'retention_policy':('Retention','الاحتفاظ'),'transfer_safeguards':('Transfer safeguards','ضمانات النقل')}
    fields=''.join(f'<p><b>{labels[k][0 if en else 1]}:</b> {escape(str(v))}</p>' for k,v in config.items() if k in labels and v)
    pending='' if info['complete'] else '<p class="notice">'+('Test version: some legal disclosures await owner confirmation before official launch.' if en else 'نسخة تجريبية: بعض الإفصاحات النظامية تنتظر توثيق المالك قبل الإطلاق الرسمي.')+'</p>'
    body=''.join('<p>'+escape(x)+'</p>' for x in paras)
    return HTMLResponse(f'<!doctype html><html lang="{lang}" dir="{"ltr" if en else "rtl"}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/customer.css"></head><body><main class="page-shell"><article class="panel legal-page"><a href="/?brand_id={escape(brand,quote=True)}">{"Menu" if en else "المنيو"}</a> · <a href="/{kind}?lang={"ar" if en else "en"}&amp;brand_id={escape(brand,quote=True)}">{"العربية" if en else "English"}</a><h1>{title}</h1>{body}<h2>{"Seller information" if en else "بيانات المنشأة"}</h2><p>{sellers}</p>{fields}<p>{"Contact: use Contact us on the menu." if en else "التواصل: عبر مركز «تواصل معنا» في صفحة المنيو."}</p>{pending}<small>2026-10-13</small></article></main></body></html>',headers={'Cache-Control':'no-store'})
