"""KPS presentation requested by owner. No settings or operational writes."""
from html import escape
from fastapi.responses import HTMLResponse

CONTROLLER = 'مؤسسة ريم فهاد لافي الحربي لتقديم الوجبات'
CR = '7037181620'
VAT = '311887526600003'
UPDATED = '2026-10-15'
# Paired sections keep Arabic/English coverage reviewable.
SECTIONS = {
 'privacy': [
  ('بيانات الخدمة', 'Service information',
   'تقدم KPS COFFEE — كبز قهوة مختصة خدمة استعراض المنيو والطلب والمتابعة والدعم. توضح هذه السياسة البيانات التي نعالجها عند استخدام الخدمة وأغراضها وحقوقك بشأنها.',
   'KPS COFFEE — كبز قهوة مختصة provides menu browsing, ordering, tracking and support. This policy explains the information processed when you use the service, its purposes and your rights.'),
  ('البيانات التي نجمعها', 'Information we collect',
   'نجمع البيانات التي تقدمها مباشرة: الاسم والجوال الموثق، والعنوان والموقع عند الحاجة للتوصيل، وبيانات السيارة للاستلام بالسيارة فقط، وسجل الطلبات ورسائل الدعم والصور التي تختار إرفاقها. نعالج أيضًا معلومات الجلسة وسجلات الأمان اللازمة لحماية الوصول ومنع إساءة الاستخدام.',
   'We collect information you provide directly: your name and verified mobile, delivery address and location where needed, vehicle details only for car pickup, order history, support messages and images you choose to attach. We also process session information and security logs needed to protect access and prevent misuse.'),
  ('المطلوب والاختياري', 'Required and optional information',
   'الاسم والجوال الموثق مطلوبان لإتمام أول طلب. بيانات التوصيل مطلوبة لخدمة التوصيل وبيانات السيارة لخدمة الاستلام بالسيارة. لا يمكن إتمام الخدمة دون بياناتها اللازمة. السماح بموقع الجهاز لاقتراح الفرع والصور في الدعم والملاحظات الإضافية أمور اختيارية؛ يمكنك تحديد موقع التوصيل يدويًا. تصفح المنيو لا يتطلب إنشاء حساب.',
   'Your name and verified mobile are required to complete your first order. Delivery details are needed for delivery, and vehicle details for car pickup. We cannot fulfil a service without its necessary information. Device location permission for branch suggestions, support images and additional notes are optional; you can specify the delivery location manually. Browsing the menu does not require an account.'),
  ('أغراض المعالجة', 'Processing purposes',
   'نستخدم البيانات لتنفيذ الطلب والتواصل بشأنه والتحقق من الهوية وعرض سجل الطلبات وتقديم الدعم ومعالجة الشكاوى وطلبات الحقوق وحماية الخدمة والوفاء بالالتزامات النظامية والمحاسبية. لا نبيع بياناتك. أي تسويق اختياري منفصل عن متطلبات الطلب. يلزم توثيق الأساس النظامي المعتمد لكل غرض قبل الإطلاق العام.',
   'We use information to fulfil and communicate about orders, verify identity, show order history, provide support, handle complaints and rights requests, protect the service and meet legal and accounting obligations. We do not sell your information. Any optional marketing is separate from ordering requirements. The approved legal basis for each purpose must be documented before public launch.'),
  ('الحفظ ومزودو الخدمة', 'Storage and service providers',
   'تُستضاف بعض بيانات الخدمة لدى مزودي خدمات تقنية. تقتصر مشاركة البيانات اللازمة على الفرع المنفذ وفريق التوصيل ومزودي الاستضافة والتحقق والدعم المرتبطين بالخدمة، بحسب الحاجة والصلاحيات. الاستضافة التجريبية الحالية لدى Render في فرانكفورت بألمانيا، خارج المملكة. اعتماد الأساس النظامي وضمانات النقل المطبقة ما زال مطلوبًا قبل الإطلاق العام؛ هذه السياسة لا تعني اكتمال ملف الامتثال للنقل.',
   'Some service information is hosted by technology providers. Necessary information is shared with the fulfilling branch, delivery team and hosting, verification and support providers involved in the service, according to need and permissions. The current test hosting is with Render in Frankfurt, Germany, outside Saudi Arabia. Approval of the applicable legal basis and transfer safeguards is still required before public launch; this policy does not signify completed transfer compliance.'),
  ('الاحتفاظ والحذف', 'Retention and deletion',
   'يقتصر الاحتفاظ على ما يلزم لأغراض الخدمة والالتزامات النظامية والمحاسبية وتسوية المطالبات. قد تُستثنى السجلات الواجب حفظها من طلب الحذف للمدة المطلوبة نظامًا، مع تقييد استخدامها. يلزم اعتماد جدول مدد الاحتفاظ وإجراءات الإتلاف قبل الإطلاق العام. تسجيل الخروج لا يحذف الحساب أو سجل الطلبات.',
   'Retention is limited to service purposes, legal and accounting obligations and resolution of claims. Records subject to mandatory retention may be excluded from deletion for the legally required period, with restricted use. The retention schedule and destruction procedures must be approved before public launch. Signing out does not delete your account or order history.'),
  ('حقوقك وطلبات الخصوصية', 'Your rights and privacy requests',
   'يمكنك طلب الاطلاع على بياناتك والحصول على نسخة واضحة منها وتصحيحها أو استكمالها وطلب حذفها أو إتلافها حيث ينطبق ذلك نظامًا. استخدم «تواصل معنا ← الخصوصية والبيانات الشخصية» لطلب الحقوق أو تقديم شكوى خصوصية. نتحقق من الهوية بالقدر المناسب لحماية بياناتك وبيانات الآخرين قبل تنفيذ الطلب، ونوضح نتيجة المراجعة وأي استثناء نظامي. يمكنك أيضًا تقديم شكوى إلى الجهة المختصة بحماية البيانات الشخصية، سدايا.',
   'You may request access to your information, a clear copy, correction or completion, and deletion or destruction where applicable by law. Use Contact us → Privacy / personal data to exercise rights or submit a privacy complaint. We apply proportionate identity checks to protect your information and others before acting, and explain the outcome and any legal exception. You may also complain to the competent personal data protection authority, SDAIA.'),
  ('الجلسة وملفات الارتباط', 'Sessions and cookies',
   'نستخدم ملفات الجلسة الضرورية والتخزين المحلي لحماية الدخول وحفظ تفضيل اللغة وحالة استخدام الخدمة. تنتهي جلسة الجوال الموثق بعد 30 يومًا من نجاح التحقق، ولا تمددها الزيارة أو الطلب. تغيير الجوال يتطلب تحققًا جديدًا. روابط التواصل الخارجية تخضع عند فتحها لسياسات المنصات المعنية؛ لا نحتاج أدوات تتبع خارجية لعرض أيقوناتها.',
   'We use essential session cookies and local storage to protect sign-in and retain language preferences and service state. The verified mobile session expires 30 days after verification; visits and orders do not extend it. Changing your mobile requires new verification. External social links are subject to the respective platforms’ policies when opened; displaying their icons does not require external tracking tools.')
 ],
 'terms': [
  ('الخدمة وبيانات العميل', 'Service and customer information',
   'تقدم KPS COFFEE — كبز قهوة مختصة خدمات الطلب من الفروع المتاحة. قدّم بيانات صحيحة وحدّث ما يلزم منها وأكمل التحقق عند طلبه. أنت مسؤول عن مراجعة رقم التواصل والفرع والخدمة وتفاصيل الطلب قبل التأكيد.',
   'KPS COFFEE — كبز قهوة مختصة offers ordering from available branches. Provide accurate information, update it as needed and complete verification when required. Review your contact number, branch, service and order details before confirming.'),
  ('المنيو وتوفر الخدمة', 'Menu and service availability',
   'تخضع الأصناف والخيارات والفروع ونطاق الخدمة وأوقات العمل للتوفر الفعلي. اختيار صنف في السلة لا يضمن توفره حتى تأكيد الطلب. عند تعذر التنفيذ يتواصل الفريق معك بشأن الحل المتاح وحقوقك المطبقة.',
   'Products, options, branches, coverage and opening hours are subject to actual availability. Adding an item to the basket does not guarantee availability until confirmation. If fulfilment becomes impossible, the team will contact you about the available resolution and your applicable rights.'),
  ('الأسعار والضريبة والرسوم', 'Prices, tax and fees',
   'الأسعار بالريال السعودي. تظهر الأصناف والإجمالي ورسوم التوصيل والحد الأدنى المطبق قبل التأكيد. تُعامل الضريبة وفق إعداد البائع الفعلي: الأسعار شاملة ضريبة القيمة المضافة للبائع المفعّل ضريبيًا، ولا تُفرض ضريبة باسم بائع غير مسجل. يجب مراجعة الرقم الضريبي المخزن مقابل الشهادة الرسمية قبل الإطلاق العام.',
   'Prices are in Saudi riyals. Items, the total, delivery fees and any applicable minimum order are shown before confirmation. Tax follows the actual seller configuration: prices include VAT for a VAT-enabled seller; VAT is not charged in the name of an unregistered seller. The stored VAT number must be matched against the official certificate before public launch.'),
  ('طرق الاستلام والتوصيل', 'Pickup and delivery',
   'للاستلام من الفرع اختر الفرع الصحيح وتابع حالة التجهيز. للاستلام بالسيارة أدخل بيانات السيارة اللازمة وأبلغ الفرع بوصولك عبر الخدمة المتاحة. للتوصيل قدّم عنوانًا وموقعًا دقيقين ورقمًا يمكن التواصل عليه. قد يؤدي الموقع غير الصحيح أو تعذر الوصول إليك إلى تأخير أو تعذر التنفيذ؛ يراجع الفريق الحل معك وفق الحقوق المطبقة.',
   'For branch pickup, select the correct branch and follow preparation status. For car pickup, provide the necessary vehicle details and notify the branch of arrival using the available service. For delivery, provide an accurate address, location and reachable contact number. An incorrect location or inability to reach you may delay or prevent fulfilment; the team reviews the resolution with you under applicable rights.'),
  ('المراجعة والتأكيد والتعديل', 'Review, confirmation and amendments',
   'راجع ملخص الطلب والأصناف والخيارات والكميات والخدمة والموقع والرسوم قبل التأكيد، وتابع حالة الطلب في النظام. أرسل طلب التعديل أو الإلغاء عبر «تواصل معنا» واربط الطلب إن أمكن. تقديم الطلب لا ينفذ التعديل أو الإلغاء تلقائيًا؛ تعتمد المعالجة على حالة القبول والتجهيز والتنفيذ والحقوق النظامية.',
   'Review the order summary, items, options, quantities, service, location and fees before confirmation, then follow its status in the system. Request amendments or cancellation through Contact us and link the order where possible. Submission does not automatically amend or cancel an order; handling depends on acceptance, preparation, fulfilment status and statutory rights.'),
  ('الدفع عند الاستلام', 'Payment on arrival',
   'الدفع عند الاستلام وفق الطرق المتاحة للطلب: نقدًا أو بجهاز الشبكة أو بتقسيم المبلغ بينهما عند إتاحته. لا تعني هذه الشروط تفعيل دفع البطاقات عبر الإنترنت. راجع الإجمالي وطريقة الدفع والباقي مع الموظف، ولا ترسل الرقم السري أو CVV أو رمز OTP في محادثات الدعم.',
   'Payment is collected on arrival using the methods available for the order: cash, card terminal or a split between them where offered. These terms do not indicate that online card payment is active. Review the total, payment method and change with the employee. Never send your card PIN, CVV or OTP in support messages.'),
  ('التوقيت والانقطاعات', 'Estimates and interruptions',
   'أوقات التجهيز والتوصيل تقديرية وقد تتأثر بالازدحام والطريق والطقس أو الأعطال التشغيلية والتقنية. تابع حالة طلبك وتواصل معنا عند التأخير. لا تلغي هذه الظروف حقوقك النظامية في معالجة التأخير أو تعذر التنفيذ.',
   'Preparation and delivery times are estimates and may be affected by demand, traffic, weather or operational and technical interruptions. Follow order status and contact us about delays. These circumstances do not remove statutory rights concerning delay or failure to fulfil.'),
  ('الشكاوى والاسترداد والخصوصية', 'Complaints, refunds and privacy',
   'تعالج الشكاوى وطلبات التعويض أو الاسترداد عبر «تواصل معنا» وفق سياسة الاسترجاع وحالة الطلب وحقوق المستهلك. تُحال مشكلات التوصيل لمدير التوصيل والمراجعات المالية للمخولين، دون صرف تلقائي بمجرد تقديم البلاغ. توضح سياسة الخصوصية كيفية معالجة البيانات اللازمة للخدمة.',
   'Complaints and compensation or refund requests are handled through Contact us under the refund policy, order status and consumer rights. Delivery issues go to delivery management and financial reviews to authorized personnel, without automatic payout on submission. The privacy policy explains processing of information needed for the service.'),
  ('الاستخدام والحقوق والنظام المطبق', 'Use, rights and applicable law',
   'يُمنع الاحتيال وانتحال الهوية وإساءة استخدام الطلبات أو الدعم ومحاولات الوصول لبيانات الآخرين. قد نقيّد الاستخدام المسيء بعد المراجعة لحماية الخدمة، مع حفظ الحقوق النظامية. لا تستبعد هذه الشروط حقوق المستهلك الإلزامية في السعودية. تسري الأنظمة السعودية وتختص الجهات السعودية المختصة بالنزاعات والشكاوى. توفر العربية والإنجليزية المعنى نفسه؛ يُعالج أي اختلاف بما يحفظ الحقوق الإلزامية.',
   'Fraud, impersonation, abuse of ordering or support, and attempts to access others’ information are prohibited. Abusive use may be restricted after review to protect the service while preserving statutory rights. These terms do not exclude mandatory Saudi consumer rights. Saudi law applies and competent Saudi authorities handle disputes and complaints. Arabic and English convey the same meaning; any discrepancy is handled in a way that preserves mandatory rights.')
 ],
 'refunds': [
  ('تقديم الطلب ومتابعته', 'Submitting and tracking a request',
   'لإلغاء طلب أو تعديله أو طلب استرجاع أو استرداد أو تعويض، افتح «تواصل معنا» واختر التصنيف المناسب واربط الطلب المعني إن توفر. اشرح المشكلة وتحصل على رقم بلاغ أو مرجع متابعة، وتتابع الحالة والرسائل ونتيجة المراجعة من داخل الخدمة.',
   'To cancel or amend an order or request a return, refund or compensation, open Contact us, select the relevant category and link the related order if available. Describe the issue and receive a case or reference number. Follow status, messages and the review outcome within the service.'),
  ('الإلغاء وحالة التجهيز', 'Cancellation and preparation status',
   'تقديم الطلب لا يلغي تلقائيًا طلبًا مقبولًا أو قيد التجهيز ولا ينشئ استردادًا ماليًا تلقائيًا. تعتمد المراجعة على وقت الطلب وحالة التجهيز والتنفيذ وطبيعة المنتج وحقوق المستهلك المطبقة. لا تتضمن هذه السياسة منعًا شاملًا للاسترداد.',
   'Submitting a request does not automatically cancel an accepted order or one being prepared, or create an automatic refund. Review considers the time of the request, preparation and fulfilment status, the nature of the product and applicable consumer rights. This policy does not impose a blanket no-refund rule.'),
  ('نقص الأصناف أو الخطأ أو الجودة', 'Missing, wrong or quality-affected items',
   'إذا كان هناك صنف ناقص أو غير مطابق أو مشكلة جودة، وضّح الصنف والمشكلة واربط الطلب. يمكنك إرفاق صور مفيدة اختياريًا دون بيانات غير لازمة. يراجع الفريق الوقائع والحل المناسب، بما قد يشمل الاستبدال أو التعويض أو الاسترداد بحسب الحالة والحقوق المطبقة.',
   'For a missing or wrong item or a quality complaint, identify the item and issue and link the order. Helpful photos are optional and should exclude unnecessary personal information. The team reviews the facts and appropriate resolution, which may include replacement, compensation or refund according to the case and applicable rights.'),
  ('التوصيل والتأخير', 'Delivery issues and delays',
   'توجّه مشكلات التوصيل والتأخير والسائقين إلى إدارة التوصيل، حتى إذا بدأت المحادثة مع الفرع، مع حفظ سياق البلاغ. وضّح المشكلة وتوقيت حدوثها ليتم التحقق من حالة الطلب ومراجعة الاستحقاق والحل.',
   'Delivery, delay and driver issues are routed to delivery management, including when a conversation starts with a branch, while retaining the case context. Describe the issue and when it occurred so the team can check order status and review entitlement and resolution.'),
  ('المراجعة المالية وأمان التواصل', 'Financial review and safe communication',
   'تُحال طلبات الاسترداد المالي إلى المسؤول المخوّل بالمراجعة والاعتماد، وتُبلّغ بنتيجة المراجعة والمبلغ والطريقة والمدة المتوقعة عند الموافقة بحسب طريقة الدفع الفعلية. لا ترسل الرقم السري للبطاقة أو CVV أو رمز التحقق OTP في محادثات الدعم. لا يلزم إرفاق صورة لتقديم البلاغ.',
   'Financial refund requests go to the authorized reviewer for approval. If approved, you are informed of the outcome, amount, method and expected timing according to the actual payment method. Never send a card PIN, CVV or OTP in support chat. An image is not required to submit a case.'),
  ('حقوقك محفوظة', 'Your rights remain protected',
   'لا تسقط هذه السياسة حقوق المستهلك النظامية الإلزامية، ولا يمنع مسار المراجعة الداخلي التواصل مع الجهات المختصة في السعودية. تُعالج البيانات المرتبطة بالبلاغ وفق سياسة الخصوصية.',
   'This policy does not waive mandatory statutory consumer rights. Internal review does not prevent contacting the competent authorities in Saudi Arabia. Information associated with the case is processed under the privacy policy.')
 ]
}


