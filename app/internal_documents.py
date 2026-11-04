"""Private owner-only legal documents. No static/public document mount."""
from base64 import b64encode
from html import escape
from pathlib import Path
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from . import auth, db
from .channel_commercial import REEM, SAMI_NAME, SAMI_CR

router = APIRouter(prefix='/api/internal-documents')


def agreement_html():
    logo = b64encode((Path(__file__).resolve().parents[1]/'public/assets/kps-logo-fb911a7b4bb4.png').read_bytes()).decode()
    reem, sami = escape(REEM['legal_name']), escape(SAMI_NAME)
    sections = [
        ('نطاق التشغيل والبيع', f'تتولى {reem} تشغيل قناة KPS ORDER باسمها أمام العميل النهائي، وتكون البائع ومحصل كامل مدفوعات العميل ومشغل خدمة التوصيل. تتولى {sami} تنفيذ طلبات الفروع المملوكة لها، وتستحق تسوية داخلية مقابل مبيعات فروعها. لا يغير هذا الاتفاق ملكية أي فرع أو أصل لأي من الطرفين.'),
        ('الفوترة وخدمة العميل', f'تتولى {reem} إصدار مستند البيع للعميل. لا تصدر فاتورة ضريبية مبسطة إلا بعد استيفاء متطلبات الفوترة الإلكترونية والضريبة وتفعيل بوابة الإصدار المعتمدة. عند عدم الاستيفاء يبقى الإيصال غير ضريبي بوضوح. لا تظهر {sami} بصفتها بائع العميل في هذه القناة ما دام هذا النموذج معتمدًا.'),
        ('تنفيذ الطلبات والتحصيل', 'يسجل النظام الفرع المنفذ ومالكه بصورة مستقلة عن بائع القناة. يلتزم الفرع بصحة التجهيز والجودة والوقت التشغيلي، ويتولى مشغل التوصيل الإسناد والتسليم والتحصيل ومتابعة شكاوى التوصيل. تسجل وسائل الدفع والتحصيل الفعلية دون تخزين أسرار البطاقات.'),
        ('التسوية الداخلية', f'تسجل حصة الفرع المملوك لـ{reem} داخل دفاترها. وتحسب حصة الفرع المملوك لـ{sami} باعتبارها المستفيد من التسوية، ثم تسوي {reem} المستحق داخليًا بعد المراجعة والمطابقة. أساس المراجعة هو قيمة أصناف الفرع مع فصل رسوم التوصيل والضريبة والتعديلات. لا يعد هذا الاتفاق بمفرده اعتمادًا لقيد ضريبي بين المنشأتين أو أمر صرف، وتثبت المعالجة النهائية وفق التسجيل الفعلي والسياسة المحاسبية المعتمدة.'),
        ('إيرادات التوصيل', f'تعود رسوم التوصيل المحصلة عبر KPS ORDER إلى حسابات قناة التوصيل لدى {reem}. ولا تضاف تلقائيًا إلى مستحق الفرع، إلا بقرار لاحق صريح ومعتمد يحدد نطاقه وتاريخ سريانه.'),
        ('الاسترداد والتعويض والتعديلات', f'تدير {reem} طلبات الاسترداد والتعويض أمام العميل وفق الصلاحيات المعتمدة. إذا ثبت ارتباط السبب بتجهيز الفرع أو جودته، يحمل الأثر الداخلي على تسوية الفرع بعد المراجعة والاعتماد. وإذا كان السبب متعلقًا بالتوصيل أو القناة، يحمل على حسابات قناة {reem}. يسجل السبب والمبلغ والطلب الأصلي ومرجع الاسترداد وهوية المعتمد وتاريخ الاعتماد، دون تعديل التاريخ المالي أو إصدار رد مالي تلقائي.'),
        ('المحاسبة والضريبة والرقابة', 'تتبع المعالجة الضريبية والمحاسبية بين المنشأتين التسجيل الفعلي المثبت والسياسة التي يعتمدها المحاسب المسؤول والمخولون. لا يفترض تسجيل ضريبي أو إعفاء غير موثق لأي طرف. تحفظ مراجع بائع العميل والفرع ومالك الفرع والمستفيد ومشغل التوصيل ومصدر الفاتورة والمبالغ والضريبة والتعديلات وسجل التدقيق. تبقى البيانات التاريخية كما سجلت، ولا يعاد نسبتها بأثر رجعي.'),
        ('السريان والتعديل والسرية', 'يسري الاتفاق بعد توقيعه واعتماده من الطرفين بتاريخ التوقيع المبين أدناه. يلزم أي تغيير في النموذج أو نسب التسوية أو ملكية الفروع أو التسجيل الضريبي قرار مكتوب واعتماد وتاريخ سريان واضح. هذا مستند داخلي غير مخصص للنشر للعملاء؛ يحفظ في المستندات النظامية ضمن اتفاقيات التشغيل والتسوية بصلاحية المالك فقط.')]
    body=''.join(f'<section><h2>{i}. {title}</h2><p>{text}</p></section>' for i,(title,text) in enumerate(sections,1))
    return f'''<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>اتفاق تشغيل وتسوية داخلية — نهائي لإعادة التوقيع</title><style>
    @font-face{{font-family:KPS;src:url('/assets/fonts/NotoKufiArabic-Regular.ttf')}}
    *{{box-sizing:border-box}}body{{font-family:KPS,'Arial',sans-serif;direction:rtl;background:#f4f2ef;color:#292724;line-height:1.9;margin:0;font-size:13px}}main{{max-width:900px;margin:24px auto;background:white;padding:38px}}header{{display:flex;align-items:center;gap:20px;border-bottom:2px solid #e78225;padding-bottom:16px}}header img{{width:74px;height:74px;object-fit:contain}}h1{{font-size:23px;margin:0}}h2{{font-size:16px;margin:18px 0 5px}}p{{margin:5px 0 12px}}.tag{{color:#7c4b19;font-size:12px}}table{{width:100%;border-collapse:collapse;direction:rtl;margin:18px 0;table-layout:fixed}}th,td{{border:1px solid #ded8d1;padding:10px;text-align:right;vertical-align:top;overflow-wrap:anywhere}}th{{background:#faf4ed}}th:first-child{{width:18%}}.sign td{{height:100px}}section{{break-inside:avoid}}footer{{border-top:1px solid #ddd;margin-top:20px;padding-top:10px;font-size:11px}}@media(max-width:600px){{main{{padding:18px;margin:0}}body{{font-size:12px}}th,td{{padding:6px}}}}@media print{{@page{{size:A4;margin:15mm}}body{{background:white}}main{{margin:0;padding:0;max-width:none}}table,.sign{{break-inside:avoid}}}}
    </style></head><body><main><header><img src="data:image/png;base64,{logo}" alt="KPS COFFEE"><div><h1>اتفاق تشغيل وتسوية داخلية</h1><div>KPS ORDER · KPS COFFEE — كبز قهوة مختصة</div><div class="tag">مستند داخلي · غير مخصص للنشر للعملاء · نسخة نهائية لإعادة التوقيع</div></div></header>
    <table dir="rtl"><thead><tr><th>البيان</th><th>الطرف الأول</th><th>الطرف الثاني</th></tr></thead><tbody><tr><th>الاسم النظامي</th><td>{reem}</td><td>{sami}</td></tr><tr><th>السجل التجاري</th><td>7037181620</td><td>7049120772</td></tr><tr><th>الضريبة</th><td>311887526600003</td><td>وفق التسجيل الفعلي المثبت؛ لا تفترض هذه النسخة تسجيلًا أو رقمًا ضريبيًا.</td></tr><tr><th>العنوان الوطني</th><td>EDPC7048<br>{escape(REEM['address']).replace(chr(10),'<br>')}</td><td>وفق مستندات المنشأة المعتمدة.</td></tr></tbody></table>
    {body}<h2>الاعتماد والتوقيع</h2><p>أعدت هذه النسخة بتحديث المحتوى؛ لا تتضمن توقيعات سابقة ولا تعد موقعة.</p><table dir="rtl" class="sign"><tr><th>البيان</th><th>{reem}</th><th>{sami}</th></tr><tr><th>الاسم والصفة والتوقيع</th><td>الاسم والصفة: __________________<br>التوقيع: __________________</td><td>الاسم والصفة: __________________<br>التوقيع: __________________</td></tr><tr><th>تاريخ التوقيع</th><td>15-10-2026</td><td>15-10-2026</td></tr></table><footer>المستندات النظامية ← اتفاقيات التشغيل والتسوية ← KPS ORDER — اتفاق تشغيل وتسوية داخلية</footer></main></body></html>'''


@router.get('/kps-operating-agreement', response_class=HTMLResponse)
def operating_agreement(user=Depends(auth.require_roles('owner'))):
    if user['brand_id'] != 'kps':
        raise db.error(404, 'DOCUMENT_NOT_FOUND', 'المستند غير موجود ضمن صلاحياتك.')
    return HTMLResponse(agreement_html(), headers={'Cache-Control':'private, no-store', 'X-Robots-Tag':'noindex, nofollow'})