def render(kind, lang):
    en = lang == 'en'
    title = {'privacy':('سياسة الخصوصية','Privacy policy'), 'terms':('الشروط والأحكام','Terms and conditions'), 'refunds':('سياسة الاسترجاع والإلغاء','Refund and cancellation policy')}[kind][en]
    sections = ''.join(f'<section><h2>{escape(s[en])}</h2><p>{escape(s[2+en])}</p></section>' for s in SECTIONS[kind])
    controller = ''
    if kind == 'privacy':
        fields = [('Data controller' if en else 'جهة التحكم بالبيانات', CONTROLLER), ('Operator of' if en else 'المشغلة لعلامة', 'KPS COFFEE — كبز قهوة مختصة'), ('Commercial Registration' if en else 'السجل التجاري', CR), ('Contact' if en else 'التواصل', '+966 55 047 2662' if en else '0550472662')]
        controller = '<section id="data-controller" class="controller-details"><h2>'+('Data Controller Details' if en else 'بيانات جهة التحكم')+'</h2><dl>'+''.join(f'<dt>{escape(k)}</dt><dd dir="auto">{escape(v)}</dd>' for k,v in fields)+'</dl></section>'
    menu = 'Menu' if en else 'المنيو'
    contact = 'Contact us' if en else 'تواصل معنا'
    date = 'Effective / last updated' if en else 'تاريخ النفاذ / آخر تحديث'
    update = 'Updates will be published here with their effective date and do not remove mandatory rights relating to previous orders.' if en else 'تُنشر التحديثات هنا مع تاريخ نفاذها، ولا تُسقط الحقوق الإلزامية المتعلقة بالطلبات السابقة.'
    return HTMLResponse(f'''<!doctype html><html lang="{lang}" dir="{'ltr' if en else 'rtl'}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} | KPS COFFEE</title><link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/customer.css"></head><body><main class="page-shell"><article class="panel legal-page"><nav aria-label="{'Policy navigation' if en else 'تصفح السياسات'}"><a href="/?brand_id=kps">{menu}</a> · <a href="/{kind}?lang={'ar' if en else 'en'}&amp;brand_id=kps">{'العربية' if en else 'English'}</a> · <a href="/?brand_id=kps#contact">{contact}</a></nav><p class="policy-brand"><b>KPS COFFEE</b><br><span lang="ar">كبز قهوة مختصة</span></p><h1>{title}</h1>{sections}{controller}<p class="small">{update}</p><p class="small muted">{date}: <time datetime="{UPDATED}">{UPDATED}</time></p></article></main></body></html>''', headers={'Cache-Control':'no-store'})
