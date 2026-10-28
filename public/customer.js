'use strict';
(() => {
  const $ = (id) => document.getElementById(id);

  const languageStorageKey='kps_customer_lang';
  let language='ar';
  try{if(localStorage.getItem(languageStorageKey)==='en')language='en';}catch{}
  const uiStrings={
    "footer_privacy":{"ar": "الخصوصية", "en": "Privacy"},
    "error_GIFT_CHANGED":{"ar":"تغير استحقاق الهدية أو توفرها. راجع السلة قبل التأكيد.","en":"Gift eligibility or availability changed. Review your basket before confirming."},
    "error_POLICY_CHANGED":{"ar":"تغيرت الإعدادات من جلسة أخرى. افتحها مجددًا.","en":"Settings changed in another session. Open them again."},
    "footer_terms":{"ar": "الشروط", "en": "Terms"},
    "footer_refunds":{"ar": "الاسترجاع", "en": "Refunds"},
    "social_accounts":{"ar": "التواصل الاجتماعي", "en": "Social accounts"},
    "legal_navigation":{"ar": "السياسات", "en": "Policies"},
    "phone_whatsapp":{"ar": "الهاتف / واتساب", "en": "Phone / WhatsApp"},
    "international_phone":{"ar": "دولي", "en": "International"},
    "contact_support_hint":{"ar": "للطلبات والشكاوى والخصوصية، افتح طلب تواصل داخل الخدمة لمتابعته برقم مرجعي.", "en": "For orders, complaints and privacy, open an in-service support request to follow it with a reference number."},
    "contact_support_action":{"ar": "فتح طلب تواصل", "en": "Open a support request"},
    "social_whatsapp":{"ar": "واتساب", "en": "WhatsApp"},
    "social_instagram":{"ar": "إنستغرام", "en": "Instagram"},
    "social_snapchat":{"ar": "سناب شات", "en": "Snapchat"},
    "social_tiktok":{"ar": "تيك توك", "en": "TikTok"},

    "branches_locations":{ar:"الفروع والمواقع",en:"Branches and locations"},
    "home":{"ar": "الرئيسية", "en": "Home"},
    "welcome_back":{"ar": "أهلًا بك", "en": "Welcome back"},
    "add_address":{"ar": "إضافة عنوان", "en": "Add address"},
    "edit_address":{"ar": "تعديل", "en": "Edit"},
    "address_label":{"ar": "اسم العنوان", "en": "Address label"},
    "coordinates_optional":{"ar": "الإحداثيات — اختياري", "en": "Coordinates — optional"},
    "refund_policy":{"ar": "سياسة الاسترجاع والاسترداد", "en": "Returns and refunds"},
    "pin_saved":{"ar": "تم حفظ الموقع. يمكنك تغييره برابط خرائط أو موقعك الحالي.", "en": "Location saved. Change it with a Maps link or your current location."},
    "pin_optional":{"ar": "يمكنك حفظ العنوان الآن وإضافة الموقع عند الطلب.", "en": "Save the address now and add its location when ordering."},
    "delete_address_confirm":{"ar": "هل تريد حذف هذا العنوان المحفوظ؟ لا تتأثر الطلبات السابقة.", "en": "Delete this saved address? Previous orders are unchanged."},
    "filter_orders":{"ar": "تصفية الطلبات", "en": "Filter orders"},
    "orders_current":{"ar": "الحالية", "en": "Current"},
    "orders_history":{"ar": "السابقة", "en": "Previous"},
    "orders_cancelled":{"ar": "الملغاة", "en": "Cancelled"},
    "no_orders_in_filter":{"ar": "لا توجد طلبات في هذه القائمة.", "en": "No orders in this list."},
    "reorder":{"ar": "إعادة الطلب", "en": "Order again"},
    "reorder_review":{"ar": "مراجعة إعادة الطلب", "en": "Review your basket"},
    "reorder_notice":{"ar": "ستُضاف الأصناف إلى السلة فقط بالأسعار الحالية. راجع الفرع والتوفر والتفاصيل قبل تأكيد طلب جديد.", "en": "Items go to your basket only, at current prices. Review the branch, availability and details before confirming a new order."},
    "replace_basket_notice":{"ar": "سيستبدل هذا محتويات السلة الحالية بعد موافقتك.", "en": "This replaces your current basket after you confirm."},
    "reorder_added":{"ar": "أُضيفت الأصناف للسلة. راجع التفاصيل؛ لم يُرسل طلب جديد.", "en": "Items added to your basket. Review the details; no new order was placed."},
    "reorder_unavailable":{"ar": "بعض الأصناف أو خياراتها غير متاحة. ستبقى ظاهرة للمراجعة أو الحذف، ولا يمكن تأكيدها قبل تصحيح السلة.", "en": "Some items or options are unavailable. They remain visible for review or removal and cannot be confirmed until the basket is corrected."},
    "error_ADDRESS_NOT_FOUND":{"ar": "العنوان غير موجود. حدّث القائمة.", "en": "Address not found. Refresh your list."},
    "error_ADDRESS_LIMIT":{"ar": "يمكنك حفظ 50 عنوانًا. احذف عنوانًا قديمًا أولًا.", "en": "You can save 50 addresses. Delete an old one first."},

    "error_SESSION_CHANGED":{"ar":"تغيرت جلسة الدخول. افتح الصفحة مجددًا.","en":"Your sign-in session changed. Open the page again."},

    "sms_try_later":{"ar":"تعذر إرسال الرمز حاليًا. حاول لاحقًا.","en":"Unable to send the code now. Please try again later."},
    "contact_guest_notice":{"ar":"أدخل بيانات التواصل لبدء رسالتك. لا يفتح رقم غير موثق طلبات أو محادثات أخرى.","en":"Enter your contact details to start your message. An unverified number cannot open other orders or conversations."},
    "start_contact":{"ar":"بدء التواصل","en":"Start contact"},

    "privacy_policy":{"ar": "سياسة الخصوصية", "en": "Privacy policy"},
    "terms_conditions":{"ar": "الشروط والأحكام", "en": "Terms and conditions"},
    "verified_checkout_notice":{"ar": "نتحقق من جوالك مرة واحدة لتسهيل طلباتك القادمة لمدة 30 يومًا. لا تحتاج كلمة مرور.", "en": "Verify your mobile once to make ordering easier for 30 days. No password needed."},
    "verified_recovery_notice":{"ar": "تستعيد طلباتك وعناوينك بعد التحقق من جوالك مجددًا. تسجيل الخروج لا يحذف بياناتك.", "en": "Verify your mobile again to recover your orders and addresses. Signing out does not delete your data."},
    "contact_type":{"ar": "نوع التواصل", "en": "Contact reason"},
    "contact_help":{"ar": "متابعة طلب أو مساعدة", "en": "Order follow-up or help"},
    "contact_suggestion":{"ar": "ملاحظة أو اقتراح", "en": "Feedback or suggestion"},
    "contact_complaint":{"ar": "شكوى", "en": "Complaint"},
    "contact_change":{"ar": "إلغاء أو تعديل طلب", "en": "Cancel or change an order"},
    "contact_refund":{"ar": "استرجاع أو تعويض", "en": "Refund or compensation"},
    "contact_delivery_problem":{"ar": "مشكلة توصيل أو تأخير", "en": "Delivery problem or delay"},
    "contact_privacy":{"ar": "الخصوصية والبيانات الشخصية", "en": "Privacy or personal data"},
    "contact_other":{"ar": "أخرى", "en": "Other"},
    "details":{"ar": "التفاصيل", "en": "Details"},
    "new_order":{"ar": "طلب جديد", "en": "New order"},
    "save_data":{"ar": "حفظ البيانات", "en": "Save details"},
    "change_mobile":{"ar": "تغيير الجوال", "en": "Change mobile"},
    "saved_addresses":{"ar": "عناويني المحفوظة", "en": "Saved addresses"},
    "choose_address":{"ar": "اختر عنوانًا", "en": "Choose an address"},
    "delete_address":{"ar": "حذف العنوان", "en": "Delete address"},
    "followup":{"ar": "رقم المتابعة", "en": "Reference"},
    "error_CUSTOMER_PHONE_VERIFICATION_REQUIRED":{"ar": "تحقق من جوالك لإتمام الطلب. حاول لاحقًا إذا تعذر إرسال الرمز.", "en": "Verify your mobile to place an order. If the code cannot be sent, try again later."},
    "error_CUSTOMER_NAME_REQUIRED":{"ar": "أدخل اسمك لإتمام الطلب.", "en": "Enter your name to continue."},
    "error_PHONE_CHANGE_UNAVAILABLE":{"ar": "لا يمكن تغيير الجوال إلى هذا الرقم.", "en": "This mobile number cannot be used for the change."},
    "checking_branch_options":{"ar":"جارٍ التحقق من الأصناف وخياراتها لهذا الفرع…","en":"Checking items and options for this branch…"},
    "branch_options_check_failed":{"ar":"تعذر التحقق من توفر الأصناف لهذا الفرع. اختر فرعًا آخر أو حدّث الصفحة.","en":"Unable to check availability at this branch. Choose another branch or refresh the page."},
    "error_AUTHENTICATION_REQUIRED":{"ar": "سجّل الدخول للوصول إلى هذه البيانات.", "en": "Sign in to access this information."},
    "error_ROLE_FORBIDDEN":{"ar": "هذه العملية غير مسموحة لحسابك.", "en": "Your account is not allowed to perform this action."},
    "error_BRAND_FORBIDDEN":{"ar": "حساب العميل لا يتبع هذا البراند.", "en": "Your customer account belongs to another brand."},
    "error_CSRF_REJECTED":{"ar": "انتهى التحقق الآمن للطلب. حدّث الصفحة.", "en": "Secure request verification expired. Refresh the page."},
    "error_ORIGIN_REJECTED":{"ar": "مصدر الطلب غير مسموح.", "en": "This request source is not permitted."},
    "error_CUSTOMER_DISABLED":{"ar": "هذا الحساب موقوف. تواصل مع الإدارة.", "en": "This account is suspended. Contact the team."},
    "error_CUSTOMER_REQUIRED":{"ar": "الطلب يتطلب جلسة عميل مخوّلة.", "en": "An authorized customer session is required."},
    "error_GUEST_ORDERING_PAUSED":{"ar": "الطلب كضيف متوقف حاليًا.", "en": "Guest ordering is currently paused."},
    "error_GUEST_CONTACT_REQUIRED":{"ar": "أدخل الاسم ورقم الاتصال.", "en": "Enter your name and contact number."},
    "error_GUEST_NAME_REQUIRED":{"ar": "أدخل الاسم.", "en": "Enter your name."},
    "error_CUSTOMER_IDENTITY_UNVERIFIED":{"ar": "تحقق من الهوية أو اختر الطلب كضيف.", "en": "Verify your identity or order as a guest."},
    "error_EMAIL_INVALID":{"ar": "أدخل بريدًا إلكترونيًا صحيحًا.", "en": "Enter a valid email address."},
    "error_PHONE_INVALID":{"ar": "أدخل رقم جوال صحيحًا مع رمز الدولة.", "en": "Enter a valid phone number including country code."},
    "error_EMAIL_PROVIDER_UNAVAILABLE":{"ar": "قناة البريد غير مهيأة حاليًا.", "en": "Email verification is currently unavailable."},
    "error_EMAIL_CHANNEL_UNAVAILABLE":{"ar": "قناة البريد غير مهيأة حاليًا.", "en": "Email verification is currently unavailable."},
    "error_EMAIL_TOKEN_INVALID":{"ar": "الرابط منتهي أو غير صالح أو مستخدم.", "en": "This link has expired, is invalid or has already been used."},
    "error_LINK_SESSION_REQUIRED":{"ar": "افتح الرابط من جلسة الحساب الذي طلب ربط البريد.", "en": "Open the link in the session that requested email linking."},
    "error_EMAIL_IDENTITY_CONFLICT":{"ar": "البريد مرتبط بحساب آخر. لا يتم دمج الحسابات تلقائيًا.", "en": "This email belongs to another account. Accounts are not merged automatically."},
    "error_STAFF_PHONE_REGISTERED":{"ar": "استخدم رقم الجوال المسجل؛ تغييره من الإدارة المخولة.", "en": "Use your registered phone number. Only authorized administration can change it."},
    "error_STAFF_PROFILE_REQUIRED":{"ar": "أكمل الاسم ورقم الاتصال.", "en": "Complete your name and contact number."},
    "error_PHONE_CHANGE_REQUIRES_VERIFICATION":{"ar": "تغيير الجوال الموثق يتطلب تحققًا مستقلاً.", "en": "Changing a verified phone number requires separate verification."},
    "error_OTP_PROVIDER_UNAVAILABLE":{"ar": "تعذر إرسال الرمز حاليًا. حاول لاحقًا.", "en": "Unable to send the code now. Please try again later."},
    "error_OTP_PROVIDER_FAILURE":{"ar": "تعذر الاتصال بمزود التحقق. حاول لاحقًا.", "en": "Unable to reach the verification provider. Try again later."},
    "error_OTP_CHANNEL_UNAVAILABLE":{"ar": "قناة التحقق غير مهيأة.", "en": "This verification channel is unavailable."},
    "error_OTP_NOT_SENT":{"ar": "لم يؤكد المزود إرسال رمز التحقق.", "en": "The provider did not confirm sending a verification code."},
    "error_OTP_EXPIRED":{"ar": "رمز التحقق منتهي أو مستهلك. اطلب رمزًا جديدًا.", "en": "This code has expired or was used. Request a new code."},
    "error_OTP_INVALID":{"ar": "رمز التحقق غير صحيح.", "en": "The verification code is incorrect."},
    "error_OTP_ALREADY_USED":{"ar": "تم استخدام طلب التحقق بالفعل.", "en": "This verification request has already been used."},
    "error_BRAND_NOT_FOUND":{"ar": "البراند غير متاح.", "en": "This brand is unavailable."},
    "error_BRANCH_NOT_FOUND":{"ar": "الفرع غير موجود.", "en": "This branch is unavailable."},
    "error_PRODUCT_NOT_FOUND":{"ar": "الصنف غير موجود ضمن هذا البراند.", "en": "This item is unavailable in this brand."},
    "error_ORDER_NOT_FOUND":{"ar": "الطلب غير موجود ضمن صلاحياتك.", "en": "This order is not available to your account."},
    "error_BRAND_SERVICE_DISABLED":{"ar": "هذه الخدمة موقوفة من إدارة البراند.", "en": "This service is paused by the brand."},
    "error_BRANCH_DISABLED":{"ar": "الفرع متوقف عن استقبال الطلبات الجديدة.", "en": "This branch is not accepting new orders."},
    "error_SERVICE_DISABLED":{"ar": "هذه الخدمة غير مفعلة لهذا الفرع.", "en": "This service is not enabled at this branch."},
    "error_DELIVERY_CITY_NOT_ELIGIBLE":{"ar": "هذا الفرع غير مشمول بخدمة التوصيل حاليًا.", "en": "This branch is not currently included in the delivery service."},
    "error_BRANCH_CONFIGURATION_UNVERIFIED":{"ar": "بيانات تشغيل هذا الفرع لم تُعتمد بعد.", "en": "Operating settings for this branch are awaiting approval."},
    "error_HOURS_UNCONFIGURED":{"ar": "ساعات استقبال هذا الفرع غير مهيأة.", "en": "Ordering hours for this branch are not configured."},
    "error_BRANCH_CLOSED":{"ar": "الفرع مغلق الآن لاستقبال الطلبات.", "en": "This branch is currently closed for new orders."},
    "error_PAYMENT_UNCONFIGURED":{"ar": "طرق الدفع لهذا الفرع غير مهيأة.", "en": "Payment methods for this branch are not configured."},
    "error_DELIVERY_HOURS_UNCONFIGURED":{"ar": "ساعات التوصيل لهذا الفرع غير مهيأة.", "en": "Delivery hours for this branch are not configured."},
    "error_DELIVERY_CLOSED":{"ar": "التوصيل مغلق الآن؛ راجع أوقات الخدمة لهذا الفرع.", "en": "Delivery is currently closed. Check the branch service hours."},
    "error_COVERAGE_UNCONFIGURED":{"ar": "نطاق التوصيل أو موقع الفرع غير مهيأ.", "en": "Delivery coverage or branch location is not configured."},
    "error_DELIVERY_FEE_UNCONFIGURED":{"ar": "رسوم التوصيل لهذا الفرع غير مهيأة.", "en": "Delivery fees for this branch are not configured."},
    "error_PRODUCT_OPTIONS_UNCONFIGURED":{"ar": "خيارات هذا الصنف بحاجة إلى مراجعة الإدارة.", "en": "This item’s options need review by the team."},
    "error_UNIT_OPTIONS_REQUIRED":{"ar": "حدد الخيارات لكل وحدة قبل إضافة الصنف.", "en": "Choose options for every unit before adding the item."},
    "error_UNIT_OPTIONS_COUNT":{"ar": "عدد اختيارات الوحدات لا يطابق الكمية.", "en": "The number of unit selections does not match the quantity."},
    "error_UNKNOWN_OPTION_GROUP":{"ar": "تم إرسال مجموعة خيارات غير معتمدة.", "en": "An unapproved option group was selected."},
    "error_OPTION_SELECTION_INVALID":{"ar": "حدد خيارًا صحيحًا لكل وحدة.", "en": "Choose a valid option for each unit."},
    "error_OPTION_VALUE_INVALID":{"ar": "الخيار المحدد غير متاح أو غير معتمد.", "en": "The selected option is unavailable or unapproved."},
    "error_OPTION_PRICE_UNCONFIGURED":{"ar": "سعر الخيار غير مهيأ.", "en": "The selected option’s price is not configured."},
    "error_PRODUCT_PRICE_INVALID":{"ar": "سعر الصنف غير صالح.", "en": "This item’s price is invalid."},
    "error_PAYMENT_METHOD_UNAVAILABLE":{"ar": "طريقة الدفع غير متاحة لهذا الفرع.", "en": "This payment method is unavailable at this branch."},
    "error_DELIVERY_ADDRESS_REQUIRED":{"ar": "حدد موقع وعنوان التوصيل.", "en": "Set your delivery location and address."},
    "error_DELIVERY_NEIGHBORHOOD_REQUIRED":{"ar": "أدخل اسم حي التوصيل بصورة واضحة.", "en": "Enter a clear delivery neighborhood name."},
    "error_OUTSIDE_COVERAGE":{"ar": "العنوان خارج نطاق توصيل هذا الفرع.", "en": "This address is outside the branch delivery coverage."},
    "error_CAR_DETAILS_REQUIRED":{"ar": "أدخل بيانات السيارة قبل تأكيد الطلب.", "en": "Enter your car details before confirming."},
    "error_ORDER_QUANTITY_LIMIT":{"ar": "الحد الأقصى مئة وحدة في الطلب.", "en": "An order can contain up to 100 units."},
    "error_PRODUCT_UNAVAILABLE":{"ar": "الصنف غير متاح في هذا الفرع.", "en": "This item is unavailable at this branch."},
    "error_CATALOG_SOURCE_UNVERIFIED":{"ar": "السعر وخيارات هذا الصنف لم تُعتمد من المصدر التشغيلي بعد.", "en": "This item’s price and options have not been approved."},
    "error_KHALIYA_TYPE_REQUIRED":{"ar": "اختر النوع: كلاسيك أو بيكان.", "en": "Choose a Khaliya type: Classic or Pecan."},
    "error_PRODUCT_VARIANT_UNAVAILABLE":{"ar": "نوع أو خيار الصنف المحدد غير متاح في هذا الفرع.", "en": "The selected type or option is unavailable at this branch."},
    "error_MINIMUM_ORDER_NOT_MET":{"ar": "السلة أقل من الحد الأدنى المعتمد للتوصيل.", "en": "Your basket is below the delivery minimum."},
    "delivery_minimum_remaining":{ar:"الحد الأدنى للمنتجات {minimum} قبل رسوم التوصيل. أضف {remaining} لإكمال طلبك.",en:"The item minimum is {minimum}, before delivery fees. Add {remaining} to continue."},
    "error_ORDER_VALUE_LIMIT":{"ar": "قيمة الطلب أعلى من الحد المسموح للموقع.", "en": "Your order exceeds the allowed amount."},
    "error_MAP_URL_NOT_ALLOWED":{"ar": "اختر دبوسًا أو رابط Google Maps معتمدًا.", "en": "Choose a supported Google Maps link or pin."},
    "error_MAP_PIN_REQUIRED":{"ar": "تعذر استخراج دبوس موثوق من الرابط. حدد الموقع مباشرة.", "en": "A verified pin cannot be extracted from this link. Set your location directly."},
    "error_COORDINATE_INVALID":{"ar": "الموقع غير صحيح.", "en": "The location coordinates are invalid."},
    "error_BRANCH_LOCATION_UNVERIFIED":{"ar": "دبوس الفرع غير معتمد.", "en": "The branch map pin has not been verified."},
    "error_DELIVERY_DUTY_UNAVAILABLE":{"ar": "لا توجد مناوبة توصيل مهيأة لاستقبال الطلبات.", "en": "No delivery shift is available to accept new orders."},
    "error_PRICE_CHANGED":{"ar": "تغير سعر الطلب. راجع الإجمالي الجديد وأكد موافقتك.", "en": "Your order price changed. Review the new total before confirming."},
    "error_CUSTOMER_PHONE_REQUIRED":{"ar": "أدخل رقم اتصال صحيحًا قبل الطلب.", "en": "Enter a valid contact number before ordering."},
    "error_SERVICE_INVALID":{"ar": "نوع الخدمة غير صحيح.", "en": "This service type is invalid."},
    "error_ORDER_VERSION_CONFLICT":{"ar": "تغير الطلب. حدّث الصفحة.", "en": "The order changed. Refresh the page."},
    "error_ARRIVAL_NOT_APPLICABLE":{"ar": "الوصول يخص طلب سيارة غير منتهٍ.", "en": "Arrival is only available for an active car pickup order."},
    "error_CONVERSATION_NOT_FOUND":{"ar": "المحادثة غير موجودة ضمن صلاحياتك.", "en": "This conversation is not available to your account."},
    "error_MESSAGE_TOO_LONG":{"ar": "الرسالة طويلة جدًا.", "en": "This message is too long."},
    "error_ATTACHMENT_INVALID":{"ar": "الصورة المرفقة غير صالحة.", "en": "This attachment is invalid."},
    "error_ATTACHMENT_TOO_LARGE":{"ar": "حجم الصورة أكبر من الحد المسموح.", "en": "This photo exceeds the size limit."},
    "error_OTP_RATE_LIMIT":{"ar": "محاولات كثيرة. انتظر قليلًا ثم حاول مجددًا.", "en": "Too many attempts. Wait a little and try again."},
    "error_EMAIL_RATE_LIMIT":{"ar": "محاولات كثيرة. انتظر قليلًا ثم حاول مجددًا.", "en": "Too many attempts. Wait a little and try again."},
    "error_LOGIN_RATE_LIMIT":{"ar": "محاولات كثيرة. انتظر قليلًا ثم حاول مجددًا.", "en": "Too many attempts. Wait a little and try again."},
    "error_GUEST_RATE_LIMIT":{"ar": "محاولات كثيرة. انتظر قليلًا ثم حاول مجددًا.", "en": "Too many attempts. Wait a little and try again."},
    "error_ORDER_RATE_LIMIT":{"ar": "محاولات كثيرة. انتظر قليلًا ثم حاول مجددًا.", "en": "Too many attempts. Wait a little and try again."},
    "error_CHAT_RATE_LIMIT":{"ar": "محاولات كثيرة. انتظر قليلًا ثم حاول مجددًا.", "en": "Too many attempts. Wait a little and try again."},
    "delivery_reopens":{"ar": "التوصيل مغلق الآن. موعد الفتح التالي {time} بتوقيت الرياض.", "en": "Delivery is closed. Next opening: {time} (Asia/Riyadh)."},
    "service_payment_summary":{"ar": "{service} · {payment}", "en": "{service} · {payment}"},
    "type":{"ar": "النوع", "en": "Type"},
    "flavor":{"ar": "النكهة", "en": "Flavor"},
    "unavailable_option":{"ar": " — غير متاح", "en": " — Unavailable"},

    "images_and_options_from_the_kps_menu": {ar:" · الصور والخيارات من منيو KPS", en:" · Images and options from the KPS menu"},
    "your_delivery_coverage_will_be_checked_when_reviewing_the_order": {ar:" سيُتحقق من تغطية موقعك عند مراجعة الطلب.", en:" Your delivery coverage will be checked when reviewing the order."},
    "via_sms": {ar:" عبر الرسائل النصية", en:" via SMS"},
    "via_whatsapp": {ar:" عبر واتساب", en:" via WhatsApp"},
    "in_the_selected_city": {ar:" في المدينة المحددة", en:" in the selected city"},
    "your_current_selection": {ar:" — اختيارك الحالي", en:" — your current selection"},
    "0_items": {ar:"0 أصناف", en:"0 items"},
    "separator": {ar:"، ", en:", "},
    "enter_valid_location_coordinates": {ar:"أدخل إحداثيات صحيحة للموقع.", en:"Enter valid location coordinates."},
    "enter_the_recipient_s_name_to_review_your_order": {ar:"أدخل اسم المستلم لمراجعة الطلب.", en:"Enter the recipient's name to review your order."},
    "enter_a_clear_delivery_neighborhood_name": {ar:"أدخل اسم حي التوصيل بصورة واضحة.", en:"Enter a clear delivery neighborhood name."},
    "enter_latitude_and_longitude_before_saving": {ar:"أدخل خط العرض وخط الطول قبل الاعتماد.", en:"Enter latitude and longitude before saving."},
    "enter_your_car_color_and_plate_number": {ar:"أدخل لون السيارة ورقم اللوحة.", en:"Enter your car color and plate number."},
    "enter_your_car_color": {ar:"أدخل لون السيارة.", en:"Enter your car color."},
    "variant_combination_unavailable": {ar:"هذا الحجم والنكهة غير متاحين معًا. غيّر أحد الاختيارات.", en:"This size and flavour are not available together. Change one of your choices."},
    "enter_your_delivery_address_description": {ar:"أدخل وصف عنوان التوصيل.", en:"Enter your delivery address description."},
    "add_items_to_your_basket": {ar:"أضف أصنافًا للسلة", en:"Add items to your basket"},
    "add_a_preparation_note": {ar:"أضف أي ملاحظة مهمة للتجهيز", en:"Add a preparation note"},
    "add_a_message_or_photo_before_sending": {ar:"أضف نصًا أو صورة قبل الإرسال.", en:"Add a message or photo before sending."},
    "menu_categories": {ar:"أقسام المنيو", en:"Menu categories"},
    "a_color_for_each_mug": {ar:"ألوان لكل كوب", en:"A color for each mug"},
    "estimated_item_total": {ar:"إجمالي المنتجات المبدئي", en:"Estimated item total"},
    "enter_location_manually": {ar:"لصق رابط خرائط جوجل", en:"Paste a Google Maps link"},
    "if_this_email_is_eligible_you_will_receive_a_secure_single_use_li": {ar:"إذا كان البريد مؤهلًا، سيصلك رابط آمن صالح لمرة واحدة. قبول الطلب لا يؤكد وصول البريد.", en:"If this email is eligible, you will receive a secure single-use link. Acceptance does not confirm email delivery."},
    "send": {ar:"إرسال", en:"Send"},
    "send_secure_sign_in_link": {ar:"إرسال رابط الدخول الآمن", en:"Send secure sign-in link"},
    "send_verification_code": {ar:"إرسال رمز التحقق", en:"Send verification code"},
    "attach_photo": {ar:"إرفاق صورة", en:"Attach photo"},
    "add_to_basket": {ar:"إضافة للسلة", en:"Add to basket"},
    "brand_announcements": {ar:"إعلانات البراند", en:"Brand announcements"},
    "close_photo": {ar:"إغلاق الصورة", en:"Close photo"},
    "close_orders": {ar:"إغلاق الطلبات", en:"Close orders"},
    "close_conversations": {ar:"إغلاق المحادثات", en:"Close conversations"},
    "close_conversation": {ar:"إغلاق المحادثة", en:"Close conversation"},
    "close_review": {ar:"إغلاق المراجعة", en:"Close review"},
    "close_sign_in": {ar:"إغلاق تسجيل الدخول", en:"Close sign in"},
    "close_product_details": {ar:"إغلاق تفاصيل الصنف", en:"Close product details"},
    "complete_staff_invitation": {ar:"إكمال دعوة الموظف", en:"Complete staff invitation"},
    "clear_selected_location": {ar:"إلغاء الموقع المحدد", en:"Clear selected location"},
    "to_your_location": {ar:"إلى موقعك", en:"To your location"},
    "pause_motion": {ar:"إيقاف الحركة", en:"Pause motion"},
    "search_for_an_item": {ar:"ابحث عن صنف…", en:"Search for an item…"},
    "start_with_your_favorite": {ar:"ابدأ باختيارك المفضل", en:"Start with your favorite"},
    "start_your_message_here_your_conversation_remains_saved_in_your_a": {ar:"ابدأ رسالتك هنا. تبقى المحادثة محفوظة في حسابك.", en:"Start your message here. Your conversation remains saved in your account."},
    "choose_the_quantity_then_a_color_for_each_mug": {ar:"اختر العدد ثم حدد اللون لكل كوب قبل الإضافة.", en:"Choose the quantity, then a color for each mug."},
    "choose_the_quantity_then_a_flavor_for_each_unit": {ar:"اختر العدد ثم حدد النكهة لكل وحدة قبل الإضافة.", en:"Choose the quantity, then a flavor for each unit."},
    "choose_quantity_and_options": {ar:"اختر العدد والخيارات", en:"Choose quantity and options"},
    "choose_a_branch": {ar:"اختر الفرع", en:"Choose a branch"},
    "choose_a_branch_each_service_is_available_independently": {ar:"اختر الفرع. اختر الفرع المتاح لطلبك.", en:"Choose a branch. Each service is available independently."},
    "choose_a_type": {ar:"اختر النوع", en:"Choose a type"},
    "choose_a_type_for_each_unit_classic_or_pecan": {ar:"اختر النوع لكل وحدة: كلاسيك أو بيكان.", en:"Choose a type for each unit: Classic or Pecan."},
    "choose_a_jpg_png_or_webp_image_up_to_5_mb": {ar:"اختر صورة JPG أو PNG أو WebP بحجم لا يتجاوز 5 ميجابايت.", en:"Choose a JPG, PNG or WebP image up to 5 MB."},
    "card_payment_is_collected_on_arrival_do_not_enter_card_details_he": {ar:"اختيار الشبكة للدفع عند الاستلام. لا تُدخل بيانات بطاقتك هنا.", en:"Card payment is collected on arrival. Do not enter card details here."},
    "choose_this_branch": {ar:"اختيار هذا الفرع", en:"Choose this branch"},
    "your_choices": {ar:"اختياراتك", en:"Your choices"},
    "your_current_branch_is_in_another_city_it_stays_selected_until_yo": {ar:"اختيارك الحالي في مدينة أخرى؛ لن يتغير حتى تختار فرعًا بنفسك.", en:"Your current branch is in another city. It stays selected until you choose a branch."},
    "optional": {ar:"اختياري", en:"Optional"},
    "required": {ar:"مطلوب", en:"Required"},
    "use_my_current_location": {ar:"استخدام موقعي الحالي", en:"Use my current location"},
    "car_pickup": {ar:"استلام بالسيارة", en:"Car pickup"},
    "branch_pickup": {ar:"استلام من الفرع", en:"Branch pickup"},
    "picked_up_by_driver": {ar:"استلم السائق", en:"Picked up by driver"},
    "neighborhood_name": {ar:"اسم الحي", en:"Neighborhood name"},
    "recipient_name": {ar:"اسم المستلم", en:"Recipient name"},
    "your_name": {ar:"اسمك", en:"Your name"},
    "name_for_collecting_your_order": {ar:"اسمك لاستلام الطلب", en:"Name for collecting your order"},
    "order_as_a_guest": {ar:"اطلب كضيف", en:"Order as a guest"},
    "order_directly_without_a_verification_code_or_creating_an_account": {ar:"اطلب مباشرة دون رمز تحقق أو إنشاء حساب.", en:"Order directly without a verification code or creating an account."},
    "save_entered_location": {ar:"اعتماد الموقع المدخل", en:"Save entered location"},
    "use_link_s_map_pin": {ar:"إعادة التحقق من الرابط", en:"Check link again"},
    "map_link_auto_hint": {ar:"الصق رابط موقعك؛ نحدده ونحفظه تلقائيًا.", en:"Paste your location link; we confirm and save it automatically."},
    "open_the_invitation_link_again": {ar:"افتح رابط الدعوة مرة أخرى.", en:"Open the invitation link again."},
    "write_your_message": {ar:"اكتب رسالتك…", en:"Write your message…"},
    "current_prices_and_options_are_awaiting_verification_orders_can_b": {ar:"الأسعار والخيارات الحالية قيد التحقق؛ لا يُتاح تأكيد طلب قبل اعتمادها.", en:"Current prices and options are awaiting verification. Orders can be confirmed after approval."},
    "items_you_add_will_appear_here": {ar:"الأصناف التي تضيفها ستظهر هنا.", en:"Items you add will appear here."},
    "assignment": {ar:"الإسناد", en:"Assignment"},
    "directions_to_branch": {ar:"الاتجاهات إلى الفرع", en:"Directions to branch"},
    "full_name": {ar:"الاسم الكامل", en:"Full name"},
    "search_menu": {ar:"البحث في المنيو", en:"Search menu"},
    "email_address": {ar:"البريد الإلكتروني", en:"Email address"},
    "email_verified_phone_verification_is_separate": {ar:"البريد موثق · توثيق الجوال مستقل", en:"Email verified · Phone verification is separate"},
    "preparation": {ar:"التجهيز", en:"Preparation"},
    "phone_verification_is_not_configured_guest_ordering_depends_on_th": {ar:"التحقق بالجوال غير مهيأ. مسار الطلب كضيف متاح حسب إعداد البراند.", en:"Phone verification is not configured. Guest ordering depends on the brand's settings."},
    "email_verification": {ar:"التحقق من البريد", en:"Email verification"},
    "verify_and_complete_sign_in": {ar:"التحقق وإكمال الدخول", en:"Verify and complete sign in"},
    "guest_and_customer_conversations_are_available_here_staff_access_": {ar:"التواصل هنا متاح لجلسة الضيف أو حساب العميل. دخول فريق التشغيل من الرابط أسفل الصفحة.", en:"Guest and customer conversations are available here. Staff access is at the bottom of the page."},
    "delivery": {ar:"التوصيل", en:"Delivery"},
    "your_basket_can_contain_up_to_100_units": {ar:"الحد الأقصى مئة وحدة في السلة.", en:"Your basket can contain up to 100 units."},
    "motion_paused": {ar:"الحركة متوقفة", en:"Motion paused"},
    "motion_paused_by_device_preference": {ar:"الحركة متوقفة حسب إعداد الجهاز", en:"Motion paused by device preference"},
    "neighborhood": {ar:"الحي", en:"Neighborhood"},
    "neighborhood_street_building_number_and_a_clear_landmark": {ar:"الحي، الشارع، رقم المبنى وأي علامة واضحة", en:"Neighborhood, street, building number and a clear landmark"},
    "service_or_location_is_not_verified": {ar:"الخدمة أو الموقع غير معتمد.", en:"Service or location is not verified."},
    "service_paused": {ar:"الخدمة متوقفة", en:"Service paused"},
    "discount": {ar:"الخصم", en:"Discount"},
    "your_session_belongs_to_another_brand_sign_out_before_signing_in_": {ar:"الدخول الحالي خاص ببراند آخر. سجل الخروج للدخول إلى هذا البراند.", en:"Your session belongs to another brand. Sign out before signing in to this brand."},
    "sign_in_with_email": {ar:"الدخول بالبريد الإلكتروني", en:"Sign in with email"},
    "payment": {ar:"الدفع", en:"Payment"},
    "pay_on_arrival": {ar:"الدفع عند الاستلام", en:"Pay on arrival"},
    "fees_and_the_final_amount_appear_after_branch_and_service_checks": {ar:"الرسوم والمبلغ النهائي يظهران بعد تحقق الفرع والخدمة.", en:"Fees and the final amount appear after branch and service checks."},
    "basket": {ar:"السلة", en:"Basket"},
    "this_item_is_unavailable_at_this_branch": {ar:"الصنف غير متاح في هذا الفرع.", en:"This item is unavailable at this branch."},
    "images_jpg_png_or_webp_up_to_5_mb": {ar:"الصور: JPG أو PNG أو WebP، بحد أقصى 5 ميجابايت.", en:"Images: JPG, PNG or WebP, up to 5 MB."},
    "photo_currently_unavailable": {ar:"الصورة غير متاحة حاليًا", en:"Photo currently unavailable"},
    "the_photo_is_currently_unavailable": {ar:"الصورة غير متاحة حاليًا.", en:"The photo is currently unavailable."},
    "orders": {ar:"الطلبات", en:"Orders"},
    "order_homepage": {ar:"الطلبات الرئيسية", en:"Order homepage"},
    "scrolling_announcements": {ar:"العبارات المتحركة", en:"Scrolling announcements"},
    "quantity": {ar:"العدد", en:"Quantity"},
    "back_to_my_orders": {ar:"العودة إلى طلباتي", en:"Back to my orders"},
    "branch": {ar:"الفرع", en:"Branch"},
    "selected_branch": {ar:"الفرع المختار", en:"Selected branch"},
    "relevant_branch": {ar:"الفرع المختص", en:"Relevant branch"},
    "nearest_eligible_branches": {ar:"الفروع المؤهلة الأقرب إليك", en:"Nearest eligible branches"},
    "mug": {ar:"الكوب", en:"Mug"},
    "color": {ar:"اللون", en:"Color"},
    "amount_due": {ar:"المبلغ المستحق", en:"Amount due"},
    "city": {ar:"المدينة", en:"City"},
    "items": {ar:"المنتجات", en:"Items"},
    "menu": {ar:"المنيو", en:"Menu"},
    "location_is_optional_for_nearby_suggestions_you_can_choose_a_city": {ar:"حدد موقع التوصيل من الخريطة أو موقعك الحالي، أو الصق رابط خرائط جوجل. للاستلام، اختر الفرع.", en:"Choose your delivery location on the map, use your current location, or paste a Google Maps link. For pickup, choose a branch."},
    "selected_delivery_location": {ar:"الموقع المحدد للتوصيل", en:"Selected delivery location"},
    "unit": {ar:"الوحدة", en:"Unit"},
    "wait_for_the_conversation_to_open_before_sending": {ar:"انتظر فتح المحادثة قبل الإرسال.", en:"Wait for the conversation to open before sending."},
    "skip_to_menu": {ar:"انتقل إلى المنيو", en:"Skip to menu"},
    "your_session_expired_start_a_new_guest_session_or_sign_in_with_yo": {ar:"انتهت الجلسة. افتح جلسة ضيف جديدة أو ادخل ببريدك الموثق.", en:"Your session expired. Start a new guest session or sign in with your verified email."},
    "awaiting_assignment": {ar:"بانتظار الإسناد", en:"Awaiting assignment"},
    "awaiting_collection": {ar:"بانتظار التحصيل", en:"Awaiting collection"},
    "awaiting_processing": {ar:"بانتظار المعالجة", en:"Awaiting processing"},
    "awaiting_task_acceptance": {ar:"بانتظار قبول المهمة", en:"Awaiting task acceptance"},
    "start_a_conversation": {ar:"بدء محادثة", en:"Start a conversation"},
    "some_items_differ_from_the_approved_source_or_need_approval_appro": {ar:"بعض الأصناف لا تطابق النسخة المعتمدة أو تحتاج اعتماد تعديلاتها. تبقى الأصناف المعتمدة متاحة حسب الفرع والخدمة.", en:"Some items differ from the approved source or need approval. Approved items remain available by branch and service."},
    "confirm_order": {ar:"تأكيد الطلب", en:"Confirm order"},
    "this_browser_does_not_support_location_you_can_enter_it_manually": {ar:"تحديد الموقع غير مدعوم في هذا المتصفح. يمكنك إدخاله يدويًا.", en:"This browser does not support location. You can enter it manually."},
    "verify_your_phone_through_an_available_channel": {ar:"تحقق من رقم جوالك عبر القناة المتاحة.", en:"Verify your phone through an available channel."},
    "verify_and_sign_in": {ar:"تحقق ودخول", en:"Verify and sign in"},
    "load_older_orders": {ar:"تحميل طلبات أقدم", en:"Load older orders"},
    "load_older_conversations": {ar:"تحميل محادثات أقدم", en:"Load older conversations"},
    "sign_out": {ar:"تسجيل الخروج", en:"Sign out"},
    "sign_in": {ar:"تسجيل الدخول", en:"Sign in"},
    "resume_motion": {ar:"تشغيل الحركة", en:"Resume motion"},
    "browse_the_menu_then_choose_your_service_and_branch": {ar:"تصفح المنيو، ثم حدد الخدمة والفرع المناسب لطلبك.", en:"Browse the menu, then choose your service and branch."},
    "use_the_first_mug_s_color_for_all": {ar:"تطبيق لون الكوب الأول على الكل", en:"Use the first mug's color for all"},
    "use_the_first_unit_s_flavor_for_all": {ar:"تطبيق نكهة الوحدة الأولى على الكل", en:"Use the first unit's flavor for all"},
    "use_the_first_unit_s_type_for_all": {ar:"تطبيق نوع الوحدة الأولى على الكل", en:"Use the first unit's type for all"},
    "unable_to_complete_this_action_check_your_details_and_try_again": {ar:"تعذر إتمام العملية. راجع البيانات وحاول مرة أخرى.", en:"Unable to complete this action. Check your details and try again."},
    "unable_to_connect_check_your_internet_and_try_again_this_action_h": {ar:"تعذر الاتصال بالموقع. تحقق من الإنترنت ثم حاول مجددًا؛ لم نؤكد حفظ هذه العملية.", en:"Unable to connect. Check your internet and try again; this action has not been confirmed."},
    "unable_to_verify_the_final_amount_try_again": {ar:"تعذر التحقق من المبلغ النهائي. حاول مرة أخرى.", en:"Unable to verify the final amount. Try again."},
    "unable_to_update_your_location_your_previous_location_is_kept_you": {ar:"تعذر تحديث الموقع؛ احتفظنا بالموقع المحدد سابقًا. يمكنك اختيار الفرع يدويًا.", en:"Unable to update your location. Your previous location is kept; you can choose a branch manually."},
    "unable_to_identify_the_brand_from_this_link": {ar:"تعذر تحديد البراند من هذا الرابط.", en:"Unable to identify the brand from this link."},
    "unable_to_load_saved_conversations": {ar:"تعذر تحميل المحادثات المحفوظة.", en:"Unable to load saved conversations."},
    "unable_to_load_part_of_the_page_refresh_to_try_again": {ar:"تعذر تحميل جزء من الصفحة. حدّث الصفحة للمحاولة مجددًا.", en:"Unable to load part of the page. Refresh to try again."},
    "unable_to_load_this_brand_s_menu_refresh_to_try_again": {ar:"تعذر تحميل منيو هذا البراند. حدّث الصفحة للمحاولة مجددًا.", en:"Unable to load this brand's menu. Refresh to try again."},
    "unable_to_open_the_order_conversation": {ar:"تعذر فتح محادثة الطلب.", en:"Unable to open the order conversation."},
    "unable_to_read_the_photo": {ar:"تعذرت قراءة الصورة.", en:"Unable to read the photo."},
    "changing_branches_will_make_these_items_unavailable_we_will_not_r": {ar:"تغيير الفرع سيجعل هذه الأصناف غير متاحة. لن نحذفها دون موافقتك:", en:"Changing branches will make these items unavailable. We will not remove them without your approval:"},
    "change_phone_number": {ar:"تغيير رقم الجوال", en:"Change phone number"},
    "decrease_quantity": {ar:"تقليل العدد", en:"Decrease quantity"},
    "enlarge_photo": {ar:"تكبير الصورة", en:"Enlarge photo"},
    "selected_location_cleared_you_can_choose_a_city_and_branch_manual": {ar:"تم إلغاء الموقع المحدد. يمكنك اختيار المدينة والفرع يدويًا.", en:"Selected location cleared. You can choose a city and branch manually."},
    "location_saved_for_eligible_branch_suggestions_you_choose_the_bra": {ar:"تم اعتماد موقعك لعرض الفروع المؤهلة؛ يبقى اختيار الفرع بيدك.", en:"Location saved for eligible branch suggestions. You choose the branch."},
    "collected": {ar:"تم التحصيل", en:"Collected"},
    "email_verified": {ar:"تم التحقق من البريد.", en:"Email verified."},
    "phone_verified": {ar:"تم التحقق من جوالك", en:"Phone verified"},
    "delivered": {ar:"تم التسليم", en:"Delivered"},
    "signed_out": {ar:"تم تسجيل الخروج.", en:"Signed out."},
    "signed_in": {ar:"تم تسجيل الدخول", en:"Signed in"},
    "your_arrival_notification_has_been_saved": {ar:"تم حفظ إشعار وصولك للفرع.", en:"Your arrival notification has been saved."},
    "your_order_is_saved_you_can_follow_its_status_and_conversation": {ar:"تم حفظ طلبك. يمكنك متابعة حالته ومحادثته.", en:"Your order is saved. You can follow its status and conversation."},
    "saved_khaliya_items_are_linked_to_their_original_types_review_the": {ar:"تم ربط أصناف الخلية المحفوظة بأنواعها الأصلية. راجع النوع والكمية في السلة.", en:"Saved Khaliya items are linked to their original types. Review the type and quantity in your basket."},
    "a_secure_guest_session_has_been_created": {ar:"تم فتح جلسة ضيف آمنة.", en:"A secure guest session has been created."},
    "resolved": {ar:"تمت المعالجة", en:"Resolved"},
    "contact_us": {ar:"تواصل معنا", en:"Contact us"},
    "delivery_2": {ar:"توصيل", en:"Delivery"},
    "checking_availability_and_amount": {ar:"جاري التحقق من الإتاحة والمبلغ…", en:"Checking availability and amount…"},
    "checking_branches_location_service_and_basket_items": {ar:"جاري التحقق من الفروع والموقع والخدمة وأصناف السلة…", en:"Checking branches, location, service and basket items…"},
    "checking": {ar:"جاري التحقق…", en:"Checking…"},
    "locating_you_with_your_permission": {ar:"جاري تحديد موقعك بإذنك…", en:"Locating you with your permission…"},
    "loading_items": {ar:"جاري تحميل الأصناف…", en:"Loading items…"},
    "loading_brand": {ar:"جاري تحميل البراند…", en:"Loading brand…"},
    "loading_menu": {ar:"جاري تحميل المنيو…", en:"Loading menu…"},
    "loading_your_orders": {ar:"جاري تحميل طلباتك…", en:"Loading your orders…"},
    "loading_your_conversations": {ar:"جاري تحميل محادثاتك…", en:"Loading your conversations…"},
    "ready": {ar:"جاهز", en:"Ready"},
    "new": {ar:"جديد", en:"New"},
    "try_another_search_or_category": {ar:"جرّب كلمة أخرى أو قسمًا مختلفًا.", en:"Try another search or category."},
    "new_orders_are_currently_paused_for_all_services": {ar:"جميع خدمات الطلبات الجديدة متوقفة حاليًا.", en:"New orders are currently paused for all services."},
    "new_orders_are_paused_at_this_branch_choose_another_branch": {ar:"جميع خدمات الطلبات الجديدة موقوفة لهذا الفرع. اختر فرعًا آخر.", en:"New orders are paused at this branch. Choose another branch."},
    "ready_before_you_arrive": {ar:"جهز طلبك قبل الوصول", en:"Ready before you arrive"},
    "verification_depends_on_the_channel": {ar:"حالة التحقق حسب القناة", en:"Verification depends on the channel"},
    "choose_a_branch_to_check_availability": {ar:"حدد الفرع للتحقق من الإتاحة", en:"Choose a branch to check availability"},
    "choose_a_branch_to_check_your_order": {ar:"حدد الفرع للتحقق من طلبك.", en:"Choose a branch to check your order."},
    "choose_a_branch_and_service_to_start_the_conversation": {ar:"حدد الفرع ونوع الخدمة لبدء المحادثة.", en:"Choose a branch and service to start the conversation."},
    "choose_a_color_for_mug": {ar:"حدد اللون للكوب", en:"Choose a color for mug"},
    "choose_a_flavor_for_unit": {ar:"حدد النكهة للوحدة", en:"Choose a flavor for unit"},
    "choose_a_type_for_unit": {ar:"حدد النوع للوحدة", en:"Choose a type for unit"},
    "choose_a_branch_to_check_availability_in_hafar_al_batin": {ar:"حدد فرع التنفيذ للتحقق من الإتاحة في حفر الباطن.", en:"Choose a branch to check availability in Hafar Al Batin."},
    "set_your_delivery_location_to_check_coverage_and_fees": {ar:"حدد موقع التوصيل للتحقق من التغطية والرسوم.", en:"Set your delivery location to check coverage and fees."},
    "remove": {ar:"حذف", en:"Remove"},
    "remove_unavailable_items_and_change_branch": {ar:"حذف الأصناف غير المتاحة وتغيير الفرع", en:"Remove unavailable items and change branch"},
    "staff_account": {ar:"حساب التشغيل", en:"Staff account"},
    "my_account": {ar:"حسابي", en:"My account"},
    "customer_navigation": {ar:"التنقل الرئيسي", en:"Main navigation"},
    "account_orders_hint": {ar:"متابعة طلباتك السابقة والحالية", en:"Follow current and past orders"},
    "account_help_hint": {ar:"المساعدة والشكاوى ومتابعة طلبات التواصل", en:"Help, complaints and support updates"},
    "personal_details": {ar:"بياناتي", en:"My details"},
    "your_services": {ar:"خدماتك", en:"Your services"},
    "policies_and_data": {ar:"السياسات وبياناتك", en:"Policies and your data"},
    "data_request": {ar:"طلب متعلق ببياناتي", en:"Request about my data"},
    "no_saved_addresses": {ar:"لا توجد عناوين محفوظة. يُحفظ عنوان التوصيل بعد إتمام طلبك.", en:"No saved addresses yet. Your delivery address is saved after an order."},
    "choose_or_change_branch": {ar:"اختيار أو تغيير الفرع", en:"Choose or change your branch"},
    "data_saved": {ar:"تم حفظ بياناتك.", en:"Your details are saved."},
    "longitude": {ar:"خط الطول", en:"Longitude"},
    "latitude": {ar:"خط العرض", en:"Latitude"},
    "options_for_each_unit": {ar:"خيارات لكل وحدة", en:"Options for each unit"},
    "staff_sign_in": {ar:"دخول فريق التشغيل", en:"Staff sign in"},
    "google_maps_link": {ar:"رابط Google Maps", en:"Google Maps link"},
    "invalid_brand_link_use_a_link_provided_by_the_brand": {ar:"رابط البراند غير صالح. استخدم رابطًا صادرًا من إدارة البراند.", en:"Invalid brand link. Use a link provided by the brand."},
    "review_unapproved_items_in_your_basket": {ar:"راجع الأصناف غير المعتمدة في السلة", en:"Review unapproved items in your basket"},
    "review_items_whose_prices_or_options_have_changed_before_confirmi": {ar:"راجع الصنف الذي تغير سعره أو خياراته في السلة قبل تأكيد الطلب.", en:"Review items whose prices or options have changed before confirming."},
    "review_the_saved_khaliya_type_and_choose_it_from_the_unified_card": {ar:"راجع نوع الخلية المحفوظ وأعد اختياره من البطاقة الموحدة.", en:"Review the saved Khaliya type and choose it from the unified card."},
    "link_to_one_of_my_orders": {ar:"ربط بأحد طلباتي", en:"Link to one of my orders"},
    "link_a_verified_email_to_this_guest_session": {ar:"ربط بريد موثق بجلسة الضيف", en:"Link a verified email to this guest session"},
    "your_message": {ar:"رسالتك", en:"Your message"},
    "delivery_fee": {ar:"رسوم التوصيل", en:"Delivery fee"},
    "contact_number": {ar:"رقم الاتصال", en:"Contact number"},
    "phone_number": {ar:"رقم الجوال", en:"Phone number"},
    "plate_number": {ar:"رقم اللوحة", en:"Plate number"},
    "your_contact_number_is_unverified_and_is_used_for_order_contact_o": {ar:"رقمك غير موثق، ويُستخدم للتواصل بشأن طلبك. الطلبات والمحادثات مرتبطة بجلستك الآمنة.", en:"Your contact number is unverified and is used for order contact. Orders and conversations belong to your secure session."},
    "verification_code": {ar:"رمز التحقق", en:"Verification code"},
    "increase_quantity": {ar:"زيادة العدد", en:"Increase quantity"},
    "items_approved_by_the_brand_will_appear_here": {ar:"ستظهر هنا الأصناف التي تعتمدها إدارة البراند.", en:"Items approved by the brand will appear here."},
    "order_status_history": {ar:"سجل حالة الطلب", en:"Order status history"},
    "your_basket": {ar:"سلتك", en:"Your basket"},
    "one_order_will_be_saved_after_server_confirmation_payment_is_coll": {ar:"سيُحفظ طلب واحد بعد تأكيد الخادم. التحصيل يتم عند الاستلام.", en:"One order will be saved after server confirmation. Payment is collected on arrival."},
    "handed_to_driver": {ar:"سُلّم للسائق", en:"Handed to driver"},
    "view_details": {ar:"شاهد التفاصيل", en:"View details"},
    "card": {ar:"شبكة", en:"Card"},
    "cash_and_card": {ar:"كاش وشبكة", en:"Cash and card"},
    "card_on_arrival": {ar:"شبكة عند الاستلام", en:"Card on arrival"},
    "item": {ar:"صنف", en:"Item"},
    "saved_item": {ar:"صنف محفوظ", en:"Saved item"},
    "product_photo": {ar:"صورة الصنف", en:"Product photo"},
    "photo_attached_to_conversation": {ar:"صورة مرفقة في المحادثة", en:"Photo attached to conversation"},
    "guest_unverified_contact_number": {ar:"ضيف — رقم الاتصال غير موثق", en:"Guest · Unverified contact number"},
    "customer_orders_are_available_to_customer_accounts_staff_access_i": {ar:"طلبات العملاء متاحة لحساب العميل. لوحة فريق التشغيل من رابط أسفل الصفحة.", en:"Customer orders are available to customer accounts. Staff access is at the bottom of the page."},
    "your_orders_and_conversations_in_one_place": {ar:"طلباتك ومحادثاتك في مكان واحد", en:"Your orders and conversations in one place"},
    "my_orders": {ar:"طلباتي", en:"My orders"},
    "order_and_follow_up": {ar:"طلبك ومتابعته", en:"Order and follow up"},
    "your_order_your_way": {ar:"طلبك، على طريقتك", en:"Your order, your way"},
    "connection_restored_checking_saved_orders": {ar:"عاد الاتصال. نتحقق من حالة الطلبات المحفوظة.", en:"Connection restored. Checking saved orders."},
    "item_quantity": {ar:"عدد الصنف", en:"Item quantity"},
    "view_order_and_conversation": {ar:"عرض الطلب ومحادثته", en:"View order and conversation"},
    "if_a_guest_session_is_lost_recovery_needs_a_verified_email_or_hel": {ar:"عند ضياع جلسة الضيف، يلزم بريد موثق أو مساعدة الفريق لاسترجاع آمن. رقم الاتصال وحده لا يفتح الطلبات.", en:"If a guest session is lost, recovery needs a verified email or help from the team. A contact number alone does not open orders."},
    "unassigned": {ar:"غير مسند", en:"Unassigned"},
    "unverified": {ar:"غير موثق", en:"Unverified"},
    "open_conversation": {ar:"فتح المحادثة", en:"Open conversation"},
    "fulfillment_branch": {ar:"فرع التنفيذ", en:"Fulfillment branch"},
    "branches_ineligible_for_this_selection": {ar:"فروع غير مؤهلة للاختيار الحالي", en:"Branches ineligible for this selection"},
    "relevant_branch_team": {ar:"فريق الفرع المختص", en:"Relevant branch team"},
    "on_the_way": {ar:"في الطريق", en:"On the way"},
    "at_participating_branches": {ar:"في الفروع المتاحة", en:"At participating branches"},
    "before_you_choose": {ar:"قبل أن تختار", en:"Before you choose"},
    "preparing": {ar:"قيد التجهيز", en:"Preparing"},
    "processing": {ar:"قيد المعالجة", en:"Processing"},
    "cash": {ar:"كاش", en:"Cash"},
    "a_choice_for_every_mood": {ar:"كل اختيار له مزاج", en:"A choice for every mood"},
    "all_cities": {ar:"كل المدن", en:"All cities"},
    "full_menu": {ar:"كل المنيو", en:"Full menu"},
    "how_would_you_like_to_receive_your_order": {ar:"كيف تستلم طلبك؟", en:"How would you like to receive your order?"},
    "no_matching_items": {ar:"لا توجد أصناف مطابقة", en:"No matching items"},
    "no_product_photo": {ar:"لا توجد صورة للصنف", en:"No product photo"},
    "no_orders_saved_in_your_account_yet": {ar:"لا توجد طلبات محفوظة في حسابك حتى الآن.", en:"No orders saved in your account yet."},
    "no_conversations_saved_in_your_account_yet": {ar:"لا توجد محادثات محفوظة في حسابك حتى الآن.", en:"No conversations saved in your account yet."},
    "not_applicable": {ar:"لا ينطبق", en:"Not applicable"},
    "no_items_have_been_added_for_this_brand_yet": {ar:"لم تُضف أصناف لهذا البراند بعد.", en:"No items have been added for this brand yet."},
    "a_secure_session_was_not_created_try_again": {ar:"لم تُنشأ جلسة دخول آمنة. حاول مجددًا.", en:"A secure session was not created. Try again."},
    "the_server_did_not_confirm_a_secure_session": {ar:"لم يؤكد الخادم إنشاء جلسة آمنة.", en:"The server did not confirm a secure session."},
    "the_server_did_not_confirm_the_photo_was_saved": {ar:"لم يؤكد الخادم حفظ الصورة.", en:"The server did not confirm the photo was saved."},
    "the_server_did_not_confirm_your_order_check_my_orders_before_retr": {ar:"لم يؤكد الخادم حفظ الطلب. راجع طلباتي قبل إعادة المحاولة.", en:"The server did not confirm your order. Check My orders before retrying."},
    "the_server_did_not_confirm_opening_the_conversation": {ar:"لم يؤكد الخادم فتح المحادثة.", en:"The server did not confirm opening the conversation."},
    "the_verification_provider_did_not_confirm_sending_the_code": {ar:"لم يؤكد مزود التحقق إرسال الرمز.", en:"The verification provider did not confirm sending the code."},
    "uncollected": {ar:"لم يُحصّل", en:"Uncollected"},
    "location_permission_was_denied_or_your_location_is_unavailable_ch": {ar:"لم يُسمح بتحديد موقعك أو تعذر الوصول إليه. اختر المدينة والفرع يدويًا، أو أدخل دبوس الموقع بنفسك.", en:"Location permission was denied or your location is unavailable. Choose a city and branch manually or enter your map pin."},
    "car_color": {ar:"لون السيارة", en:"Car color"},
    "saved_order_amount": {ar:"مبلغ الطلب المحفوظ", en:"Saved order amount"},
    "available_only_in_hafar_al_batin": {ar:"متوفر في حفر الباطن فقط", en:"Available only in Hafar Al Batin"},
    "available_only_in_hafar_al_batin_choose_an_eligible_branch": {ar:"متوفر في حفر الباطن فقط؛ اختر فرعًا مؤهلًا.", en:"Available only in Hafar Al Batin. Choose an eligible branch."},
    "your_conversations_are_saved_in_your_account_choose_a_service_and": {ar:"محادثاتك محفوظة في حسابك. حدد الخدمة والفرع للتواصل مع الفريق المختص، ويمكنك ربط المحادثة بأحد طلباتك.", en:"Your conversations are saved in your account. Choose a service and branch to contact the right team; you can link an order."},
    "my_conversations": {ar:"محادثاتي", en:"My conversations"},
    "order_conversation": {ar:"محادثة الطلب", en:"Order conversation"},
    "conversation_without_an_order": {ar:"محادثة دون طلب", en:"Conversation without an order"},
    "too_many_attempts_wait_a_little_and_try_again": {ar:"محاولات كثيرة. انتظر قليلًا ثم حاول مجددًا.", en:"Too many attempts. Wait a little and try again."},
    "paid": {ar:"مدفوع", en:"Paid"},
    "partially_paid": {ar:"مدفوع جزئيًا", en:"Partially paid"},
    "delivery_manager_and_team": {ar:"مدير وفريق التوصيل", en:"Delivery manager and team"},
    "review_order": {ar:"مراجعة الطلب", en:"Review order"},
    "review_branch_change": {ar:"مراجعة تغيير الفرع", en:"Review branch change"},
    "review_and_confirm_order": {ar:"مراجعة وتأكيد الطلب", en:"Review and confirm order"},
    "rejected": {ar:"مرفوض", en:"Rejected"},
    "approximate_geographic_distance_not_road_distance": {ar:"مسافة جغرافية تقريبية؛ ليست مسافة الطريق.", en:"Approximate geographic distance, not road distance."},
    "refunded": {ar:"مسترد", en:"Refunded"},
    "partially_refunded": {ar:"مسترد جزئيًا", en:"Partially refunded"},
    "received": {ar:"مستلم", en:"Received"},
    "assigned": {ar:"مسند", en:"Assigned"},
    "required_for_delivery": {ar:"مطلوب للتوصيل", en:"Required for delivery"},
    "open": {ar:"مفتوحة", en:"Open"},
    "accepted": {ar:"مقبول", en:"Accepted"},
    "completed": {ar:"مكتمل", en:"Completed"},
    "item_notes": {ar:"ملاحظات على الصنف", en:"Item notes"},
    "order_notes": {ar:"ملاحظات على الطلب", en:"Order notes"},
    "cancelled": {ar:"ملغي", en:"Cancelled"},
    "menu_delivery_branch_pickup_and_car_pickup_orders": {ar:"منيو وطلبات التوصيل والاستلام من الفرع والسيارة", en:"Menu, delivery, branch pickup and car pickup orders"},
    "service_type": {ar:"نوع الخدمة", en:"Service type"},
    "car_model": {ar:"نوع السيارة", en:"Car model"},
    "car_make_and_model": {ar:"نوع وموديل السيارة", en:"Car make and model"},
    "this_item_is_temporarily_unavailable": {ar:"هذا الصنف غير متاح مؤقتًا.", en:"This item is temporarily unavailable."},
    "this_service_has_not_been_enabled_at_this_branch": {ar:"هذه الخدمة لم تُفعّل للفرع بعد.", en:"This service has not been enabled at this branch."},
    "the_brand_does_not_match_this_link": {ar:"هوية البراند لا تطابق الرابط.", en:"The brand does not match this link."},
    "address_description": {ar:"وصف العنوان", en:"Address description"},
    "i_have_arrived_at_the_branch": {ar:"وصلت إلى الفرع", en:"I have arrived at the branch"},
    "sign_in_again_for_secure_verification": {ar:"يلزم تجديد الدخول للتحقق الآمن.", en:"Sign in again for secure verification."},
    "a_customer_or_guest_session_is_required_to_confirm_an_order_staff": {ar:"يلزم دخول عميل أو جلسة ضيف لتأكيد الطلب. حساب فريق التشغيل مخصص للتشغيل.", en:"A customer or guest session is required to confirm an order. Staff accounts are for operations."},
    "close_this_window_to_keep_your_current_branch_and_basket": {ar:"يمكنك إغلاق هذه النافذة للاحتفاظ بالفرع والسلة الحاليين.", en:"Close this window to keep your current branch and basket."},
    "choose_a_city_and_branch_without_location_access_ineligible_branc": {ar:"حدد موقعك لنختار الفرع الأقرب المتاح، أو اختر المدينة والفرع بنفسك.", en:"Choose a city and branch without location access. Ineligible branches can be viewed but cannot accept an order."},
    "choose_a_city_and_branch_without_location_access_service_status_a": {ar:"يمكنك اختيار المدينة والفرع دون تفعيل الموقع. تظهر حالة الخدمة بجانب الفرع.", en:"Choose a city and branch without location access. Service status appears beside each branch."},
    "you_can_ask_about_a_service_even_when_new_orders_are_paused_at_th": {ar:"يمكنك التواصل بخصوص الخدمة حتى إن كان استقبال الطلبات متوقفًا حاليًا في الفرع.", en:"You can ask about a service even when new orders are paused at the branch."},
    "order_as_a_guest_without_an_email_or_code_your_orders_are_kept_in": {ar:"يمكنك الطلب كضيف دون بريد أو رمز. تُحفظ طلباتك في جلسة آمنة على هذا الجهاز.", en:"Order as a guest without an email or code. Your orders are kept in a secure session on this device."},
    "you_can_browse_the_menu_your_sign_in_session_could_not_be_checked": {ar:"يمكنك تصفح المنيو. تعذر التحقق من جلسة الدخول حاليًا.", en:"You can browse the menu. Your sign-in session could not be checked."},
    "0_sar": {ar:"{0} ر.س", en:"{0} SAR"},
    "unavailable_0": {ar:" — غير متاح: {0}", en:" — Unavailable: {0}"},
    "0_you_can_choose_a_city_and_branch_manually": {ar:"{0} يمكنك اختيار المدينة والفرع يدويًا.", en:"{0} You can choose a city and branch manually."},
    "0_ranked_for_1_and_basket_items": {ar:"{0} الترتيب حسب {1} وأصناف السلة.", en:"{0} Ranked for {1} and basket items."},
    "no_verified_eligible_branches_for_this_selection_0_review_your_ci": {ar:"لا توجد فروع متاحة لهذا الاختيار{0}. راجع الموقع والخدمة المختارة.", en:"No branches are available for this selection{0}. Check your location and selected service."},
    "nearest_basket_unavailable": {ar:"يوجد فرع يخدم موقعك، لكن بعض أصناف أو خيارات سلتك غير متاحة فيه: {items}.", en:"A branch serves your location, but some basket items or options are unavailable there: {items}."},
    "review_basket": {ar:"مراجعة السلة", en:"Review basket"},
    "basket_option_unavailable": {ar:"الخيار المحفوظ غير متاح حاليًا. احذفه من السلة واختر بديلًا.", en:"This saved option is unavailable. Remove it from your basket and choose another option."},
    "nearest_saved_options_unavailable": {ar:"بعض خيارات سلتك لم تعد متاحة: {items}. راجع السلة لعرض الفروع المناسبة.", en:"Some saved basket options are no longer available: {items}. Review your basket to see available branches."},
    "0_km": {ar:"{0} كم", en:"{0} km"},
    "0_m": {ar:"{0} م", en:"{0} m"},
    "0_approximate_geographic_distance": {ar:"{0} · مسافة جغرافية تقريبية", en:"{0} · Approximate geographic distance"},
    "branches_ineligible_for_this_selection_0": {ar:"فروع غير مؤهلة للاختيار الحالي ({0})", en:"Branches ineligible for this selection ({0})"},
    "service_is_available_under_the_current_branch_settings_0": {ar:"الخدمة متاحة حسب نطاق الفرع وإعداداته الحالية.{0}", en:"Service is available under the current branch settings.{0}"},
    "0_items_1": {ar:"{0} صنفًا{1}", en:"{0} items{1}"},
    "details_of_0": {ar:"تفاصيل {0}", en:"Details of {0}"},
    "add_0_to_basket": {ar:"إضافة {0} إلى السلة", en:"Add {0} to basket"},
    "enlarge_photo_of_0": {ar:"تكبير صورة {0}", en:"Enlarge photo of {0}"},
    "0_items_2": {ar:"{0} أصناف", en:"{0} items"},
    "your_basket_0_items": {ar:"سلتك · {0} أصناف", en:"Your basket · {0} items"},
    "previously_saved_item_0_type_and_quantity_retained": {ar:"صنف محفوظ سابقًا: {0} · النوع والكمية محفوظان", en:"Previously saved item: {0} · Type and quantity retained"},
    "note_0": {ar:"ملاحظة: {0}", en:"Note: {0}"},
    "decrease_quantity_of_0": {ar:"تقليل كمية {0}", en:"Decrease quantity of {0}"},
    "increase_quantity_of_0": {ar:"زيادة كمية {0}", en:"Increase quantity of {0}"},
    "remove_0_1": {ar:"حذف {0}{1}", en:"Remove {0}{1}"},
    "choose_0": {ar:"اختر {0}", en:"Choose {0}"},
    "0_1_before_adding": {ar:"{0} {1} قبل الإضافة.", en:"{0} {1} before adding."},
    "added_0_to_basket": {ar:"أضيف {0} إلى السلة", en:"Added {0} to basket"},
    "a_verification_code_was_sent_to_the_number_ending_in_0_1": {ar:"أُرسل رمز التحقق إلى الرقم المنتهي بـ{0}{1}.", en:"A verification code was sent to the number ending in {0}{1}."},
    "order_0": {ar:"طلب {0}", en:"Order {0}"},
    "order_notes_0": {ar:"ملاحظات الطلب: {0}", en:"Order notes: {0}"},
    "order_0_1": {ar:"طلب {0} · {1}", en:"Order {0} · {1}"},
    "order_conversation_0": {ar:"محادثة طلب {0}", en:"Order conversation {0}"},
    "0_new_messages": {ar:"{0} رسائل جديدة", en:"{0} new messages"},
    "order_directly_from_0": {ar:"اطلب مباشرة من {0}", en:"Order directly from {0}"},
    "announcement_for_0": {ar:"إعلان {0}", en:"Announcement for {0}"},
    "announcement_0_of_1": {ar:"الإعلان {0} من {1}", en:"Announcement {0} of {1}"},
    "0_orders": {ar:"{0} | الطلبات", en:"{0} | Orders"},
    "0_menu_delivery_branch_pickup_and_car_pickup_orders": {ar:"منيو {0} وطلبات التوصيل والاستلام من الفرع والسيارة", en:"{0} menu, delivery, branch pickup and car pickup orders"},
    "0_home": {ar:"{0} الرئيسية", en:"{0} home"},
    "0_announcements": {ar:"إعلانات {0}", en:"{0} announcements"},
  };
  const localizedBindings=new Map();
  class LocalizedText {
    constructor(code,params={}){this.code=code;this.params=params;}
    toString(){
      const entry=uiStrings[this.code];
      const template=entry?.[language]||entry?.ar||'';
      return template.replace(/\{(\w+)\}/g,(_,name)=>{
        let value=this.params[name];
        if(typeof value==='function')value=value();
        return String(value??'');
      });
    }
  }
  const t=(code,params)=>new LocalizedText(code,params);
  let bindingCleanupPending=false;
  function setText(element,content){
    if(content instanceof LocalizedText)localizedBindings.set(element,content);
    else localizedBindings.delete(element);
    element.textContent=String(content??'');
    if(!bindingCleanupPending){bindingCleanupPending=true;queueMicrotask(()=>{
      bindingCleanupPending=false;
      for(const element of localizedBindings.keys())if(!element.isConnected)localizedBindings.delete(element);
    });}
  }
  // Display translations only: never rewrite catalog approval data, IDs or prices.
  // Exact source labels prevent a future renamed item from inheriting a stale name.
  const menuEnglish=new Map([
    ['كولد برو','Cold Brew'],['قهوة اليوم بارد','Iced Coffee of the Day'],
    ['قهوة اليوم حار','Hot Coffee of the Day'],['كوكيز شوكليت','Chocolate Cookies'],
    ['V60 حار','Hot V60'],['V60 بارد','Iced V60'],['ماء نوفا 330 مل','Nova Water 330 ml'],
    ['آيس تي خوخ','Peach Iced Tea'],['آيس كركديه','Iced Hibiscus'],['موهيتو','Mojito'],
    ['آيس شوكليت','Iced Chocolate'],['سموذي شمام','Melon Smoothie'],['ماتشا','Matcha'],
    ['تيراميسو','Tiramisu'],['سينابون','Cinnamon Roll'],['ترايفل مانجو','Mango Trifle'],
    ['كروسان رول','Croissant Roll'],['كيكة دبي','Dubai Cake'],['براونيز كراميل','Caramel Brownies'],
    ['كرانشي','Crunchy'],['كوكونت','Coconut'],['كرانش شوكو','Choco Crunch'],
    ['كوب إضافي','Extra Cup'],['بوكس قهوة','Coffee Box'],['بوكس كوكيز','Cookie Box'],
    ['بوكس كرانشي','Crunchy Box'],['بوكس سينابون','Cinnamon Roll Box'],['بوكس براونيز','Brownie Box'],
    ['خلية بيكان','Pecan Khaliya'],['خلية كلاسيك','Classic Khaliya'],['الخلية','Khaliya'],
    ['خلية','Khaliya'],['كوب حراري','Thermal Mug'],
    ['✨ جديدنا','✨ New Arrivals'],['⭐ الأكثر مبيعاً','⭐ Best Sellers'],
    ['☕ المشروبات','☕ Drinks'],['🍹 المنعشات','🍹 Refreshers'],
    ['🍪 السناكس والحلويات','🍪 Snacks and Desserts'],['🥤 الملحقات','🥤 Extras'],
    ['📦 بوكسات الضيافة','📦 Sharing Boxes'],['☕ الأكواب الحرارية','☕ Thermal Mugs'],
  ]);
  const optionEnglish=new Map([
    ['توت أسود','Blackberry'],['توت أزرق','Blueberry'],['باشن فروت','Passion Fruit'],
    ['رمان','Pomegranate'],['توت أحمر','Raspberry'],['فراولة','Strawberry'],
    ['وردي','Pink'],['أسود','Black'],['أزرق','Blue'],['موف','Mauve'],['أوف وايت','Off-white'],
    ['كلاسيك','Classic'],['بيكان','Pecan'],
  ]);
  function sourceText(record,field='name',lang=language){
    const original=record?.[field]||'';
    const supplied=record?.[field+'_'+lang]||record?.translations?.[lang]?.[field];
    if(supplied)return supplied;
    if(lang!=='en')return original;
    const isSourceProduct=record?.brand_id==='kps'&&(/^kps-\d{2}$/.test(record.source_id||record.id||'')||record.unified_khaliya===true);
    if(isSourceProduct&&['name','category'].includes(field))return menuEnglish.get(original)||original;
    if(field==='name'&&record?.code)return optionEnglish.get(original)||original;
    return original;
  }
  function productSearchText(product){
    return ['ar','en'].flatMap(lang=>[
      sourceText(product,'name',lang),sourceText(product,'category',lang),
      ...groups(product).flatMap(group=>(group.values||[]).map(value=>sourceText(value,'name',lang))),
    ]).join(' ').toLocaleLowerCase(language);
  }
  function knownMessage(message){
    if(message instanceof LocalizedText)return message;
    const entry=Object.entries(uiStrings).find(([,value])=>value.ar===message||value.en===message);
    return entry?t(entry[0]):message;
  }

  function errorMessage(error){return error?.localizedMessage||knownMessage(error?.message||error||'');}
  function serverReason(record,fallback='error_PRODUCT_UNAVAILABLE',schedule){
    if(record?.unavailable_reason==='الموهيتو متاح للاستلام من الفرع فقط.')return language==='en'?'Mojito is available for branch pickup only.':'الموهيتو متاح للاستلام من الفرع فقط.';
    if(record?.unavailable_reason==='الخلية متوفرة للاستلام من فرع 2 فقط.')return language==='en'?'Khaliya is available for pickup at Branch 2 only.':'الخلية متوفرة للاستلام من فرع 2 فقط.';if(language==='ar')return record?.reason||record?.unavailable_reason||t(fallback);
    if(record?.code==='DELIVERY_CLOSED'&&schedule?.next_open_at)return t('delivery_reopens',{time:()=>dateTime(schedule.next_open_at)});
    return uiStrings['error_'+record?.code]?t('error_'+record.code):t(fallback);
  }
  function groupLabel(group){
    if(language==='en'){
      const supplied=group?.name_en||group?.translations?.en?.name;
      if(supplied)return supplied;
      const label={TYPE:'type',COLOR:'color',FLAVOR:'flavor',FLAVOUR:'flavor'}[group?.code];
      if(label)return t(label);
    }
    return sourceText(group);
  }
  function applyStaticLanguage(){
    document.documentElement.lang=language;
    document.documentElement.dir=language==='en'?'ltr':'rtl';
    for(const element of document.querySelectorAll('[data-i18n]'))element.textContent=String(t(element.dataset.i18n));
    for(const attribute of ['aria-label','title','placeholder','content'])for(const element of document.querySelectorAll('[data-i18n-'+attribute+']'))element.setAttribute(attribute,String(t(element.getAttribute('data-i18n-'+attribute))));
    for(const [element,message] of localizedBindings){
      if(element.isConnected)element.textContent=String(message);
      else localizedBindings.delete(element);
    }
    const toggle=$('customer-language');
    toggle.textContent=language==='ar'?'English':'العربية';
    toggle.lang=language==='ar'?'en':'ar';
    toggle.setAttribute('aria-label',language==='ar'?'Switch to English':'التبديل إلى العربية');
  }
  const brandParameters=new URLSearchParams(location.search);
  const brandValue=brandParameters.has('brand_id')?brandParameters.get('brand_id'):(document.documentElement.dataset.storefrontBrand||'kps');
  const requestedBrandId=brandParameters.getAll('brand_id').length<=1&&/^[a-z][a-z0-9_-]{1,59}$/.test(brandValue||'')?brandValue:null;
  const basketStorageKey=`kps-unconfirmed-basket-v2:${requestedBrandId||'invalid'}`;
  const state = {products: [], branches: [], brandServices: {delivery:false,pickup:false,car_pickup:false}, brand: {id: requestedBrandId||'', name: requestedBrandId==='kps'?'KPS COFFEE':t("orders")}, sourceVerified: false, identityReady: false, session: {user: null, csrf_token: null}, branchId: '', service: 'pickup', location: null, cart: [], quote: null, quoteError: '', quotePending: false, quoteRevision: 0, category: '', product: null, optionValues: [], banners: [], bannerIndex: 0, bannerTimer: null, presentation: null, tickerPaused: false, conversation: null, chatSequence: 0, chatTimer: null, ordersTimer: null, orders: [], nextOrdersOffset: null, activeOrder: null, challenge: null, afterLogin: null, confirmation: null, conversations: [], contactOrders: [], nextConversationOffset: null};
  state.legacyItemMappings={};
  state.entryEnabled=false;
  state.entryDraft={};
  const entryBrowseKey=`kps-customer-browsing-v1:${requestedBrandId||'invalid'}`;
  state.entryBrowsing=false;
  try{state.entryBrowsing=sessionStorage.getItem(entryBrowseKey)==='1';}catch{}
  const locationDraftKey=`kps-delivery-location-v1:${requestedBrandId||'invalid'}`;
  const mapLink={revision:0,timer:null,controller:null,promise:null,url:'',appliedUrl:'',pending:false};
  const entryText=(ar,en)=>language==='en'?en:ar;
  function entryVerified(){const u=state.session.user;return !!(u?.role==='customer'&&u.brand_id===requestedBrandId&&state.session.csrf_token&&state.session.customer_verified===true);}
  // Presentation only: keep the shared identity, quote, order and map contracts.
  function mountEnbishanJourney() {
    if(requestedBrandId!=='enbishan'||$('enbishan-basket-dialog'))return;
    document.body.classList.add('enbishan-journey');
    state.service='delivery';
    const translated=(tag,cls,id,message)=>{const el=node(tag,cls,t(message));if(id)el.id=id;el.dataset.i18n=message;return el;};
    const dialog=(id,titleId,message)=>{
      const el=node('dialog','dialog enbishan-journey-dialog');el.id=id;el.setAttribute('aria-labelledby',titleId);
      const header=node('div','dialog-header');header.append(translated('h2','',titleId,message));
      const dismiss=node('button','icon-button','×');dismiss.type='button';dismiss.setAttribute('data-close','');dismiss.setAttribute('aria-label',String(t('close_review')));header.append(dismiss);
      const body=node('div','dialog-scroll');el.append(header,body);document.body.append(el);return {el,body};
    };
    const servicePanel=document.querySelector('.service-panel');
    const compact=node('section','enbishan-service-card');compact.id='enbishan-service-card';
    compact.append(document.querySelector('.service-options'));
    const locationButton=translated('button','button quiet full','enbishan-location-button','choose_or_change_branch');locationButton.type='button';locationButton.addEventListener('click',showEnbishanLocation);compact.append(locationButton);
    const welcome=node('div','enbishan-welcome');$('banner-area').before(welcome);welcome.append($('banner-area'),compact);
    const location=dialog('enbishan-location-dialog','enbishan-location-title','choose_or_change_branch');location.body.append(servicePanel);
    const done=translated('button','button primary full','enbishan-location-done','save_entered_location');done.type='button';done.addEventListener('click',()=>{
      if(state.service==='delivery'&&!state.location){text('location-state',t('set_your_delivery_location_to_check_coverage_and_fees'));return;}
      if(!state.branchId){text('service-notice',t('choose_a_branch_to_check_your_order'));return;}
      closeDialog('enbishan-location-dialog');renderEnbishanJourney();queueQuote();
    });location.body.append(done);
    const basketDialog=dialog('enbishan-basket-dialog','enbishan-basket-title','your_basket');basketDialog.body.append($('basket'));
    const details=$('basket-details');
    for(const child of [...details.children])if(!['quote-summary','basket-error','checkout-button'].includes(child.id)&&!child.classList.contains('basket-footnote'))child.classList.add('enbishan-checkout-only');
    const locationSummary=translated('button','button secondary full enbishan-checkout-only','enbishan-checkout-location','choose_or_change_branch');locationSummary.type='button';locationSummary.addEventListener('click',showEnbishanLocation);details.prepend(locationSummary);
    $('location-fields').classList.add('enbishan-checkout-only');$('car-fields').classList.add('enbishan-checkout-only');
    $('customer-phone').closest('label').after($('location-fields'),$('car-fields'));
    const advance=translated('button','button primary full','enbishan-continue','order_as_a_guest');advance.type='button';advance.addEventListener('click',()=>{
      state.enbishanCheckout=true;renderEnbishanJourney();$('enbishan-basket-dialog').querySelector('.dialog-scroll').scrollTop=0;
      if(!$('customer-name').value)$('customer-name').focus();
    });details.append(advance);
    const back=translated('button','button quiet full','enbishan-basket-back','review_basket');back.type='button';back.addEventListener('click',()=>{state.enbishanCheckout=false;renderEnbishanJourney();});details.append(back);
    const browse=translated('button','button quiet full','enbishan-browse','menu');browse.type='button';browse.addEventListener('click',()=>closeDialog('enbishan-basket-dialog'));$('basket').append(browse);
    const footnote=document.querySelector('.basket-footnote');footnote.removeAttribute('data-i18n');
    // Keep the original wording in both languages; only change its placement.
    $('product-options').after(document.querySelector('#product-dialog .quantity-row'));
    renderEnbishanJourney();
  }
  function showBasket() {
    if(requestedBrandId==='enbishan'&&$('enbishan-basket-dialog')){
      state.enbishanCheckout=false;renderEnbishanJourney();openDialog('enbishan-basket-dialog');
    }else $('basket').scrollIntoView({behavior:prefersLessMotion()?'auto':'smooth',block:'start'});
  }
  function showEnbishanLocation(){openDialog('enbishan-location-dialog');}
  function renderEnbishanJourney(){
    if(requestedBrandId!=='enbishan'||!$('enbishan-basket-dialog'))return;
    const checkout=!!state.enbishanCheckout,hasItems=!!state.cart.length;
    $('enbishan-basket-dialog').classList.toggle('enbishan-checkout',checkout);
    text('enbishan-basket-title',t(checkout?'review_order':'your_basket'));
    text('enbishan-location-button',branch()?`${serviceName(state.service)} · ${sourceText(branch())}`:t('choose_or_change_branch'));
    text('enbishan-checkout-location',branch()?`${serviceName(state.service)} · ${sourceText(branch())}`:t('choose_or_change_branch'));
    const guest=state.phoneRequired===false&&state.channels.guest?.available&&!state.session.user;
    text('enbishan-continue',t(guest?'order_as_a_guest':'review_and_confirm_order'));
    $('enbishan-continue').hidden=checkout||!hasItems;$('enbishan-continue').disabled=!catalogReady();
    $('enbishan-basket-back').hidden=!checkout||!hasItems;
    const footnote=document.querySelector('.basket-footnote');
    if(footnote)footnote.textContent=String(t(state.phoneRequired===false&&state.channels.guest?.available?'order_directly_without_a_verification_code_or_creating_an_account':'verified_checkout_notice'));
    document.querySelector('.branch-location-tools').hidden=state.service!=='delivery';
    $('nearest-panel').hidden=state.service!=='delivery'||!state.location;
    syncMobileBasket();
  }
  async function reviewEnbishanCheckout(){
    if(requestedBrandId!=='enbishan')return beginCheckout();
    if(!$('customer-name').value.trim()){
      showError('basket-error',t('enter_the_recipient_s_name_to_review_your_order'));$('customer-name').focus();return;
    }
    if(!state.branchId||(state.service==='delivery'&&!state.location)){showEnbishanLocation();return;}
    if(state.service==='delivery'&&!$('neighborhood').value.trim()){
      showError('basket-error',t('enter_a_clear_delivery_neighborhood_name'));$('neighborhood').focus();return;
    }
    if(!state.session.user&&state.phoneRequired===false&&state.channels.guest?.available){
      try{
        state.afterLogin=null;
        await establishIdentity(await api('/api/identity/guest',{method:'POST',data:{display_name:$('customer-name').value.trim(),phone:$('customer-phone').value.trim(),brand_id:requestedBrandId}}));
      }catch(error){showError('basket-error',errorMessage(error));return;}
    }
    await beginCheckout();
  }

  function renderCustomerEntry(){
    const section=$('customer-entry');if(!section)return;
    if(requestedBrandId==='enbishan'&&$('enbishan-basket-dialog')){section.hidden=true;renderEnbishanJourney();return;}
    const showEntry=state.entryEnabled||state.offerActive;section.hidden=!showEntry;document.body.classList.toggle('customer-entry-flow',showEntry);
    $('menu').hidden=false;$('basket').hidden=false;
    const servicePanel=document.querySelector('.service-panel');if(servicePanel)servicePanel.hidden=false;
    document.querySelector('.branch-picker')?.classList.remove('entry-branch-hidden');
    if(!showEntry)return;
    const verified=entryVerified(),locationReady=state.service!=='delivery'||!!state.location;
    const branchReady=!!state.branchId&&(state.service!=='delivery'||!state.nearest.pending&&!state.nearest.error&&state.nearest.branches.some(b=>b.id===state.branchId));
    const showIdentityForm=!verified&&!state.entryBrowsing&&requestedBrandId!=='enbishan';
    const step=!locationReady||!branchReady?0:!state.cart.length?1:2;
    const steps=node('ol','entry-steps');
    [entryText('موقعك والفرع','Location & branch'),entryText('طلبك','Your order'),entryText('المراجعة والتأكيد','Review & confirm')].forEach((label,index)=>{const item=node('li',index<step?'done':'',label);if(index===step)item.setAttribute('aria-current','step');steps.append(item);});
    // Preserve the visitor's partially typed name and number during background updates.
    if(showIdentityForm&&section.querySelector('#entry-form')&&section.dataset.lang===language){section.querySelector('.entry-steps')?.replaceWith(steps);}
    else{
      section.replaceChildren(steps);
      const title=node('h2','',showIdentityForm?(state.brand?.id==='kps'?entryText('أهلًا بك في كبز 🧡','Welcome to KPS COFFEE 🧡'):entryText('أهلًا بك','Welcome')):!locationReady?entryText('وين نوصّل طلبك؟','Where should we deliver?'):!branchReady?entryText('اختر الفرع اللي يخدمك','Choose a branch serving you'):entryText('جاهز لطلبك','Ready for your order'));title.id='entry-title';section.append(title);
      if(showIdentityForm){
        section.append(node('p','entry-description',state.offerActive?entryText('هديتك بانتظارك مع طلبك 🎁','Your gift awaits with your order 🎁'):entryText('سجل من البداية لتسهيل طلبك، أو تصفح المنيو بدون تسجيل.','Sign in now for easier ordering, or browse the menu without signing in.')));
        const form=node('form','entry-form');form.id='entry-form';
        const nameLabel=node('label','',entryText('الاسم','Name')),name=node('input');name.name='name';name.autocomplete='name';name.required=true;name.minLength=2;name.maxLength=100;name.value=$('customer-name').value;
        const phoneLabel=node('label','',entryText('رقم الجوال','Mobile number')),phone=node('input');phone.name='phone';phone.type='tel';phone.inputMode='tel';phone.autocomplete='tel';phone.required=true;phone.maxLength=30;phone.dir='ltr';phone.placeholder='05XXXXXXXX';phone.value=$('customer-phone').value;
        name.value=state.entryDraft.name||name.value;phone.value=state.entryDraft.phone||phone.value;
        name.addEventListener('input',()=>state.entryDraft.name=name.value);phone.addEventListener('input',()=>state.entryDraft.phone=phone.value);
        nameLabel.append(name);phoneLabel.append(phone);
        const submit=node('button','button',entryText('متابعة','Continue'));submit.type='submit';form.append(nameLabel,phoneLabel,submit);
        form.addEventListener('submit',async event=>{event.preventDefault();if(!form.reportValidity())return;$('customer-name').value=name.value.trim();$('customer-phone').value=phone.value.trim();state.identityIntent='login';renderAccount();$('otp-name').value=name.value.trim();$('phone').value=phone.value.trim();openDialog('account-dialog');if(state.session.user&&state.session.user.role!=='customer'||state.session.user&&state.session.user.brand_id!==requestedBrandId)return;await requestOTP({preventDefault(){}});});
        const browse=node('button','button quiet',entryText('تصفّح كضيف','Browse as a guest'));browse.id='entry-browse';browse.type='button';browse.addEventListener('click',()=>{state.entryDraft={name:name.value,phone:phone.value};if(name.value.trim())$('customer-name').value=name.value.trim();if(phone.value.trim())$('customer-phone').value=phone.value.trim();state.entryBrowsing=true;try{sessionStorage.setItem(entryBrowseKey,'1');}catch{}renderCustomerEntry();queueQuote();$('menu').scrollIntoView({behavior:prefersLessMotion()?'auto':'smooth'});$('search').focus({preventScroll:true});});form.append(browse);section.append(form);
      }else{
        const summary=node('div','entry-summary');summary.append(node('p','entry-description',step===2?entryText('راجع السلة والمبلغ، ثم أكد طلبك.','Review your basket and total, then confirm your order.'):step===1?entryText('اختر أصنافك وأضفها للسلة.','Choose your items and add them to your basket.'):entryText('حدد موقعك، ثم اختر الفرع الذي يخدمك.','Set your location, then choose a branch serving you.')));
        if(!verified)summary.append(node('p','entry-description',state.phoneRequired===false&&state.channels.guest?.available?entryText('اختر طلبك، ثم تابع كضيف أو سجل الدخول عند التأكيد.','Choose your order, then continue as a guest or sign in at checkout.'):entryText('تصفح واختر طلبك الآن؛ نتحقق من جوالك عند إتمام الطلب.','Browse and choose your order now; we verify your mobile at checkout.')));
        const account=node('button','button quiet',verified?entryText('حسابي','My account'):entryText('تسجيل الدخول','Sign in'));account.type='button';account.addEventListener('click',()=>{renderAccount();openDialog('account-dialog');});summary.append(account);section.append(summary);
      }
    }
    section.dataset.lang=language;
  }
  state.channels={guest:{available:false},email:{available:false},sms:{available:false},whatsapp:{available:false}};
  state.cityFilter='';
  state.nearest={branches:[],excluded:[],pending:false,error:'',distanceLabel:'',revision:0,controller:null};
  state.bannerRevision=0;
  state.branchCatalogId='';
  state.branchCatalogPending=false;
  state.branchCatalogRevision=0;
  const pendingKeys = new Map();
  const focusHistory = new WeakMap();
  const locale=()=>language==='en'?'en-SA':'ar-SA';
  const currency = {format:value=>new Intl.NumberFormat(locale(), {minimumFractionDigits:2,maximumFractionDigits:2}).format(value)};
  const number = {format:value=>new Intl.NumberFormat(locale()).format(value)};
  const money = (halalas) => t("0_sar",{"0":()=>(currency.format(Number(halalas || 0) / 100))});
  const complimentaryName=order=>({hospitality:language==='ar'?'ضيافة':'Hospitality',advertising:language==='ar'?'إعلان':'Advertising',compensation:language==='ar'?'تعويض':'Compensation'})[order?.snapshot?.complimentary?.kind]||'';
  const key = () => globalThis.crypto?.randomUUID?.() || `kps-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const stableKey = (operation, payload) => {
    const signature = `${requestedBrandId}:${state.session.user?.id || 'guest'}:${operation}:${JSON.stringify(payload)}`;
    let a=0x811c9dc5,b=0x9e3779b9;
    for(let i=0;i<signature.length;i++){a=Math.imul(a^signature.charCodeAt(i),0x01000193)>>>0;b=Math.imul(b^signature.charCodeAt(i),0x85ebca6b)>>>0;}
    const storageKey=`kps-pending-operation:${requestedBrandId}:${a.toString(16)}${b.toString(16)}`;
    if(!pendingKeys.has(signature)){let saved;try{saved=sessionStorage.getItem(storageKey);}catch{}pendingKeys.set(signature,saved||key());try{sessionStorage.setItem(storageKey,pendingKeys.get(signature));}catch{}}
    return {operation,signature,value:pendingKeys.get(signature),storageKey};
  };
  function releaseKey(idem){pendingKeys.delete(idem.signature);try{sessionStorage.removeItem(idem.storageKey);}catch{}if(idem.operation==='create-order')window.KPSCustomerExtras?.reset();}
  // One durable unresolved purchase per brand and identity. IndexedDB read/write
  // transactions serialize acquisition across tabs; never send if storage fails.
  let checkoutDatabase;
  let basketGeneration=key();
  function checkoutStore(update) {
    const scope=`${requestedBrandId}:${state.session.user?.id||'guest'}`;
    if(!checkoutDatabase)checkoutDatabase=new Promise((resolve,reject)=>{
      if(!globalThis.indexedDB){reject(new Error(entryText('تعذر حفظ محاولة الطلب بأمان. افتح الموقع في متصفح يدعم التخزين.','Unable to save this purchase safely. Use a browser with storage enabled.')));return;}
      const request=indexedDB.open('kps-checkout-attempts-v1',1);
      request.onupgradeneeded=()=>request.result.createObjectStore('attempts');
      request.onsuccess=()=>resolve(request.result);
      request.onerror=()=>reject(request.error);
      request.onblocked=()=>reject(new Error('Checkout storage is blocked'));
    }).catch(error=>{checkoutDatabase=null;throw error;});
    return checkoutDatabase.then(database=>new Promise((resolve,reject)=>{
      const transaction=database.transaction('attempts','readwrite');
      const store=transaction.objectStore('attempts');let result;
      const request=store.get(scope);
      request.onsuccess=()=>{
        try{const previous=request.result||null;result=update(previous);
          if(result!==previous){if(result)store.put(result,scope);else store.delete(scope);}
        }catch(error){transaction.abort();reject(error);}
      };
      transaction.oncomplete=()=>resolve(result);
      transaction.onerror=()=>reject(transaction.error);
      transaction.onabort=()=>reject(transaction.error||new Error('Checkout storage aborted'));
    }));
  }
  async function checkoutAttempt(payload) {
    return checkoutStore(previous=>{
      if(previous&&previous.status!=='rejected'&&(previous.status==='pending'||previous.generation===basketGeneration))return previous;
      if(!payload)return previous?.status==='pending'?previous:null;
      return {key:key(),payload:JSON.parse(JSON.stringify(payload)),generation:basketGeneration,status:'pending',createdAt:Date.now()};
    });
  }
  async function finishCheckout(attempt,orderId,principal=state.session.user?.id) {
    if(principal!==state.session.user?.id)throw Object.assign(new Error(t('error_SESSION_CHANGED')),{code:'SESSION_CHANGED'});
    // The result belongs to this principal even if a session was replaced while
    // the network request ran. api() verifies that before we reach this point.
    await checkoutStore(previous=>previous?.key===attempt.key?{...previous,status:'saved',orderId}:previous);
    if(principal!==state.session.user?.id)throw Object.assign(new Error(t('error_SESSION_CHANGED')),{code:'SESSION_CHANGED'});
    if(basketGeneration===attempt.generation){
      state.cart=[];state.quote=null;
      try{const saved=JSON.parse(localStorage.getItem(basketStorageKey)||'null');if(!saved||!saved.generation||saved.generation===attempt.generation)persistDraft();}catch{}
      renderCart();queueNearestBranches();
    }
    window.KPSCustomerExtras?.reset();
    closeDialog('confirm-dialog');if(requestedBrandId==='enbishan')closeDialog('enbishan-basket-dialog');
    toast(t("your_order_is_saved_you_can_follow_its_status_and_conversation"));
    await openOrders(orderId);
  }
  async function submitCheckout(payload) {
    const principal=state.session.user?.id;
    const attempt=await checkoutAttempt(payload);
    if(principal!==state.session.user?.id)throw Object.assign(new Error(t('error_SESSION_CHANGED')),{code:'SESSION_CHANGED'});
    if(!attempt)return false;
    if(attempt.status==='saved'){await finishCheckout(attempt,attempt.orderId,principal);return true;}
    try{
      const result=await api('/api/orders',{method:'POST',data:attempt.payload,idempotency:attempt.key});
      if(!result.order?.id)throw new Error(t("the_server_did_not_confirm_your_order_check_my_orders_before_retr"));
      await finishCheckout(attempt,result.order.id,principal);return true;
    }catch(error){
      // These responses prove that the transaction was rejected, never saved.
      // Unknown responses, malformed success, auth loss and transport failures
      // keep the exact frozen body/key, regardless of a changed gift quote.
      if(['GIFT_CHANGED','PRICE_CHANGED','MINIMUM_ORDER_NOT_MET','PRODUCT_UNAVAILABLE','PRODUCT_VARIANT_UNAVAILABLE','OPTION_VALUE_INVALID','OPTION_SELECTION_INVALID','UNIT_OPTIONS_REQUIRED','UNIT_OPTIONS_COUNT','UNKNOWN_OPTION_GROUP','DELIVERY_ADDRESS_REQUIRED','DELIVERY_NEIGHBORHOOD_REQUIRED','CAR_DETAILS_REQUIRED','OUTSIDE_COVERAGE','PAYMENT_METHOD_UNAVAILABLE','ONLINE_PAYMENT_REQUIRED','ORDER_QUANTITY_LIMIT'].includes(error.code)){
        await checkoutStore(previous=>previous?.key===attempt.key?{...previous,status:'rejected'}:previous);
        closeDialog('confirm-dialog');queueQuote();
      }
      throw error;
    }
  }
  async function recoverCheckout() {
    if(state.session.user?.role!=='customer'||state.session.user.brand_id!==requestedBrandId)return false;
    return submitCheckout();
  }
  const node = (tag, cls, content) => { const el = document.createElement(tag); if (cls) el.className = cls; if (content != null) setText(el,content); return el; };
  const text = (id, content) => { setText($(id),content); };
  const showError = (id, content) => { const el = $(id); setText(el,knownMessage(content||'')); el.hidden = !content; };
  const safeImage = (url) => { if(typeof url!=='string'||!url.trim())return ''; try { const parsed = new URL(url, location.origin); return parsed.origin === location.origin && !parsed.pathname.startsWith('/api/admin') ? parsed.pathname + parsed.search : ''; } catch { return ''; } };
  const city = (name) => String(name || '').replace(/\s/g, '').replace('رفحا', 'رفحاء').replace('رفحاءء', 'رفحاء').replace('قبة','قبه');
  const branch = () => state.branches.find((entry) => entry.id === state.branchId);
  const restricted = (product) => (product.available_cities || []).length > 0;
  const groups = (product) => {
    const entries=product.option_groups || product.settings?.option_groups || [];
    if(product.brand_id!=='enbishan')return entries;
    const rank=value=>{
      const name=String(value.name||value.name_en||'').trim();
      if(/^(صغير|small)(?:$|\s|·)/i.test(name))return 0;
      if(/^(وسط|متوسط|medium)(?:$|\s|·)/i.test(name))return 1;
      if(/^(كبير|large)(?:$|\s|·)/i.test(name))return 2;
      return 3;
    };
    return entries.map(group=>group.code==='SIZE'?{...group,values:[...(group.values||[])].sort((a,b)=>rank(a)-rank(b))}:group);
  };
  function fixedSingleSize(product,group){
    const values=group.values||[];
    return product.brand_id==='enbishan'&&group.code==='SIZE'&&values.length===1
      &&values[0].enabled!==false&&values[0].available!==false
      &&(/^(فردي|فردية|single)$/i.test(String(values[0].name||'').trim())
        ||/^single$/i.test(String(values[0].name_en||'').trim()));
  }
  const progressLabels={
    received:{ar:'تم استلام طلبك',en:'Your order has been received'},
    preparing:{ar:'جاري تجهيز طلبك',en:'Your order is being prepared'},
    ready:{ar:'طلبك جاهز',en:'Your order is ready'},
    on_the_way:{ar:'طلبك في الطريق',en:'Your order is on the way'},
    delivered:{ar:'تم تسليم طلبك',en:'Your order has been delivered'},
    cancelled:{ar:'تم إلغاء طلبك',en:'Your order has been cancelled'},
    problem:{ar:'طلبك قيد المتابعة',en:'Your order needs attention'}
  };
  function customerProgressLabel(order){
    const progress=order.customer_progress;
    const key=progress?.exception||progress?.stage||'received';
    return (progressLabels[key]||progressLabels.received)[language];
  }
  function renderCustomerProgress(order){
    const progress=order.customer_progress||{stage:'received',stages:['received']};
    const section=node('section','customer-progress');
    section.setAttribute('aria-label',t('order_status_history'));
    section.append(node('span','small muted',order.service==='delivery'?`${t('preparation')} / ${t('delivery')}`:t('preparation')));
    section.append(node('h4','',customerProgressLabel(order)));
    const list=node('ol','customer-progress-steps');
    const current=progress.stages.indexOf(progress.stage);
    progress.stages.forEach((stage,index)=>{
      const item=node('li',index<current?'complete':index===current?'current':'');
      item.append(node('span','',progressLabels[stage]?.[language]||''));
      if(index===current)item.setAttribute('aria-current','step');
      list.append(item);
    });
    section.append(list);return section;
  }
  const isMug = (product) => ['kps-30', 'kps-31'].includes(product.source_id || product.id) || groups(product).some((g) => g.code === 'COLOR');
  const isKhaliya = (product) => product.unified_khaliya === true;
  const cartLineName = (product, options={}) => isKhaliya(product||{}) ? `${sourceText(product)} — ${sourceText(((groups(product).find((g)=>g.code==='TYPE')?.values)||[]).find((v)=>v.code===options.TYPE))||t("choose_a_type")}` : sourceText(product) || t("saved_item");
  const optionLabel = (product, options) => groups(product).map((g) => { const v = (g.values || []).find((entry) => (entry.code || entry.id) === options[g.code]); return v ? `${groupLabel(g)}: ${sourceText(v)}` : ''; }).filter(Boolean).join(' · ');
  const serviceName = (value) => ({delivery: t("delivery_2"), pickup: t("branch_pickup"), car: t("car_pickup")}[value] || value);
  const statusName = (value) => ({new:t("new"), pending:t("awaiting_processing"), accepted:t("accepted"), preparing:t("preparing"), ready:t("ready"), handed_over:t("handed_to_driver"), handed_to_driver:t("handed_to_driver"), pending_assignment:t("awaiting_assignment"), assigned:t("assigned"), collected:t("collected"), unpaid:t("uncollected"), paid:t("paid"), partially_paid:t("partially_paid"), delivered:t("delivered"), completed:t("completed"), cancelled:t("cancelled"), rejected:t("rejected"), out_for_delivery:t("on_the_way"), picked_up:t("picked_up_by_driver"), not_assigned:t("unassigned"), unassigned:t("unassigned"), refunded:t("refunded"), partially_refunded:t("partially_refunded"), not_required:t("not_applicable"),not_applicable:t("not_applicable"), pending_acceptance:t("awaiting_task_acceptance"), received:t("received"), awaiting_payment:t("awaiting_collection")}[value] || t("processing"));
  const dateTime = (value) => { if (!value) return ''; try { return new Intl.DateTimeFormat(locale(), {dateStyle:'short', timeStyle:'short', timeZone:'Asia/Riyadh'}).format(new Date(value)); } catch { return ''; } };
  function toast(message) { text('toast', message); $('toast').hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => { $('toast').hidden = true; }, 3400); }
  function openDialog(id) { const dialog = $(id); if (dialog.open) return; focusHistory.set(dialog, document.activeElement); dialog.showModal(); syncCustomerNavigation();if(requestedBrandId==='enbishan')syncMobileBasket(); }
  function clearConfirmation() { const pending=state.confirmation; state.confirmation=null; if(pending?.cancel)pending.cancel(); }
  function closeDialog(dialog) { if (typeof dialog === 'string') dialog = $(dialog); if (dialog.open) { if(dialog.id==='confirm-dialog')clearConfirmation(); dialog.close(); } }
  function minimumOrderMessage(detail) {
    const minimum=detail?.minimum_order_halalas,subtotal=detail?.subtotal_halalas,remaining=detail?.remaining_halalas;
    if(detail?.currency!=='SAR'||![minimum,subtotal,remaining].every(Number.isSafeInteger)||
        minimum<=0||subtotal<0||remaining<=0||remaining!==minimum-subtotal)return null;
    return t('delivery_minimum_remaining',{minimum:()=>money(minimum),remaining:()=>money(remaining)});
  }
  async function api(path, {method='GET', data, idempotency, signal} = {}) {
    const principal=state.session.user?.id;
    const headers = {'Accept': 'application/json', 'X-KPS-Context':'customer'};
    if (data !== undefined) headers['Content-Type'] = 'application/json';
    if (method !== 'GET' && method !== 'HEAD') { if (state.session.csrf_token) headers['X-CSRF-Token'] = state.session.csrf_token; headers['Idempotency-Key'] = idempotency || key(); }
    let response;
    try { response = await fetch(path, {method, credentials: 'same-origin', headers, body: data === undefined ? undefined : JSON.stringify(data), signal, cache: 'no-store'}); }
    catch (cause) { if (cause.name === 'AbortError') throw cause; const error = new Error(t("unable_to_connect_check_your_internet_and_try_again_this_action_h")); error.code = 'NETWORK'; throw error; }
    let body; try { body = await response.json(); } catch { body = {}; }
    if(principal&&principal!==state.session.user?.id){const error=new Error(String(t('error_SESSION_CHANGED')));error.code='SESSION_CHANGED';throw error;}
    if (!response.ok) {
      const detail = body.error || body.detail || body;
      const code=detail?.code||body.code||String(response.status);
      const fallback=response.status===401?'your_session_expired_start_a_new_guest_session_or_sign_in_with_yo':response.status===429?'too_many_attempts_wait_a_little_and_try_again':'unable_to_complete_this_action_check_your_details_and_try_again';
      const localized=(code==='MINIMUM_ORDER_NOT_MET'&&minimumOrderMessage(detail))||(language==='ar'&&typeof detail?.message==='string'?knownMessage(detail.message):uiStrings['error_'+code]?t('error_'+code):t(fallback));
      const error = new Error(String(localized));
      error.localizedMessage=localized;
      error.code = code; error.status = response.status; error.details = detail;
      if (response.status === 401) { state.session = {user: null, csrf_token: null}; renderAccount(); }
      throw error;
    }
    return body;
  }
  async function busy(button, fn) { if (button.disabled) return; const original = localizedBindings.get(button)||(button.dataset.i18n?t(button.dataset.i18n):button.textContent); button.disabled = true; button.setAttribute('aria-busy', 'true'); try { return await fn(); } finally { button.disabled = false; button.removeAttribute('aria-busy'); setText(button,original); if(button.id==='checkout-button')renderQuote(); } }
  function persistDraft() { try { localStorage.setItem(basketStorageKey, JSON.stringify({brandId: state.brand.id, generation:basketGeneration, cart: state.cart.map(({productId, quantity, options, note, restoredName}) => ({productId, quantity, options, note, restoredName}))})); } catch {} }
  function restoreDraft() {
    try {
      const saved=JSON.parse(localStorage.getItem(basketStorageKey)||(state.phoneRequired!==false?localStorage.getItem('kps-unconfirmed-basket-v1'):null)||'{}');
      if(saved.brandId!==state.brand.id||!Array.isArray(saved.cart))return;
      basketGeneration=typeof saved.generation==='string'?saved.generation:'legacy:'+JSON.stringify(saved.cart);
      state.cart=saved.cart.slice(0,100).filter((item)=>typeof item.productId==='string'&&Number.isInteger(item.quantity)&&item.quantity>=1&&item.quantity<=30).map((item)=>{
        const options=typeof item.options==='object'&&item.options&&!Array.isArray(item.options)?{...item.options}:{};
        const mapping=state.legacyItemMappings[item.productId];
        // The former IDs prove the original type. Never replace an explicit saved selection.
        const remapped=mapping&&Object.keys(options).length===0;
        return {productId:remapped?mapping.product_id:item.productId,quantity:item.quantity,
          options:remapped?{...mapping.options}:options,note:typeof item.note==='string'?item.note.slice(0,300):'',
          restoredName:remapped?mapping.original_name:typeof item.restoredName==='string'?item.restoredName.slice(0,100):'',rowId:key()};
      }).filter((item)=>state.products.some((product)=>product.id===item.productId)||state.legacyItemMappings[item.productId]);
      if(state.cart.some((item)=>item.restoredName))toast(t("saved_khaliya_items_are_linked_to_their_original_types_review_the"));
    } catch {}
  }
  function syncMobileBasket() {if(requestedBrandId==='enbishan'&&$('enbishan-basket-dialog')){$('mobile-basket').hidden=!state.cart.length||!!document.querySelector('dialog[open]');return;}const basket=$('basket'),viewport=window.visualViewport,height=viewport?.height||innerHeight,top=viewport?.offsetTop||0;basket.classList.toggle('basket-scroll-flow',basket.offsetHeight>height-40);const rect=basket.getBoundingClientRect(),visible=rect.bottom>top&&rect.top<top+height;$('mobile-basket').hidden=!state.cart.length||visible||state.entryEnabled&&basket.hidden;}
  window.addEventListener('scroll',syncMobileBasket,{passive:true});
  window.addEventListener('resize',syncMobileBasket,{passive:true});
  window.visualViewport?.addEventListener('resize',syncMobileBasket,{passive:true});
  window.visualViewport?.addEventListener('scroll',syncMobileBasket,{passive:true});
  if(globalThis.ResizeObserver){state.basketResizeObserver=new ResizeObserver(syncMobileBasket);state.basketResizeObserver.observe($('basket'));}
  function productFor(line) {
    const current=state.products.find((product)=>product.id===line.productId);if(current)return current;
    const mapping=state.legacyItemMappings[line.productId];if(!mapping)return undefined;
    const parent=state.products.find((product)=>product.id===mapping.product_id);if(!parent)return undefined;
    // Unrecognized old selections remain visible and blocked, never silently discarded or repriced as a different type.
    const value=groups(parent).find((group)=>group.code==='TYPE')?.values?.find((entry)=>entry.code===mapping.options.TYPE);
    return {...parent,id:line.productId,name:mapping.original_name,price_halalas:parent.price_halalas+Number(value?.price_delta_halalas||0),
      enabled:false,source_verified:false,unified_khaliya:false,option_groups:[],unavailable_reason:t("review_the_saved_khaliya_type_and_choose_it_from_the_unified_card")};
  }
  function linePrice(line,product=productFor(line)) { const variant=globalThis.KPSMenuVariants?.selected(product,line.options);if(variant)return variant.price_halalas;const extra = groups(product || {}).reduce((sum,g) => sum + Number((g.values || []).find((v) => (v.code || v.id) === line.options[g.code])?.price_delta_halalas || 0), 0); return Number(product?.price_halalas || 0) + extra; }
  function draftSubtotal() { return state.cart.reduce((sum, line) => sum + linePrice(line) * line.quantity, 0); }
  function eligibleForProduct(product, target=branch()) { if (state.branchId && state.branchCatalogId !== state.branchId) return {ok:false, reason:t(state.branchCatalogPending?"checking_branch_options":"branch_options_check_failed")}; if (product.enabled === false) return {ok:false, reason:t("this_item_is_temporarily_unavailable")}; if (restricted(product)) { if (!target) return {ok:false, reason:t("choose_a_branch_to_check_availability_in_hafar_al_batin")}; if (!product.available_cities.some((c) => city(c) === city(target.city))) return {ok:false, reason:t("available_only_in_hafar_al_batin_choose_an_eligible_branch")}; } if (state.branchId && product.available === false && product.unavailable_reason && !/تحقق|مهيأ|مصدر|سعر/.test(product.unavailable_reason)) return {ok:false, reason:serverReason(product)}; return {ok:true, reason:''}; }
  function catalogReady() { return state.cart.length > 0 && state.cart.every((line) => productFor(line)?.source_verified === true); }
  function cartOptionsAvailable(line,product=productFor(line)) {
    if(product?.variant_price_matrix&&!globalThis.KPSMenuVariants?.selected(product,line.options))return false;
    return Object.keys(line.options||{}).every(code=>groups(product||{}).some(group=>group.code===code))&&groups(product||{}).every(group=>{
      const choice=line.options?.[group.code];
      if(!choice)return group.required===false;
      const value=(group.values||[]).find(value=>(value.code||value.id)===choice);
      return Boolean(value&&value.enabled!==false&&value.available!==false);
    });
  }
  function appendBasketReview(container) {
    const review=node('button','button secondary',t('review_basket'));
    review.type='button';review.addEventListener('click',()=>{showBasket();});
    container.append(review);
  }
  function updatePolicyLinks(){for(const [id,path] of [['privacy-link','privacy'],['terms-link','terms'],['account-privacy-link','privacy'],['account-terms-link','terms'],['refunds-link','refunds'],['account-refunds-link','refunds'],['identity-privacy-link','privacy']])$(id).href=`/${path}?brand_id=${encodeURIComponent(requestedBrandId)}&lang=${language}`;}
  function maskedPhone(value=''){return value.length>4?value.slice(0,4)+' •••• '+value.slice(-3):value;}
  function renderAccount() {
    window.KPSCustomerExtras?.render({brand:state.brand,session:state.session,language,api,queueQuote});
    updatePolicyLinks();text('customer-phone-status',state.session.customer_verified?t('phone_verified'):t('unverified'));$('customer-phone').readOnly=state.session.customer_verified===true;const guestNotice=$('guest-form').querySelector('p');guestNotice.id='guest-flow-note';guestNotice.dataset.i18n=state.identityIntent==='contact'?'contact_guest_notice':'order_directly_without_a_verification_code_or_creating_an_account';text('guest-flow-note',t(guestNotice.dataset.i18n));$('guest-submit').dataset.i18n=state.identityIntent==='contact'?'start_contact':'order_as_a_guest';text('guest-submit',t($('guest-submit').dataset.i18n));
 ['account-button','orders-button','contacts-button'].forEach((id)=>$(id).disabled=!state.identityReady);const user=state.session.user,authenticated=Boolean(user&&state.session.csrf_token);text('account-button-label',user&&user.role!=='customer'?t("staff_account"):t("my_account"));$('account-other-brand').hidden=!user||user.brand_id===requestedBrandId;$('account-profile').hidden=!authenticated;$('guest-form').hidden=(state.phoneRequired!==false&&state.identityIntent!=='contact')||authenticated||!state.channels.guest?.available;$('email-login-button').hidden=true;$('email-request-form').hidden=true;$('otp-request-form').hidden=state.phoneRequired!==false?(authenticated&&state.session.customer_verified===true):authenticated||!(state.channels.sms?.available||state.channels.whatsapp?.available);$('otp-verify-form').hidden=true;$('otp-unavailable').hidden=true;$('link-email-button').hidden=true;if(user){text('profile-label',user.display_name||t("signed_in"));text('identity-label',user.identity_kind==='guest'?t("guest_unverified_contact_number"):user.email_verified_at?t("email_verified_phone_verification_is_separate"):t("verification_depends_on_the_channel"));if(user.brand_id===requestedBrandId){if(!$('customer-name').value)$('customer-name').value=user.display_name||'';if(!$('customer-phone').value)$('customer-phone').value=user.phone||'';}}$('otp-name').value=$('customer-name').value||user?.display_name||'';$('phone').value=$('customer-phone').value||user?.phone||'';$('customer-profile-form').hidden=!state.session.customer_verified||user?.role!=='customer';if(user)$('profile-name').value=user.display_name||'';if(state.session.customer_verified&&user?.role==='customer'&&user.brand_id===requestedBrandId)loadCustomerProfile();$('guest-name').value=$('customer-name').value;$('guest-phone').value=$('customer-phone').value;
    const directory=$('account-locations');directory.replaceChildren();for(const b of state.branches){const url=directionsLink(b);if(!url)continue;const link=node('a','small',`${b.branch_no} · ${sourceText(b)} · ${b.city}`);link.href=url;link.target='_blank';link.rel='noopener noreferrer';directory.append(link);}
    const ownCustomer=authenticated&&user.role==='customer'&&user.brand_id===requestedBrandId;
    $('account-customer-actions').hidden=!ownCustomer;
    $('customer-profile-form').hidden=!ownCustomer||!state.session.customer_verified;
    $('account-contact').hidden=ownCustomer;
    $('account-signout').hidden=!authenticated;
    $('profile-phone').hidden=!ownCustomer||!state.session.customer_verified;
    text('profile-phone',ownCustomer&&state.session.customer_verified?maskedPhone(user.phone):'');
    if(ownCustomer&&state.session.customer_verified)text('identity-label',t('phone_verified'));
    text('account-branch-label',branch()?sourceText(branch()):t('choose_or_change_branch'));
    renderCustomerEntry();
  }
  async function establishIdentity(result) {if(!result.user||!result.csrf_token)throw new Error(t("the_server_did_not_confirm_a_secure_session"));state.session=result;renderAccount();closeDialog('account-dialog');if(result.user.role!=='customer'){location.assign('/ops.html');return;}queueQuote();const callback=state.afterLogin;state.afterLogin=null;if(callback)await callback();}
  async function requestGuest(event){event.preventDefault();await busy($('guest-submit'),async()=>{try{const data={display_name:$('guest-name').value.trim(),phone:$('guest-phone').value.trim(),brand_id:requestedBrandId};$('customer-name').value=data.display_name;$('customer-phone').value=data.phone;await establishIdentity(await api('/api/identity/guest',{method:'POST',data}));toast(t("a_secure_guest_session_has_been_created"));}catch(error){showError('account-error',errorMessage(error));}});}
  async function requestEmail(event){event.preventDefault();await busy($('email-request-submit'),async()=>{try{await api('/api/identity/email/request',{method:'POST',data:{email:$('login-email').value.trim(),brand_id:requestedBrandId,purpose:state.session.user?.identity_kind==='guest'?'customer_link':'email_login'}});text('email-request-state',t("if_this_email_is_eligible_you_will_receive_a_secure_single_use_li"));}catch(error){showError('account-error',errorMessage(error));}});}
  async function verifyEmailLink(){const fragment=new URLSearchParams(location.hash.slice(1));if(!fragment.has('identity_challenge')||!fragment.has('identity_token'))return;history.replaceState(null,'',location.pathname+location.search);state.emailLink={challenge_id:fragment.get('identity_challenge'),token:fragment.get('identity_token')};try{const result=await api('/api/identity/email/verify',{method:'POST',data:state.emailLink});state.emailLink=null;await establishIdentity(result);toast(t("email_verified"));}catch(error){showError('account-error',errorMessage(error));if(error.code==='STAFF_PROFILE_REQUIRED'){$('guest-form').hidden=true;$('email-login-button').hidden=true;$('identity-onboarding-form').hidden=false;}text('account-title',t("email_verification"));openDialog('account-dialog');}}
  function directionsLink(b){if(!b)return '';const raw=b.directions_url;if(typeof raw!=='string')return '';try{const u=new URL(raw);return u.protocol==='https:'&&['www.google.com','google.com','maps.google.com','maps.app.goo.gl','goo.gl'].includes(u.hostname)?u.href:'';}catch{return '';}}
  function showImage(product){const url=safeImage(product?.image_url);if(!url){toast(t("the_photo_is_currently_unavailable"));return;}state.imageProduct=product;$('image-full').src=url;$('image-full').alt=sourceText(product);text('image-title',sourceText(product));openDialog('image-dialog');}
  function renderBranches() {
    const cities=new Map();
    for(const b of state.branches)if(b.city&&!cities.has(city(b.city)))cities.set(city(b.city),b.city);
    const citySelect=$('city-select');
    citySelect.replaceChildren(new Option(t("all_cities"),''));
    for(const [value,label] of cities)citySelect.append(new Option(label,value));
    citySelect.value=state.cityFilter;
    const select=$('branch-select');
    select.replaceChildren(new Option(t("choose_a_branch"),''));
    for(const b of state.branches){
      if(state.entryEnabled&&state.service==='delivery'&&(!state.location||state.nearest.pending||!state.nearest.branches.some(row=>row.id===b.id)))continue;
      // An existing choice remains visible when the city filter changes.
      if(state.cityFilter&&city(b.city)!==state.cityFilter&&b.id!==state.branchId)continue;
      const service=b.services?.[state.service];
      const status=service?.available===false?t("unavailable_0",{"0":()=>(serverReason(service,'service_paused',b.delivery_schedule))}):'';
      const current=state.cityFilter&&city(b.city)!==state.cityFilter?t("your_current_selection"):'';
      select.append(new Option(`${b.branch_no} · ${sourceText(b)} · ${b.city}${status}${current}`,b.id));
    }
    select.value=state.branchId;
    text('branch-picker-hint',state.branchId&&state.cityFilter&&city(branch()?.city)!==state.cityFilter?
      t("your_current_branch_is_in_another_city_it_stays_selected_until_yo"):
      t("choose_a_city_and_branch_without_location_access_ineligible_branc"));
  }
  function renderNearestBranches() {
    const nearest=state.nearest,panel=$('nearest-panel'),list=$('nearest-branches'),excluded=$('nearest-excluded-list');
    panel.hidden=!state.location;
    list.replaceChildren();excluded.replaceChildren();
    $('nearest-excluded').hidden=true;
    if(panel.hidden)return;
    if(nearest.pending){text('nearest-state',t("checking_branches_location_service_and_basket_items"));panel.setAttribute('aria-busy','true');return;}
    panel.removeAttribute('aria-busy');
    if(nearest.error){
      if(nearest.errorCode==='OPTION_VALUE_INVALID'){
        const names=[...new Set(state.cart.filter(line=>!cartOptionsAvailable(line)).map(line=>{
          const product=productFor(line);return `${sourceText(product||{})||t('items')}${optionLabel(product||{},line.options)?' · '+optionLabel(product||{},line.options):''}`;
        }))];
        text('nearest-state',names.length?t('nearest_saved_options_unavailable',{items:()=>names.join(language==='ar'?'، ':', ')}):nearest.error);
        appendBasketReview(list);
      }else text('nearest-state',t("0_you_can_choose_a_city_and_branch_manually",{"0":()=>(nearest.error)}));
      return;
    }
    const eligible=nearest.branches.filter(b=>!state.cityFilter||city(b.city)===state.cityFilter);
    text('nearest-state',eligible.length?
      t("0_ranked_for_1_and_basket_items",{"0":()=>((language==='ar'?nearest.distanceLabel:'')||t("approximate_geographic_distance_not_road_distance")),"1":()=>(serviceName(state.service))}):
      t("no_verified_eligible_branches_for_this_selection_0_review_your_ci",{"0":()=>(state.cityFilter?t("in_the_selected_city"):'')}));
    if(!eligible.length){
      const basketBlocked=nearest.excluded.find(b=>b.code==='PRODUCT_UNAVAILABLE'&&
        (!state.cityFilter||city(b.city)===state.cityFilter)&&b.unavailable_products?.length);
      if(basketBlocked){
        const names=[...new Set(basketBlocked.unavailable_products.map(item=>
          sourceText(state.products.find(product=>product.id===item.product_id)||{})||t('items')))];
        text('nearest-state',t('nearest_basket_unavailable',{items:()=>names.join(language==='ar'?'، ':', ')}));
        appendBasketReview(list);
      }
    }
    for(const b of eligible){
      const card=node('article','nearest-branch-card');
      card.dataset.branchId=b.id;
      const content=node('div');
      content.append(node('h3','',`${b.branch_no} · ${sourceText(b)}`),node('p','small muted',b.city));
      const meters=Number(b.direct_distance_meters);
      if(Number.isFinite(meters)&&meters>=0){
        const distance=meters>=1000?t("0_km",{"0":()=>(new Intl.NumberFormat(locale(),{maximumFractionDigits:1}).format(meters/1000))}):t("0_m",{"0":()=>(number.format(Math.round(meters)))});
        content.append(node('p','branch-distance',t("0_approximate_geographic_distance",{"0":()=>(distance)})));
      }
      card.append(content);
      const choose=node('button',`button ${b.id===state.branchId?'secondary':'primary'}`,b.id===state.branchId?t("selected_branch"):t("choose_this_branch"));
      choose.type='button';choose.dataset.branchId=b.id;choose.setAttribute('aria-pressed',String(b.id===state.branchId));
      choose.addEventListener('click',()=>changeBranch(b.id));card.append(choose);list.append(card);
    }
    // Exclusion diagnostics are not part of the customer branch picker.
    // The list stays empty and hidden; eligibility is still enforced by the API.
  }
  function queueNearestBranches() {
    clearTimeout(queueNearestBranches.timer);
    const nearest=state.nearest;
    nearest.controller?.abort();nearest.controller=null;
    nearest.revision++;nearest.error='';nearest.errorCode='';nearest.branches=[];nearest.excluded=[];
    nearest.pending=Boolean(state.location);
    renderNearestBranches();
    renderCustomerEntry();
    if(state.location)queueNearestBranches.timer=setTimeout(loadNearestBranches,200);
  }
  async function loadNearestBranches() {
    if(!state.location)return;
    const nearest=state.nearest,revision=nearest.revision,controller=new AbortController();
    nearest.controller=controller;
    const payload={brand_id:state.brand.id||'kps',location:{...state.location},service:state.service,
      product_ids:[...new Set(state.cart.map(line=>line.productId))],items:orderBody().items};
    try{
      const result=await api('/api/branches/nearest',{method:'POST',data:payload,signal:controller.signal});
      if(revision!==nearest.revision)return;
      // Only server-qualified, verified rows become suggestions.
      nearest.branches=(result.branches||[]).filter(b=>b.available===true&&b.location_verified===true&&Number.isFinite(b.direct_distance_meters));
      nearest.excluded=result.excluded||[];nearest.distanceLabel=result.distance_label||'';
      for(const row of [...nearest.branches,...nearest.excluded]){
        const existing=state.branches.find(b=>b.id===row.id);
        if(existing){if(row.services)existing.services=row.services;if(row.delivery_schedule)existing.delivery_schedule=row.delivery_schedule;existing.directions_url=row.directions_url||null;existing.location_verified=row.location_verified===true;}
      }
      renderBranches();renderService();
    }catch(error){if(revision===nearest.revision&&error.name!=='AbortError'){nearest.error=errorMessage(error);nearest.errorCode=error.code;}}
    finally{if(revision===nearest.revision){nearest.pending=false;nearest.controller=null;
        if(requestedBrandId==='enbishan'&&state.service==='delivery'&&!nearest.error&&nearest.branches.length&&!nearest.branches.some(b=>b.id===state.branchId)){
          // Do not silently remove basket lines when moving to a different branch.
          const candidate=nearest.branches[0];
          if(!state.cart.some(line=>{const p=productFor(line);return restricted(p||{})&&!p.available_cities.some(c=>city(c)===city(candidate.city));}))changeBranch(candidate.id);
        }
        renderBranches();renderNearestBranches();renderCustomerEntry();}}
  }
  function renderService(){
  const selected=branch();
  const inputs=[...document.querySelectorAll('input[name=service]')];
  const permitted=(input)=>{
    const key=input.value==='car'?'car_pickup':input.value;
    if(state.brandServices?.[key]===false)return false;
    return !['BRAND_SERVICE_DISABLED','SERVICE_DISABLED','DELIVERY_CITY_NOT_ELIGIBLE']
      .includes(selected?.services?.[input.value]?.code);
  };
  const visible=inputs.filter(permitted);
  const switched=visible.length>0&&!visible.some(input=>input.value===state.service);
  if(switched){
    // A delivery-only branch must never leave a hidden pickup option selected.
    state.service=(visible.find(input=>input.value==='delivery')||visible[0]).value;
  }
  text('branch-summary',selected?`${selected.branch_no} · ${sourceText(selected)}`:t("choose_a_branch_to_check_availability"));
  $('location-fields').hidden=state.service!=='delivery';
  $('neighborhood').required=state.service==='delivery';
  $('neighborhood').setAttribute('aria-required',String(state.service==='delivery'));
  $('car-fields').hidden=state.service!=='car';
  const onlineBranchPayment=state.brand?.branch_online_required===true&&['pickup','car'].includes(state.service);
  const paymentOptions=document.querySelector('.payment-options');
  if(paymentOptions)paymentOptions.hidden=onlineBranchPayment;
  const onlinePaymentNotice=$('online-payment-notice');
  if(onlinePaymentNotice){onlinePaymentNotice.hidden=!onlineBranchPayment;onlinePaymentNotice.textContent=entryText('الدفع إلكترونيًا بعد موافقة الكاشير — لا دفع عند الاستلام.','Pay online after cashier approval — no payment at handover.');}
  const arrivalPaymentNotice=$('arrival-payment-notice');
  if(arrivalPaymentNotice)arrivalPaymentNotice.hidden=onlineBranchPayment;

  const directions=directionsLink(selected);
  $('branch-directions').hidden=!directions;
  if(directions)$('branch-directions').href=directions;
  else $('branch-directions').removeAttribute('href');
  for(const input of inputs){
    const allowed=permitted(input);
    input.disabled=!allowed;
    input.closest('label').hidden=!allowed;
    input.checked=allowed&&input.value===state.service;
  }
  const service=selected?.services?.[state.service];
  const none=visible.length===0;
  text('service-notice',none?
    (selected?t("new_orders_are_paused_at_this_branch_choose_another_branch"):t("new_orders_are_currently_paused_for_all_services")):
    selected?
      service?.available?
        t("service_is_available_under_the_current_branch_settings_0",{"0":()=>(state.service==='delivery'?t("your_delivery_coverage_will_be_checked_when_reviewing_the_order"):'')}):
        serverReason(service,'this_service_has_not_been_enabled_at_this_branch',selected?.delivery_schedule):
      t("choose_a_branch_each_service_is_available_independently"));
  $('service-notice').classList.toggle('status',none||service?.available===false);
  if(switched){
    renderBranches();
    queueNearestBranches();
    queueQuote();
  }
  renderCustomerEntry();
}
  // Presentation only: do not preselect a size or alter catalog prices.
  function menuSection(product) {
    if(product.brand_id!=='enbishan')return product.category||'';
    const name=product.name||'';
    if(/موهيتو|فتة/.test(name))return 'الفتة والموهيتو';
    if(/بوكس|سيجنتشر|عنبيشن كبير/.test(name))return 'البوكسات';
    if(/ورق عنب|ملفوف/.test(name))return 'ورق العنب والملفوف';
    if(/كبة|سمبوسة/.test(name))return 'المقليات';
    if(/مسخن/.test(name))return 'المسخن';
    if(/نودلز/.test(name))return 'النودلز';
    return product.category||'';
  }
  const enbishanSections=['ورق العنب والملفوف','المحاشي','المقليات','البوكسات','المسخن','الفتة والموهيتو','النودلز','المخبوزات','المشروبات'];
  function menuSectionLabel(section) {
    const english={'ورق العنب والملفوف':'Grape leaves & cabbage','البوكسات':'Boxes','المحاشي':'Stuffed vegetables','المقليات':'Fried bites','النودلز':'Noodles','المسخن':'Musakhan','الفتة والموهيتو':'Fattah & Mojito','المخبوزات':'Bakery','المشروبات':'Drinks'};
    return requestedBrandId==='enbishan'?entryText(section,english[section]||section):sourceText(state.products.find(p=>p.category===section),'category');
  }
  function menuRank(product, overview=false) {
    // Keep each section together in the full menu as well as category tabs.
    const section=enbishanSections.indexOf(menuSection(product));
    const grapeOrder=['enbishan-menu-894fad254b67890eafc2c47e','enbishan-menu-c434c397f9072844ba9c93b4','enbishan-menu-6c7a9f521d5f21500c2e6d9a','enbishan-menu-cfe9ad0bccc96e0d0537be28','enbishan-menu-2abc3c6b5efd0180c521eef0'];
    const index=grapeOrder.indexOf(product.id);
    return (section<0?99:section)*100+(index<0?50:index);
  }
  function menuCover(product) {
    if(product.brand_id!=='enbishan')return {image_url:product.image_url};
    const sizes=groups(product).find(g=>g.code==='SIZE')?.values||[];
    const preferred=['عائلي','كبير','وسط'];
    for(const label of preferred){
      const size=sizes.find(v=>String(v.name||'').includes(label));
      const image=size&&(product.variant_images||[]).find(v=>v.options?.SIZE===(size.code||size.id)&&safeImage(v.image_url));
      if(image)return {image_url:image.image_url,label:sourceText(size)};
    }
    return {image_url:product.image_url};
  }
  // Customer-facing copy only; catalog names, options and prices stay authoritative.
  const enbishanMenuCopy={
    "enbishan-menu-028afa83b0694d93a171b9b5": ["لقيمات مسخن بطعم مميز؛ اختر الحجم المناسب وشاركها مع أحبابك.", "Musakhan bites with a distinctive taste. Pick a size and share."],
    "enbishan-menu-0d6d5352fce7cbd44ad6594a": ["بطاطس محشية لعشّاق المحاشي؛ إضافة لذيذة لسفرتك.", "Stuffed potatoes for mahashi lovers. A delicious addition to your table."],
    "enbishan-menu-2abc3c6b5efd0180c521eef0": ["لفّات ملفوف لذيذة؛ اختر حجمك واستمتع بكل لفّة.", "Delicious cabbage rolls. Choose your size and enjoy every roll."],
    "enbishan-menu-3facf0781142b248264e4be9": ["بوكس يجمع 5 كبة + 5 سمبوسة + 5 ورق عنب كريسبي؛ تشكيلة جاهزة للمشاركة.", "A sharing box with 5 kibbeh + 5 samosa + 5 crispy grape leaves. A selection to enjoy together."],
    "enbishan-menu-43ff27d14aa7990095f3af11": ["كبة وسمبوسة مع بعض؛ صنفين من القرمشة في صحن واحد.", "Kibbeh and sambosa together. Two crispy favourites on one plate."],
    "enbishan-menu-6c7a9f521d5f21500c2e6d9a": ["ورق عنب بقرمشة مختلفة؛ لقمة تغيّر مزاجك.", "Grape leaves with a crisp twist. A little crunch in every bite."],
    "enbishan-menu-7b0752b4daa269674e08576f": ["بوكس سيجنتشر للمشاركة؛ تشكيلة عنبيشن تجمع أحبابك على أكثر من طعم.", "Our Signature sharing box brings your favourites together for the whole gathering."],
    "enbishan-menu-7f0dd1ebba3e28f5b134aea5": ["بصل محشي بطعمه المميز؛ لقمة تستاهل التجربة.", "Stuffed onions with their distinctive taste. A bite worth trying."],
    "enbishan-menu-894fad254b67890eafc2c47e": ["لفّات ورق عنب بطعم تحبه؛ اختر نكهتين أو مشكل، والحجم على قد جمعتك.", "Grape leaf rolls your way. Choose two flavours or Mixed, in a size for your gathering."],
    "enbishan-menu-8b9c0ac992a637fc6e97e3e9": ["سمبوسة مقرمشة؛ رفيقة سفرتك ولقمة تحب تكررها.", "Crisp sambosa: a table favourite you will reach for again."],
    "enbishan-menu-97454e7c5796562295e4937c": ["نودلز لذيذة تغيّر بها اختيارك؛ صحن فردي على ذوقك.", "Delicious noodles for a change of pace, served as an individual plate."],
    "enbishan-menu-9a85ab30d44850bc6d2e6e18": ["صحن فتة يجمع الطعم والمتعة؛ اختيار لذيذ لسفرتك.", "A satisfying plate of fattah, full of flavour for your table."],
    "enbishan-menu-b641eb7a3572b133717e64ef": ["تشكيلة عنبيشن في بوكس واحد؛ محاشي ولفّات وكبة لتنويع سفرتك.", "The Enbishan selection in one box: stuffed vegetables, rolls and kibbeh."],
    "enbishan-menu-b912778fdbf8eeb4211ccf60": ["خلية بحجم فردي؛ لقيمات لذيذة للحظة تستمتع فيها.", "An individual khaliya serving for a delicious little moment."],
    "enbishan-menu-ba943fd11771582e9003ac47": ["تشكيلة محاشي تجمع أكثر من طعم في صحن واحد؛ لكل لقمة متعتها.", "A selection of stuffed vegetables in one plate. A different delight in every bite."],
    "enbishan-menu-babdb49012d7b67402db8fc2": ["بوكس يجمع 5 كبة + 6 مسخن + 5 سمبوسة؛ تشكيلة جاهزة للمشاركة.", "A sharing box with 5 kibbeh + 6 musakhan + 5 samosa. A selection to enjoy together."],
    "enbishan-menu-c434c397f9072844ba9c93b4": ["ورق عنب وملفوف في صحن واحد؛ جمعة الطعمين اللي تحبهم.", "Grape leaves and cabbage together: two favourites in one plate."],
    "enbishan-menu-cfe9ad0bccc96e0d0537be28": ["قرمشة ورق العنب مع لفّات الملفوف؛ تنويع لذيذ في كل صحن.", "Crispy grape leaves meet cabbage rolls for a delicious mix."],
    "enbishan-menu-d99fd6d6d3f908666e604235": ["صحن فتة مع موهيتو منعش؛ جمّع الطعم والانتعاش بطلب واحد.", "A fattah plate with a refreshing mojito. Flavour and refreshment together."],
    "enbishan-menu-d9c5375981f3b70a043b43c9": ["كبة ذهبية بقرمشة شهية؛ لقيمات تكمل جمعتكم.", "Golden, crisp kibbeh bites to share around the table."],
    "enbishan-menu-e739fd96698117d6fc9cd87d": ["بوكس يجمع 5 سمبوسة + 12 مسخن؛ تشكيلة جاهزة للمشاركة.", "A sharing box with 5 samosa + 12 musakhan. A selection to enjoy together."],
    "enbishan-menu-eea4f81998c7705a69c69532": ["تشكيلة محاشي تجمع أكثر من طعم في صحن واحد؛ لكل لقمة متعتها.", "A selection of stuffed vegetables in one plate. A different delight in every bite."],
    "enbishan-menu-f7a3a347bc71bf37d6645975": ["بوكس يجمع 5 كبة + 12 مسخن؛ تشكيلة جاهزة للمشاركة.", "A sharing box with 5 kibbeh + 12 musakhan. A selection to enjoy together."],
    "enbishan-menu-fda651bf4e57088442d6c972": ["محاشي مشكل وموهيتو منعش؛ تشكيلة لذيذة تكملها رشفة.", "Mixed stuffed vegetables with a refreshing mojito to complete your meal."],
    "enbishan-menu-fff314143ea1fb39265e203f": ["ورق عنب مع موهيتو منعش؛ لفّة لذيذة ورشفة تكمّلها.", "Grape leaves with a refreshing mojito. A delicious roll, a refreshing sip."],
    "enbishan-citrus-20261026": ["مشروب حمضيات منعش يكمّل وجبتك.", "A refreshing citrus drink to enjoy with your meal."],
    "enbishan-menu-2bafb30d47293596fcc41f07": ["بيبسي بارد يكمّل الطعم مع وجبتك.", "A cold Pepsi to enjoy with your meal."],
    "enbishan-menu-3a1075f078a64e58726b861e": ["سفن أب منعش؛ رشفة خفيفة مع اختياراتك.", "Refreshing 7Up to accompany your favourites."],
    "enbishan-menu-b3de6ed8adc01188db5d239b": ["ماء للشرب؛ انتعاش بسيط مع وجبتك.", "Drinking water for simple refreshment with your meal."],
  };
  function menuDescription(product) {
    const stored=sourceText(product,'description');
    if(stored||product.brand_id!=='enbishan')return stored||'';
    const copy=enbishanMenuCopy[product.id];
    return copy?entryText(...copy):'';
  }
  // Piece distributions transcribed from the approved workbook's contents column.
  function menuSizeContents(product,size) {
    if(product.brand_id!=='enbishan')return '';
    const sizes={
      'enbishan-menu-eea4f81998c7705a69c69532':{
        'v-8bc5ff94ef1484666fa0cfde':['٢ ورق عنب + ١ بطاطس + ١ فليفلة + ٢ بصل + ٢ كوسا + ٢ باذنجان','2 grape leaves + 1 potato + 1 pepper + 2 onions + 2 zucchini + 2 eggplants'],
        'v-7ec6c327c235a99f12ee1173':['٦ ورق عنب + ١ بطاطس + ١ فليفلة + ٤ بصل + ٤ كوسا + ٥ باذنجان','6 grape leaves + 1 potato + 1 pepper + 4 onions + 4 zucchini + 5 eggplants']
      },
      'enbishan-menu-b641eb7a3572b133717e64ef':{
        'v-3301de500e7478b4e03b3a73':['٥ ورق عنب + ١ بطاطس + ٢ فليفلة + ٢ بصل + ٢ كوسا + ١ باذنجان + ٣ ملفوف + ٢ كبة','5 grape leaves + 1 potato + 2 peppers + 2 onions + 2 zucchini + 1 eggplant + 3 cabbage rolls + 2 kibbeh']
      },
      'enbishan-menu-7b0752b4daa269674e08576f':{
        'v-b77b6cf14198f496f49c1d3c':['١٢ ورق عنب + ٢ بطاطس + ٢ فليفلة + ٤ بصل + ٥ كوسا + ٤ باذنجان + ٨ ملفوف + ٧ كبة','12 grape leaves + 2 potatoes + 2 peppers + 4 onions + 5 zucchini + 4 eggplants + 8 cabbage rolls + 7 kibbeh']
      }
    };
    const copy=sizes[product.id]?.[size?.code||size?.id];
    return copy?entryText(...copy):'';
  }
  function menuSizeLabel(product,size) {
    return [sourceText(size),menuSizeContents(product,size)].filter(Boolean).join(' — ');
  }
  function renderCategories() { const tabs = $('categories'); tabs.replaceChildren(); const categories = [...new Set(state.products.map((p) => menuSection(p)).filter(Boolean))];if(requestedBrandId==='enbishan')categories.sort((a,b)=>(enbishanSections.indexOf(a)<0?99:enbishanSections.indexOf(a))-(enbishanSections.indexOf(b)<0?99:enbishanSections.indexOf(b))); for (const category of ['', ...categories]) { const button = node('button','category-tab',(category?menuSectionLabel(category):t("full_menu"))); button.type='button'; button.setAttribute('aria-current', String(state.category === category)); button.addEventListener('click', () => { state.category = category; renderCategories(); renderProducts(); }); tabs.append(button); } }
  function renderFirstGift(container){
    const gift=state.quote?.first_order_gift;if(!gift||['inactive','not_applicable'].includes(gift.status))return;
    const card=node('section','notice first-order-gift');card.setAttribute('aria-live','polite');
    if(gift.status==='eligible'){
      card.append(node('strong','',entryText('هديتك مع هذا الطلب','Your gift with this order')));
      for(const item of gift.items||[])card.append(node('p','',sourceText(item)+' · '+entryText('مجانًا','Free')));
      card.append(node('small','',entryText('مرة واحدة لكل جوال موثق، مع استيفاء الحد الأدنى.','Once per verified mobile, with the approved minimum.')));
    }else{
      const copy={expired:['انتهى العرض.','This offer has ended.'],claimed:['سبق لك الاستفادة من هدية أول طلب.','You have already received your first-order gift.'],scheduled:['العرض لم يبدأ بعد.','The offer has not started yet.'],verification_required:['تحقق من جوالك لمعرفة استحقاق الهدية.','Verify your mobile to check gift eligibility.'],unavailable:['الهدية غير متاحة في هذا الفرع حاليًا.','The gift is currently unavailable at this branch.'],minimum_unconfigured:['العرض غير متاح لهذا الفرع حاليًا.','The offer is currently unavailable at this branch.'],minimum_not_met:['أكمل الحد الأدنى من الأصناف المدفوعة لاستحقاق الهدية.','Reach the paid-item minimum to qualify for the gift.']}[gift.status];
      if(!copy)return;card.append(node('p','',entryText(...copy)));
    }
    container.append(card);
  }
  function renderProducts() { const search = $('search').value.trim().toLocaleLowerCase(language); const products = state.products.filter((p) => (!state.category || menuSection(p) === state.category) && (!search || productSearchText(p).includes(search))); if(requestedBrandId==='enbishan')products.sort((a,b)=>menuRank(a,!state.category)-menuRank(b,!state.category)); const container = $('products'); container.replaceChildren(); text('menu-count', t("0_items_1",{"0":()=>(number.format(state.products.length)),"1":()=>(requestedBrandId==='kps'?t("images_and_options_from_the_kps_menu"):` · ${sourceText(state.brand)}`)})); if (!products.length) { const empty=node('div','empty-state'); empty.append(node('h3','', state.products.length?t("no_matching_items"):t("no_items_have_been_added_for_this_brand_yet")),node('p','', state.products.length?t("try_another_search_or_category"):t("items_approved_by_the_brand_will_appear_here"))); container.append(empty); return; }
    let previousSection=null;
    for (const product of products) { const section=menuSection(product);if(requestedBrandId==='enbishan'&&!state.category&&section!==previousSection){const heading=node('h3','menu-section-title',menuSectionLabel(section));container.append(heading);previousSection=section;} const card=node('article','product-card');card.dataset.productId=product.id; const imageButton=node('button','product-image-button'); imageButton.type='button'; imageButton.setAttribute('aria-label',t(groups(product).length?"details_of_0":"add_0_to_basket",{"0":()=>(sourceText(product))})); const cover=menuCover(product); const originalUrl=safeImage(cover.image_url); const imageUrl=originalUrl?.match(/^\/assets\/[A-Za-z0-9_.-]+\.(jpg|jpeg|png|webp)$/)?originalUrl.replace('/assets/','/media/previews/'):originalUrl?.startsWith('/api/product-assets/')?originalUrl+'?preview=true':originalUrl; if (imageUrl) { const img=node('img'); img.src=imageUrl; img.alt=sourceText(product);if(isKhaliya(product))img.classList.add('khaliya-owner-image'); img.loading=container.childElementCount<4?'eager':'lazy'; img.fetchPriority=container.childElementCount<2?'high':'auto'; img.decoding='async'; img.addEventListener('error', () => { if(img.src!==new URL(originalUrl,location.href).href){img.src=originalUrl;return;}img.remove(); imageButton.append(node('span','image-fallback',t("photo_currently_unavailable"))); }, {once:true}); imageButton.append(img); } else imageButton.append(node('span','image-fallback',t("no_product_photo"))); imageButton.addEventListener('click',()=>addFromCard(product)); const info=node('div','product-content'); const title=node('h3'); const titleButton=node('button','',sourceText(product)); titleButton.type='button'; titleButton.addEventListener('click',()=>openProduct(product)); title.append(titleButton); info.append(title); if(product.brand_id==='enbishan'){const description=menuDescription(product);if(description)info.append(node('p','enbishan-product-description',description));const detail=cover.label?entryText('الصورة: ','Pictured: ')+cover.label:'';if(detail)info.append(node('p','enbishan-size-summary',detail));} if (restricted(product)) info.append(node('span','pill warning',t("available_only_in_hafar_al_batin"))); if (groups(product).length) info.append(node('span','pill',isMug(product)?t("a_color_for_each_mug"):t("options_for_each_unit"))); const bottom=node('div','product-card-bottom'); const startingVariant=globalThis.KPSMenuVariants?.starting(product);if(startingVariant?.compare_at_halalas>product.price_halalas)bottom.append(node('del','muted',money(startingVariant.compare_at_halalas)));bottom.append(node('span','money',(product.brand_id==='enbishan'&&groups(product).find(g=>g.code==='SIZE')?.values?.length>1?entryText('يبدأ من ','From '):'')+money(product.price_halalas))); const add=node('button','product-add-button','+'); add.type='button'; add.setAttribute('aria-label',t("add_0_to_basket",{"0":()=>(sourceText(product))})); const eligibility=eligibleForProduct(product); add.disabled=!eligibility.ok; add.title=eligibility.reason || t("choose_quantity_and_options"); add.addEventListener('click',()=>openProduct(product)); const minus=node('button','product-minus-button','−');minus.type='button';minus.setAttribute('aria-label',t('decrease_quantity_of_0',{'0':()=>(sourceText(product))}));minus.addEventListener('click',()=>removeFromCard(product));const count=node('output','product-card-quantity',number.format(cardQuantity(product.id)));count.setAttribute('aria-live','polite');const controls=node('div','product-card-controls');controls.append(minus,count,add);bottom.append(controls); info.append(bottom); if(isKhaliya(product)&&state.service!=='delivery'&&branch()&&city(branch().city)===city('حفر الباطن')&&Number(branch().branch_no)!==2){info.append(node('p','small muted',language==='en'?'Khaliya is available for pickup at Branch 2 only.':'الخلية متوفرة للاستلام من فرع 2 فقط.'));const target=state.branches.find(b=>Number(b.branch_no)===2&&city(b.city)===city('حفر الباطن'));if(target){const transfer=node('button','button secondary',language==='en'?'Order from Branch 2':'الطلب من فرع 2');transfer.type='button';transfer.addEventListener('click',()=>changeBranch(target.id));info.append(transfer);}} if(imageUrl){const zoom=node('button','image-zoom-button',t("enlarge_photo"));zoom.type='button';zoom.setAttribute('aria-label',t("enlarge_photo_of_0",{"0":()=>(sourceText(product))}));zoom.addEventListener('click',()=>showImage({...product,image_url:cover.image_url}));info.append(zoom);}card.addEventListener('click',event=>{if(!event.target.closest('button,a,input,select,textarea'))addFromCard(product);});card.append(imageButton,info); container.append(card); }
    syncCardQuantities();
  }
  function cardQuantity(id) { return state.cart.filter(line=>line.productId===id).reduce((sum,line)=>sum+line.quantity,0); }
  function syncCardQuantities() {
    for(const card of $('products').querySelectorAll('.product-card')){
      const quantity=cardQuantity(card.dataset.productId);
      card.querySelector('.product-card-quantity').textContent=number.format(quantity);
      card.querySelector('.product-minus-button').disabled=quantity===0;
    }
  }
  function addFromCard(product) {
    // A tap is one local draft unit. Authoritative price, scope and availability
    // are checked again by the quote and order routes before confirmation.
    const current=state.products.find(item=>item.id===product.id);
    if(!current)return;
    if(groups(current).length){openProduct(current);return;}
    const eligibility=eligibleForProduct(current);
    if(!eligibility.ok){toast(eligibility.reason);return;}
    if(state.cart.reduce((sum,line)=>sum+line.quantity,0)>=100){toast(t('your_basket_can_contain_up_to_100_units'));return;}
    const row=state.cart.find(line=>line.productId===current.id&&!line.note&&!Object.keys(line.options||{}).length&&line.quantity<30);
    if(row)row.quantity++;
    else state.cart.push({productId:current.id,quantity:1,options:{},note:'',rowId:key()});
    cartChanged();
  }
  function removeFromCard(product) {
    const lines=state.cart.filter(line=>line.productId===product.id);
    // A product with multiple option/note lines needs an explicit line choice.
    // Show the existing basket instead of silently choosing a flavour to remove.
    if(lines.length>1){showBasket();return;}
    if(lines.length)updateQuantity(lines[0].rowId,-1);
  }
  function renderCart() { const container=$('cart-lines'); container.replaceChildren(); const count=state.cart.reduce((sum,line)=>sum+line.quantity,0); text('header-count',number.format(count)); text('basket-count',t("0_items_2",{"0":()=>(number.format(count))})); text('mobile-basket-count',t("your_basket_0_items",{"0":()=>(number.format(count))})); text('mobile-basket-total',money(state.quote?.total_halalas ?? draftSubtotal())); $('basket-details').hidden=!count;syncMobileBasket();
    if (!count) { const empty=node('div','empty-state'); empty.append(node('span','empty-symbol','＋'),node('h3','',t("start_with_your_favorite")),node('p','',t("items_you_add_will_appear_here"))); container.append(empty); }
    for (const line of state.cart) { const product=productFor(line); const row=node('div','cart-line'); if(product?.brand_id==='enbishan'){const selectedImage=(product.variant_images||[]).find(v=>Object.entries(v.options||{}).every(([code,value])=>line.options?.[code]===value));const url=safeImage(selectedImage?.image_url||product.image_url);if(url){const image=node('img','enbishan-cart-image');image.src=url;image.alt=cartLineName(product,line.options);image.loading='lazy';row.append(image);}} const title=node('div','cart-line-title'); title.append(node('span','',cartLineName(product,line.options)),node('span','money',money(linePrice(line)*line.quantity))); row.append(title); const label=optionLabel(product || {},line.options); if(label&&!isKhaliya(product||{})) row.append(node('small','',label)); if(line.restoredName)row.append(node('small','',t("previously_saved_item_0_type_and_quantity_retained",{"0":()=>(line.restoredName)}))); if(line.note) row.append(node('small','',t("note_0",{"0":()=>(line.note)}))); const eligible=eligibleForProduct(product || {}); if(!eligible.ok) row.append(node('small','status error',eligible.reason)); else if(!cartOptionsAvailable(line,product))row.append(node('small','status error',t('basket_option_unavailable'))); const controls=node('div','cart-line-controls'); const stepper=node('div','stepper'); const minus=node('button','','−'); minus.type='button'; minus.setAttribute('aria-label',t("decrease_quantity_of_0",{"0":()=>(sourceText(product))})); minus.addEventListener('click',()=>updateQuantity(line.rowId,-1)); const plus=node('button','','+'); plus.type='button'; plus.disabled=line.quantity>=30; plus.setAttribute('aria-label',t("increase_quantity_of_0",{"0":()=>(sourceText(product))})); plus.addEventListener('click',()=>updateQuantity(line.rowId,1)); stepper.append(minus,node('span','',number.format(line.quantity)),plus); const remove=node('button','remove-line',t("remove")); remove.type='button'; remove.setAttribute('aria-label',t("remove_0_1",{"0":()=>(sourceText(product)),"1":()=>(label?' '+label:'')})); remove.addEventListener('click',()=>{state.cart=state.cart.filter((item)=>item.rowId!==line.rowId); cartChanged();}); controls.append(stepper,remove); row.append(controls); container.append(row); }
    syncCardQuantities();renderQuote();
    renderEnbishanJourney();
  }
  function renderQuote() { const guest=$('basket-guest');if(guest)guest.hidden=!state.cart.length||!!state.session.user||state.phoneRequired!==false||!state.channels.guest?.available; const summary=$('quote-summary'); summary.replaceChildren(); const row=(label,amount,cls='')=>{const entry=node('div',`quote-row ${cls}`); entry.append(node('span','',label),node('span','money',money(amount))); summary.append(entry);}; row(t("items"),state.quote?.subtotal_halalas ?? draftSubtotal()); if(state.quote) {row(t("delivery_fee"),state.quote.delivery_fee_halalas || 0); if(Number.isInteger(state.quote.discount_halalas)&&state.quote.discount_halalas>0) row(t("discount"),-state.quote.discount_halalas); row(t("amount_due"),state.quote.total_halalas,'grand');} else {row(t("estimated_item_total"),draftSubtotal(),'grand');summary.append(node('p','small muted',state.quotePending?t("checking_availability_and_amount"):t("fees_and_the_final_amount_appear_after_branch_and_service_checks")));} renderFirstGift(summary);showError('basket-error',state.quoteError); $('checkout-button').disabled=!state.cart.length||state.quotePending||!state.quote||!catalogReady(); text('checkout-button',state.quotePending?t("checking"):!state.cart.length?t("add_items_to_your_basket"):!catalogReady()?t("review_unapproved_items_in_your_basket"):t("review_and_confirm_order")); if(requestedBrandId==='enbishan'){$('checkout-button').disabled=!state.cart.length||state.quotePending||!catalogReady();renderEnbishanJourney();} }
  function updateQuantity(id,delta) { const row=state.cart.find((line)=>line.rowId===id); if(!row) return; if(row.quantity+delta<1) state.cart=state.cart.filter((line)=>line.rowId!==id); else row.quantity=Math.min(30,row.quantity+delta); cartChanged(); }
  function cartChanged() { basketGeneration=key(); state.quote=null; persistDraft(); renderCart(); queueQuote(); queueNearestBranches(); }
  function orderBody(cart=state.cart,branchId=state.branchId) { const customer={name:$('customer-name').value.trim() || state.session.user?.display_name || ''};const contactPhone=$('customer-phone').value.trim()||state.session.user?.phone;if(contactPhone)customer.phone=contactPhone; if(state.service==='delivery') {if(state.location) customer.location=state.location; customer.address=$('address').value.trim();customer.neighborhood=$('neighborhood').value.trim();} if(state.service==='car') customer.car={model:$('car-model').value.trim(),color:$('car-color').value.trim(),plate:$('car-plate').value.trim()}; const body={brand_id:state.brand.id||'kps',branch_id:branchId,service:state.service,payment_method:(state.brand?.branch_online_required===true&&['pickup','car'].includes(state.service))?'card':(document.querySelector('input[name=payment]:checked')?.value||'cash'),items:cart.map((line)=>({product_id:line.productId,quantity:line.quantity,...((groups(productFor(line)||{}).length||Object.keys(line.options||{}).length)?{units:Array.from({length:line.quantity},()=>({options:line.options}))}:{}),note:line.note||''})),customer,notes:$('order-notes').value.trim()}; return window.KPSCustomerExtras?.augment(body)||body; }
  function queueQuote() { persistLocationDraft(); clearTimeout(queueQuote.timer); state.quote=null; state.quoteError=''; state.quotePending=false; state.quoteRevision++; renderQuote(); if(!state.cart.length) return; if(!$('customer-name').value.trim()&&!state.session.user?.display_name) {state.quoteError=t("enter_the_recipient_s_name_to_review_your_order");renderQuote();return;} if(!state.branchId) {state.quoteError=t("choose_a_branch_to_check_your_order");renderQuote();return;} if(state.service==='delivery'&&!state.location) {state.quoteError=mapLink.pending?entryText("جارٍ تحديد موقعك من الرابط…","Finding your location from the link…"):t("set_your_delivery_location_to_check_coverage_and_fees");renderQuote();return;} if(state.service==='delivery'&&!$('neighborhood').value.trim()){state.quoteError=t("enter_a_clear_delivery_neighborhood_name");renderQuote();return;} if(state.service==='car'&&!$('car-color').value.trim()){state.quoteError=t("enter_your_car_color");renderQuote();return;} queueQuote.timer=setTimeout(()=>refreshQuote(),350); }
  async function refreshQuote() { const revision=++state.quoteRevision; state.quotePending=true; state.quoteError=''; renderQuote(); try {const payload=orderBody();delete payload.notes;const result=await api(entryVerified()?'/api/gifts/quote':'/api/quote',{method:'POST',data:payload}); if(revision!==state.quoteRevision) return; state.quote=result.quote; if(!state.quote || !Number.isInteger(state.quote.total_halalas)) throw new Error(t("unable_to_verify_the_final_amount_try_again"));} catch(error) {if(revision!==state.quoteRevision)return;state.quote=null;state.quoteError=errorMessage(error);} finally {if(revision===state.quoteRevision){state.quotePending=false;renderQuote();}} }
  function openProduct(product, preserveSelection=false) {
    state.product=product;
    const optionFocus=preserveSelection?Array.from($('product-options').querySelectorAll('select')).indexOf(document.activeElement):-1;
    if(!preserveSelection){state.optionValues=[{}];$('product-qty').value='1';$('product-notes').value='';showError('product-error','');}
    else {
      for(const selected of state.optionValues)for(const code of Object.keys(selected)){
        const group=groups(product).find((entry)=>entry.code===code);
        if(!(group?.values||[]).some((value)=>(value.code||value.id)===selected[code]&&value.enabled!==false&&value.available!==false))delete selected[code];
      }
    }
    text('product-title',sourceText(product)); text('product-price',money(product.price_halalas)); const image=safeImage(menuCover(product).image_url); $('product-image').hidden=!image;$('product-image-zoom').hidden=!image; if(image){$('product-image').src=image;$('product-image').alt=sourceText(product);$('product-image').classList.toggle('khaliya-owner-image',isKhaliya(product));} $('product-description').hidden=!menuDescription(product); text('product-description',menuDescription(product)); $('product-restriction').hidden=!restricted(product); const eligibility=eligibleForProduct(product); $('product-add').disabled=!eligibility.ok; showError('product-availability',eligibility.reason); text('product-add',t("add_to_basket")); renderOptions();
    if(optionFocus>=0)$('product-options').querySelectorAll('select')[optionFocus]?.focus({preventScroll:true});
    openDialog('product-dialog');
  }
  function refreshProductDialog() {
    if(!$('product-dialog').open||!state.product)return;
    if(state.branchCatalogPending||state.branchCatalogId!==state.branchId){
      const eligibility=eligibleForProduct(state.product);$('product-add').disabled=true;showError('product-availability',eligibility.reason);return;
    }
    const product=state.products.find((entry)=>entry.id===state.product.id);
    openProduct(product||{...state.product,enabled:false,available:false},true);
  }
  function showSelectedProductImage() { if(!state.product)return;showImage({...state.product,image_url:$('product-image').getAttribute('src')||state.product.image_url}); }
  function renderVariantPrice(activeUnit=0){
    const product=state.product;if(!product)return;
    if(product.brand_id==='enbishan'){
      const options=state.optionValues[activeUnit]||state.optionValues[0]||{};
      const sizeGroup=groups(product).find(g=>g.code==='SIZE');
      const selectedSize=sizeGroup?.values?.find(v=>(v.code||v.id)===options.SIZE);
      const description=[menuDescription(product),selectedSize?menuSizeLabel(product,selectedSize):sizeGroup?.values?.map(v=>menuSizeLabel(product,v)).join(' • ')].filter(Boolean).join(' — ');
      text('product-description',description);$('product-description').hidden=!description;
      const variant=(product.variant_images||[]).find(entry=>Object.entries(entry.options||{}).every(([key,value])=>options[key]===value));
      const sizeImages=(product.variant_images||[]).filter(entry=>options.SIZE&&entry.options?.SIZE===options.SIZE);
      const sizeImage=sizeImages.length&&sizeImages.every(entry=>entry.image_url===sizeImages[0].image_url)?sizeImages[0].image_url:null;
      const image=safeImage(variant?.image_url||sizeImage||menuCover(product).image_url);
      if(image){$('product-image').src=image;$('product-image').hidden=false;}
    }
    if(!product.variant_price_matrix)return;
    const rows=state.optionValues.map(options=>globalThis.KPSMenuVariants?.selected(product,options));
    const complete=rows.every(Boolean);
    const total=complete?rows.reduce((sum,row)=>sum+row.price_halalas,0):product.price_halalas;
    const before=complete?rows.reduce((sum,row)=>sum+(row.compare_at_halalas||row.price_halalas),0):globalThis.KPSMenuVariants?.starting(product)?.compare_at_halalas;
    const price=$('product-price');price.replaceChildren();
    if(before>total){const old=node('del','',money(before));price.append(old,document.createTextNode(' '));}
    price.append(document.createTextNode(money(total)));
    price.dataset.variantPrice=String(total);
    const allChosen=state.optionValues.every(options=>groups(product).every(group=>options[group.code]));
    const invalid=allChosen&&!complete;
    $('product-add').disabled=!eligibleForProduct(product).ok||invalid;
    if(invalid)showError('product-error',t('variant_combination_unavailable'));
  }
  // Partial picks stay local; only a complete approved combination is sent to the API.
  const grapeFlavourDrafts=new WeakMap();
  function renderGrapeFlavours(product,group,index){
    if(requestedBrandId!=='enbishan'||group.code!=='FLAVOUR')return null;
    const pairs=(group.values||[]).filter(v=>v.code?.startsWith('pair-'));
    const mixed=(group.values||[]).find(v=>v.name==='مشكل');
    if(pairs.length!==3||!mixed)return null;
    const split=v=>String(v.name).split(' + ');
    const names=[...new Set(pairs.flatMap(split))];
    if(names.length!==3)return null;
    const options=state.optionValues[index];
    const selected=pairs.find(v=>v.code===options.FLAVOUR);
    let picks=selected?split(selected):options.FLAVOUR===mixed.code?[]:grapeFlavourDrafts.get(options)||[];
    const field=node('fieldset','field grape-flavour-field');
    field.append(node('legend','',entryText('نكهات ورق العنب','Grape leaf flavours')));
    const hint=node('p','small muted',entryText('اختر نكهتين من أول ثلاث، أو اختر مشكل.','Choose two of the first three flavours, or choose Mixed.'));
    hint.setAttribute('aria-live','polite');field.append(hint);
    const choices=node('div','customer-option-choices grape-flavour-choices');
    choices.setAttribute('role','group');choices.setAttribute('aria-label',`${entryText('نكهات ورق العنب','Grape leaf flavours')} ${number.format(index+1)}`);
    const enabled=v=>v.enabled!==false&&v.available!==false;
    const buttons=[];
    const draw=()=>{
      const isMixed=options.FLAVOUR===mixed.code;
      buttons.forEach((button,i)=>{
        const active=i===3?isMixed:!isMixed&&picks.includes(names[i]);
        button.setAttribute('aria-pressed',String(active));
        button.disabled=!eligibleForProduct(product).ok||(i===3?!enabled(mixed):!pairs.some(v=>enabled(v)&&split(v).includes(names[i]))||(!active&&picks.length===2));
      });
      hint.textContent=isMixed?entryText('تم اختيار مشكل.','Mixed selected.'):entryText(`اختر نكهتين من أول ثلاث، أو اختر مشكل. المحدد: ${number.format(picks.length)} من ${number.format(2)}`,`Choose two of the first three flavours, or choose Mixed. Selected: ${picks.length} of 2`);
    };
    [...names,mixed.name].forEach((name,i)=>{
      const pair=pairs.find(v=>split(v).includes(name));
      const english=i===3?'Mixed':String(pair.name_en).split(' + ')[split(pair).indexOf(name)];
      const button=node('button','button secondary',entryText(name,english));button.type='button';
      button.addEventListener('click',()=>{
        if(i===3){picks=[];if(options.FLAVOUR===mixed.code)delete options.FLAVOUR;else options.FLAVOUR=mixed.code;}
        else{
          if(options.FLAVOUR===mixed.code)picks=[];
          picks=picks.includes(name)?picks.filter(n=>n!==name):picks.length<2?[...picks,name]:picks;
          const match=pairs.find(v=>enabled(v)&&picks.length===2&&split(v).every(n=>picks.includes(n)));
          if(match)options.FLAVOUR=match.code;else delete options.FLAVOUR;
        }
        grapeFlavourDrafts.set(options,picks);draw();showError('product-error','');renderVariantPrice(index);
      });buttons.push(button);choices.append(button);
    });
    field.append(choices);draw();return field;
  }
  function renderOptions() { const product=state.product; if(!product)return; const quantity=Math.max(1,Math.min(30,Math.floor(Number($('product-qty').value)||1))); $('product-qty').value=String(quantity); while(state.optionValues.length<quantity)state.optionValues.push({}); state.optionValues.length=quantity; const allGroups=groups(product); for(const options of state.optionValues)for(const group of allGroups)if(fixedSingleSize(product,group))options[group.code]=group.values[0].code||group.values[0].id; const optionGroups=allGroups.filter(group=>!fixedSingleSize(product,group)); const container=$('product-options');container.replaceChildren(); $('product-option-hint').hidden=!optionGroups.length; text('product-option-hint',isMug(product)?t("choose_the_quantity_then_a_color_for_each_mug"):isKhaliya(product)?t("choose_a_type_for_each_unit_classic_or_pecan"):optionGroups.length?(requestedBrandId==='enbishan'&&!optionGroups.some(g=>g.code==='FLAVOUR')?entryText("حدد اختياراتك لكل وحدة قبل الإضافة.","Choose the options for each unit before adding."):t("choose_the_quantity_then_a_flavor_for_each_unit")):''); $('apply-first-option').hidden=optionGroups.length===0||quantity<2; text('apply-first-option',isMug(product)?t("use_the_first_mug_s_color_for_all"):isKhaliya(product)?t("use_the_first_unit_s_type_for_all"):(requestedBrandId==='enbishan'&&!optionGroups.some(g=>g.code==='FLAVOUR')?entryText("تطبيق اختيارات الوحدة الأولى على الكل","Use the first unit’s choices for all"):t("use_the_first_unit_s_flavor_for_all"))); for(let index=0;index<quantity&&optionGroups.length;index++){const unit=node('div','option-unit');unit.append(node('h3','',`${isMug(product)?t("mug"):t("unit")} ${number.format(index+1)}`));for(const group of optionGroups){if(requestedBrandId==='enbishan'&&group.code==='FLAVOUR'){const flavours=renderGrapeFlavours(product,group,index);if(flavours){unit.append(flavours);continue;}}const label=node('label','field',groupLabel(group));const select=node('select');select.setAttribute('aria-label',`${groupLabel(group)} ${number.format(index+1)}`); select.append(new Option(isKhaliya(product)?t("choose_a_type"):t("choose_0",{"0":()=>(isMug(product)?t("color"):groupLabel(group))}),''));for(const value of group.values||[]){const available=(state.branchCatalogPending&&state.branchCatalogId!==state.branchId||eligibleForProduct(product).ok)&&value.enabled!==false&&value.available!==false;const repeated=requestedBrandId==='enbishan'&&group.code.startsWith('CHOICE_')&&Object.entries(state.optionValues[index]).some(([code,selected])=>code!==group.code&&code.startsWith('CHOICE_')&&selected===(value.code||value.id));if(repeated)continue;const option=new Option(String(sourceText(value))+(available?'':String(t('unavailable_option'))),value.code||value.id);option.disabled=!available;select.append(option);}select.value=state.optionValues[index][group.code]||'';select.required=group.required!==false;select.addEventListener('change',()=>{state.optionValues[index][group.code]=select.value;if(group.code.startsWith('CHOICE_')){renderOptions();return;}showError('product-error','');renderVariantPrice(index);});label.append(select);
      if(requestedBrandId==='enbishan'){
        select.hidden=true;
        const choices=node('div','customer-option-choices');choices.setAttribute('role','group');choices.setAttribute('aria-label',select.getAttribute('aria-label'));
        for(const option of Array.from(select.options).filter(option=>option.value)){
          const button=node('button','button secondary',option.textContent);button.type='button';button.disabled=option.disabled;
          if(group.code==='SIZE'){const prices=(product.variant_price_matrix||[]).filter(row=>row.options?.SIZE===option.value);if(prices.length){const amount=Math.min(...prices.map(row=>row.price_halalas));const old=prices.find(row=>row.price_halalas===amount)?.compare_at_halalas;const price=node('span','enbishan-size-price');if(old>amount)price.append(node('del','',money(old)),document.createTextNode(' '));price.append(document.createTextNode(money(amount)));button.append(price);}}
          button.setAttribute('aria-pressed',String(select.value===option.value));
          button.addEventListener('click',()=>{select.value=option.value;state.optionValues[index][group.code]=option.value;if(group.code.startsWith('CHOICE_')){renderOptions();return;}choices.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));showError('product-error','');renderVariantPrice(index);});choices.append(button);
        }
        label.append(choices);
      }
      unit.append(label);}container.append(unit);} $('product-minus').disabled=quantity<=1;$('product-plus').disabled=quantity>=30;renderVariantPrice(); }
  async function addProduct() { const product=state.product;const eligibility=eligibleForProduct(product);if(!eligibility.ok){showError('product-error',eligibility.reason);return;}const quantity=Number($('product-qty').value);if(state.cart.reduce((sum,line)=>sum+line.quantity,0)+quantity>100){showError('product-error',t("your_basket_can_contain_up_to_100_units"));return;}if(state.branchId){try{const result=await api(`/api/products/${encodeURIComponent(product.id)}/availability?branch_id=${encodeURIComponent(state.branchId)}&service=${encodeURIComponent(state.service)}`);if(result.available!==true){showError('product-error',serverReason(result,'this_item_is_unavailable_at_this_branch'));return;}}catch(error){showError('product-error',errorMessage(error));return;}}const optionGroups=groups(product);for(let index=0;index<quantity;index++)for(const group of optionGroups){const selected=state.optionValues[index]?.[group.code];if(group.required!==false&&!(group.values||[]).some((v)=>(v.code||v.id)===selected&&v.enabled!==false&&v.available!==false)){showError('product-error',t("0_1_before_adding",{"0":()=>(isMug(product)?t("choose_a_color_for_mug"):isKhaliya(product)?t("choose_a_type_for_unit"):(requestedBrandId==='enbishan'&&!optionGroups.some(g=>g.code==='FLAVOUR')?entryText("أكمل اختيارات الوحدة","Complete the choices for unit"):t("choose_a_flavor_for_unit"))),"1":()=>(number.format(index+1))}));$('product-options').querySelectorAll('.option-unit')[index]?.querySelector('select')?.focus();return;}}const note=$('product-notes').value.trim();const proposed=state.cart.map((line)=>({...line}));for(let index=0;index<quantity;index++){const options=optionGroups.length?{...state.optionValues[index]}:{};const signature=JSON.stringify(options);const current=proposed.find((line)=>line.productId===product.id&&JSON.stringify(line.options)===signature&&line.note===note);if(current&&current.quantity<30)current.quantity++;else proposed.push({productId:product.id,quantity:1,options,note,rowId:key()});} if(catalogReady()&&state.branchId&&($('customer-name').value.trim()||state.session.user?.display_name)&&(state.service!=='delivery'||state.location&&$('neighborhood').value.trim())&&(state.service!=='car'||$('car-color').value.trim())){try{const payload=orderBody(proposed);delete payload.notes;await api('/api/quote',{method:'POST',data:payload});}catch(error){if(error.code!=='MINIMUM_ORDER_NOT_MET'){showError('product-error',errorMessage(error));return;}}}state.cart=proposed;cartChanged();closeDialog('product-dialog');toast(t("added_0_to_basket",{"0":()=>(sourceText(product))})); }
  function confirmAction({title, content, label, action, cancel}) { state.confirmation={action,cancel}; text('confirm-title',title);$('confirm-content').replaceChildren(content);showError('confirm-error','');text('confirm-submit',label);$('confirm-submit').disabled=false;openDialog('confirm-dialog'); }
  function changeBranch(newId) { if(newId===state.branchId)return;const previous=state.branchId;const target=state.branches.find((b)=>b.id===newId);const invalid=state.cart.filter((line)=>{const p=productFor(line);return restricted(p||{})&&(!target||!p.available_cities.some((c)=>city(c)===city(target.city)));});if(invalid.length){const content=node('div');content.append(node('p','',t("changing_branches_will_make_these_items_unavailable_we_will_not_r")));const list=node('ul');invalid.forEach((line)=>list.append(node('li','',`${sourceText(productFor(line))} × ${number.format(line.quantity)}`)));content.append(list,node('p','small muted',t("close_this_window_to_keep_your_current_branch_and_basket")));$('branch-select').value=previous;confirmAction({title:t("review_branch_change"),content,label:t("remove_unavailable_items_and_change_branch"),action:async()=>{const ids=new Set(invalid.map((i)=>i.rowId));state.cart=state.cart.filter((line)=>!ids.has(line.rowId));state.branchId=newId;renderBranches();renderService();renderProducts();cartChanged();loadBranchCatalog();loadBanners();closeDialog('confirm-dialog');}});return;}state.branchId=newId;renderBranches();renderService();renderNearestBranches();renderProducts();queueQuote();loadBranchCatalog();loadBanners(); }
  async function beginCheckout() { state.identityIntent='checkout'; if(state.session.user&&state.session.user.brand_id!==requestedBrandId){requireCustomer(beginCheckout);return;}if(await recoverCheckout())return;if(!catalogReady()){showError('basket-error',t("review_items_whose_prices_or_options_have_changed_before_confirmi"));return;}if(!state.session.user||(state.phoneRequired!==false&&!state.session.customer_verified)){state.afterLogin=beginCheckout;renderAccount();showError('account-error','');openDialog('account-dialog');return;}if(state.session.user.role!=='customer'){showError('basket-error',t("a_customer_or_guest_session_is_required_to_confirm_an_order_staff"));return;}if(!state.session.csrf_token){state.afterLogin=beginCheckout;showError('account-error',t("sign_in_again_for_secure_verification"));openDialog('account-dialog');return;}await refreshQuote();if(!state.quote)return;const payload=orderBody();const quoted=state.quote;const content=node('div');content.append(node('h3','',branch()?.name||''),node('p','muted',t('service_payment_summary',{service:()=>serviceName(state.service),payment:()=>state.brand?.branch_online_required===true&&['pickup','car'].includes(state.service)?entryText('الدفع الإلكتروني بعد موافقة الكاشير','Online payment after cashier approval'):payload.payment_method==='split'?t('cash_and_card'):payload.payment_method==='cash'?t('cash'):t('card_on_arrival')})));const list=node('ul','order-items');state.cart.forEach((line)=>{const p=productFor(line);const li=node('li','',`${cartLineName(p,line.options)} × ${number.format(line.quantity)}${optionLabel(p||{},line.options)?' · '+optionLabel(p||{},line.options):''}`);list.append(li);});content.append(list);renderFirstGift(content);if(state.service==='delivery')content.append(node('p','small',payload.customer.address||t("selected_delivery_location")));if(state.service==='car')content.append(node('p','small',`${payload.customer.car.model} · ${payload.customer.car.color} · ${payload.customer.car.plate||entryText("لم يُدخل رقم اللوحة","No plate entered")}`));const total=node('div','quote-row grand');total.append(node('strong','',t("amount_due")),node('strong','money',money(quoted.total_halalas)));content.append(total,node('p','small muted',state.brand?.branch_online_required===true&&['pickup','car'].includes(state.service)?entryText("سيُحفظ الطلب بعد التأكيد. تتاح خطوة الدفع الإلكتروني بعد موافقة الكاشير.","Your order is saved after confirmation. Online payment becomes available after cashier approval."):t("one_order_will_be_saved_after_server_confirmation_payment_is_coll")));payload.expected_total_halalas=quoted.total_halalas;if(quoted.first_order_gift){payload.expected_gift=quoted.first_order_gift.status==='eligible';payload.expected_gift_version=quoted.first_order_gift.version;}confirmAction({title:t("review_order"),content,label:t("confirm_order"),action:()=>submitCheckout(payload)}); }
  async function requestOTP(event) { event.preventDefault();showError('account-error','');const payload={phone:$('phone').value.trim(),brand_id:state.brand.id||'kps',lang:language,purpose:state.changingPhone?'change_phone':'login'};await busy($('send-otp'),async()=>{try{const result=await api('/api/auth/otp/request',{method:'POST',data:payload});if(!result.challenge_id)throw new Error(t("the_verification_provider_did_not_confirm_sending_the_code"));const digits=result.code_length===undefined?4:result.code_length;if(!Number.isInteger(digits)||digits<4||digits>10)throw new Error(t("the_verification_provider_did_not_confirm_sending_the_code"));$('otp-code').minLength=digits;$('otp-code').maxLength=digits;$('otp-code').pattern='[0-9]{'+digits+'}';state.challenge=result.challenge_id;$('otp-request-form').hidden=true;$('otp-verify-form').hidden=false;$('otp-unavailable').hidden=true;const masked=payload.phone.slice(-4);text('otp-info',t("a_verification_code_was_sent_to_the_number_ending_in_0_1",{"0":()=>(masked),"1":()=>(result.channel==='whatsapp'?t("via_whatsapp"):t("via_sms"))}));$('otp-code').value='';$('otp-code').focus();}catch(error){showError('account-error',errorMessage(error));$('otp-unavailable').hidden=error.code!=='OTP_PROVIDER_UNAVAILABLE';}}); }
  async function verifyOTP(event) { event.preventDefault();showError('account-error','');await busy($('verify-otp'),async()=>{try{const result=await api('/api/auth/otp/verify',{method:'POST',data:{challenge_id:state.challenge,code:$('otp-code').value.trim(),display_name:$('otp-name').value.trim()}});if(!result.user||!result.csrf_token)throw new Error(t("a_secure_session_was_not_created_try_again"));state.session=result;persistLocationDraft();state.challenge=null;state.changingPhone=false;$('customer-phone').value=result.user.phone;$('customer-name').value=result.user.display_name;renderAccount();closeDialog('account-dialog');toast(t("phone_verified"));const callback=state.afterLogin;state.afterLogin=null;if(callback)await callback();}catch(error){showError('account-error',errorMessage(error));}}); }
  async function openOrders(orderId) { if(state.session.user&&state.session.user.brand_id!==requestedBrandId){requireCustomer(()=>openOrders(orderId));return;}if(!state.session.user){state.afterLogin=()=>openOrders(orderId);renderAccount();showError('account-error','');openDialog('account-dialog');return;}if(state.session.user.role!=='customer'){toast(t("customer_orders_are_available_to_customer_accounts_staff_access_i"));return;}openDialog('orders-dialog');showError('orders-error','');$('order-detail').hidden=true;$('orders-list').hidden=false;$('orders-list').replaceChildren(node('div','empty-state',t("loading_your_orders")));try{const result=await api(orderListUrl());state.orders=result.orders||[];state.nextOrdersOffset=result.next_offset;renderOrders(state.orders);scheduleOrdersPoll();if(orderId)await showOrder(orderId);}catch(error){showError('orders-error',errorMessage(error));$('orders-list').replaceChildren();} }
  function orderBranchLabel(order) { const saved=order.snapshot||{};const current=state.branches.find((b)=>b.id===order.branch_id);const no=saved.branch_no||order.branch_no||current?.branch_no;return `${no?`${no} · `:''}${saved.branch_name||order.branch_name||current?.name||t("fulfillment_branch")}`; }
  function conversationOrderLabel(conversation) { const order=state.orders.find((o)=>o.id===conversation.order_id)||state.contactOrders.find((o)=>o.id===conversation.order_id);return conversation.order_reference||order?.snapshot?.order_reference||conversation.order_no||order?.order_no||order?.number||conversation.order_id; }
  function renderOrders(orders) { $('order-filters').hidden=false;document.querySelectorAll('[data-order-filter]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.orderFilter===(state.orderFilter||'active')))); const container=$('orders-list');container.replaceChildren();if(!orders.length){container.append(node('div','empty-state',t("no_orders_in_filter")));return;}for(const order of orders){const card=node('article','order-card');card.append(node('h3','',t("order_0",{"0":()=>(order.snapshot?.order_reference||order.number||order.order_no||order.id)})));const summary=node('div','row wrap');summary.append(node('span','pill',serviceName(order.service)),node('span','pill',customerProgressLabel(order)),node('strong','money',complimentaryName(order)||money(order.total_halalas||order.snapshot?.total_halalas)));card.append(summary,node('p','small',orderBranchLabel(order)),node('p','small muted',dateTime(order.created_at)));const button=node('button','button secondary',t("view_order_and_conversation"));button.type='button';button.addEventListener('click',()=>showOrder(order.id));const again=node('button','button quiet',t('reorder'));again.type='button';again.dataset.reorder=order.id;again.addEventListener('click',()=>busy(again,()=>prepareReorder(order.id)));const actions=node('div','row wrap');actions.append(button,again);card.append(actions);container.append(card);} if(state.nextOrdersOffset!=null){const more=node('button','button secondary full',t("load_older_orders"));more.type='button';more.addEventListener('click',()=>busy(more,async()=>{try{const result=await api(orderListUrl(state.nextOrdersOffset));state.orders.push(...(result.orders||[]).filter((o)=>!state.orders.some((existing)=>existing.id===o.id)));state.nextOrdersOffset=result.next_offset;renderOrders(state.orders);}catch(error){showError('orders-error',errorMessage(error));}}));container.append(more);} }
  function scheduleOrdersPoll(){clearTimeout(state.ordersTimer);if(!$('orders-dialog').open)return;state.ordersTimer=setTimeout(async()=>{if(!$('orders-dialog').open)return;try{if(!document.hidden){const scroll=$('orders-dialog').querySelector('.dialog-scroll').scrollTop;if(state.activeOrder)await showOrder(state.activeOrder.id,true);else{const result=await api(orderListUrl());const recent=result.orders||[];const older=state.orders.filter((order)=>!recent.some((r)=>r.id===order.id));state.orders=[...recent,...older];renderOrders(state.orders);}$('orders-dialog').querySelector('.dialog-scroll').scrollTop=scroll;}}catch(error){showError('orders-error',errorMessage(error));}finally{scheduleOrdersPoll();}},8000);}
  async function showOrder(id,quiet=false) { showError('orders-error','');try{const result=await api(`/api/orders/${encodeURIComponent(id)}`);state.activeOrder=result.order;state.activeOrderEvents=result.events||[];renderOrderDetail(result.order,state.activeOrderEvents);if(!quiet){$('order-filters').hidden=true;$('orders-list').hidden=true;$('order-detail').hidden=false;}}catch(error){showError('orders-error',errorMessage(error));} }
  function renderOrderDetail(order,events) { if(!order)return;const container=$('order-detail');container.replaceChildren();const back=node('button','button quiet',t("back_to_my_orders"));back.type='button';back.addEventListener('click',()=>{state.activeOrder=null;openOrders();});container.append(back,node('h3','',t("order_0",{"0":()=>(order.snapshot?.order_reference||order.number||order.order_no||order.id)})),node('p','small muted',dateTime(order.created_at)));container.append(renderCustomerProgress(order));const statuses=node('div','order-status-grid');const payment=node('div');payment.append(node('span','',t('payment')),node('b','',statusName(order.payment_status)));statuses.append(payment);container.append(statuses);const snapshot=order.snapshot||{};const orderBranch=state.branches.find((b)=>b.id===order.branch_id);container.append(node('p','small',`${orderBranchLabel(order)} · ${serviceName(order.service)}`));const list=node('ul','order-items');for(const item of order.items||snapshot.items||[]){const line=node('li');line.append(node('strong','',`${item.name||item.product_name||t("item")} × ${number.format(item.quantity||item.qty||1)}`));if(item.note)line.append(node('p','small muted',item.note));for(const unit of item.units||[]){const label=unit.option_label||unit.options_label||Object.values(unit.option_snapshot||{}).map((g)=>`${groupLabel(g)}: ${(g.choices||[]).map((v)=>sourceText(v)).join(t("separator"))}`).join(' · ');if(label)line.append(node('p','small muted',label));}list.append(line);}container.append(list);if(snapshot.notes)container.append(node('p','small muted',t("order_notes_0",{"0":()=>(snapshot.notes)})));const total=node('div','quote-row grand');total.append(node('strong','',complimentaryName(order)?complimentaryName(order):t("saved_order_amount")),node('strong','money',complimentaryName(order)||money(order.total_halalas??snapshot.total_halalas)));container.append(total);const actions=node('div','row wrap');const chat=node('button','button primary',t("order_conversation"));chat.type='button';chat.addEventListener('click',()=>openChat(order));actions.append(chat);const again=node('button','button secondary',t('reorder'));again.type='button';again.addEventListener('click',()=>busy(again,()=>prepareReorder(order.id)));actions.append(again);if(order.service==='car'&&!['completed','delivered','cancelled','rejected'].includes(order.status||order.preparation_status)&&!order.arrival_at){const arrived=node('button','button secondary',t("i_have_arrived_at_the_branch"));arrived.type='button';arrived.addEventListener('click',()=>busy(arrived,async()=>{try{const payload={version:order.version};const idem=stableKey(`arrival-${order.id}`,payload);await api(`/api/orders/${encodeURIComponent(order.id)}/arrival`,{method:'POST',data:payload,idempotency:idem.value});releaseKey(idem);toast(t("your_arrival_notification_has_been_saved"));await showOrder(order.id,true);}catch(error){showError('orders-error',errorMessage(error));}}));actions.append(arrived);}const amend=node('button','button secondary',document.documentElement.lang==='en'?'Change or cancel order':'تعديل أو إلغاء الطلب');amend.type='button';amend.addEventListener('click',()=>window.KPSAmendments.open({oid:order.id,api,mutation:async(path,payload)=>{const idem=stableKey('amend-'+path,payload);const result=await api(path,{method:'POST',data:payload,idempotency:idem.value});releaseKey(idem);return result;},user:{role:'customer'},onChanged:()=>showOrder(order.id,true)}));actions.append(amend);container.append(actions);if(events.length){const details=node('details');details.append(node('summary','',t("order_status_history")));const history=node('ul','order-items');events.forEach((event)=>history.append(node('li','small',`${dateTime(event.created_at)} · ${event.message||event.label||statusName(event.status||String(event.event_type||'').split('.').at(-1))}`)));details.append(history);container.append(details);} }
  function requireCustomer(callback) {
    state.identityIntent='contact';
    if(!state.session.user||!state.session.csrf_token){state.afterLogin=callback;renderAccount();showError('account-error','');openDialog('account-dialog');return false;}
    if(state.session.user.brand_id!==requestedBrandId){state.afterLogin=callback;renderAccount();showError('account-error',t("your_session_belongs_to_another_brand_sign_out_before_signing_in_"));openDialog('account-dialog');return false;}
    if(state.session.user.role!=='customer'){toast(t("guest_and_customer_conversations_are_available_here_staff_access_"));return false;}
    return true;
  }
  async function openContacts() {
    if(!requireCustomer(openContacts))return;
    closeDialog('chat-dialog');closeDialog('orders-dialog');openDialog('contacts-dialog');showError('contacts-error','');
    $('contacts-list').replaceChildren(node('div','empty-state',t("loading_your_conversations")));
    const select=$('contact-branch');select.replaceChildren(new Option(t("choose_a_branch"),''));state.branches.forEach((b)=>select.append(new Option(`${b.branch_no} · ${sourceText(b)}`,b.id)));select.value=state.branchId;
    try{$('contact-details').value=sessionStorage.getItem(contactDraftKey())||'';}catch{}$('contact-service').value=state.service;$('contact-order').replaceChildren(new Option(t("conversation_without_an_order"),''));updateContactContext();
    const results=await Promise.allSettled([api('/api/conversations'),api('/api/orders?limit=30')]);
    if(!$('contacts-dialog').open)return;
    if(results[0].status==='fulfilled'){state.conversations=results[0].value.conversations||[];state.nextConversationOffset=state.conversations.length===50?50:null;renderContacts();}
    else{$('contacts-list').replaceChildren(node('p','small muted',t("unable_to_load_saved_conversations")));showError('contacts-error',errorMessage(results[0].reason));}
    if(results[1].status==='fulfilled'){state.contactOrders=results[1].value.orders||[];state.contactOrders.forEach((order)=>$('contact-order').append(new Option(t("order_0_1",{"0":()=>(order.snapshot?.order_reference||order.number||order.order_no||order.id),"1":()=>(serviceName(order.service))}),order.id)));}
    else{state.contactOrders=[];showError('contacts-error',errorMessage(results[1].reason));}
  }
  function renderContacts() {
    const list=$('contacts-list');list.replaceChildren();
    if(!state.conversations.length)list.append(node('p','small muted',t("no_conversations_saved_in_your_account_yet")));
    state.conversations.forEach((conversation)=>{const card=node('article','order-card');const label=conversation.reference|| (conversation.order_id?t("order_conversation_0",{"0":()=>(conversationOrderLabel(conversation))}):t("conversation_without_an_order"));card.append(node('h3','',label),node('p','small muted',`${conversation.branch_name||state.branches.find((b)=>b.id===conversation.branch_id)?.name||t("relevant_branch")} · ${serviceName(conversation.service||conversation.context_service||'')}`));const summary=node('div','row wrap');summary.append(node('span','pill',({open:t("open"),in_progress:t("processing"),resolved:t("resolved")}[conversation.status]||t("open"))));if(conversation.unread_count>0)summary.append(node('span','pill warning',t("0_new_messages",{"0":()=>(number.format(conversation.unread_count))})));card.append(summary);const button=node('button','button secondary',t("open_conversation"));button.type='button';button.dataset.conversationId=conversation.id;button.addEventListener('click',()=>showConversation(conversation));card.append(button);list.append(card);});
    if(state.nextConversationOffset!==null){const more=node('button','button secondary full',t("load_older_conversations"));more.type='button';more.addEventListener('click',()=>busy(more,async()=>{try{const result=await api(`/api/conversations?offset=${state.nextConversationOffset}`);const rows=result.conversations||[];state.conversations.push(...rows.filter((row)=>!state.conversations.some((entry)=>entry.id===row.id)));state.nextConversationOffset=rows.length===50?state.nextConversationOffset+50:null;renderContacts();}catch(error){showError('contacts-error',errorMessage(error));}}));list.append(more);}
  }
  function updateContactContext() { $('contact-details').required=$('contact-category').value==='other';const linked=Boolean($('contact-order').value)||['privacy','refund'].includes($('contact-category').value);$('contact-context-fields').hidden=linked;$('contact-branch').required=!linked;$('contact-service').required=!linked; }
  async function startContact(event) {
    event.preventDefault();if(!requireCustomer(openContacts))return;
    const ownOrder=state.contactOrders.find((order)=>order.id===$('contact-order').value);
    const payload={...(ownOrder?{order_id:ownOrder.id}:{branch_id:$('contact-branch').value||null,service:$('contact-service').value}),category:$('contact-category').value,details:$('contact-details').value.trim()};
    if(!ownOrder&&!payload.branch_id&&!['privacy','refund'].includes(payload.category)){showError('contacts-error',t("choose_a_branch_and_service_to_start_the_conversation"));return;}
    await busy($('contact-start'),async()=>{try{const idem=stableKey('start-conversation',payload);const result=await api('/api/conversations',{method:'POST',data:payload,idempotency:idem.value});const conversation=result.conversation||result;if(!conversation.id)throw new Error(t("the_server_did_not_confirm_opening_the_conversation"));releaseKey(idem);try{sessionStorage.removeItem(contactDraftKey());}catch{}$('contact-details').value='';await showConversation({...conversation,order_no:ownOrder?.order_no||ownOrder?.number,service:ownOrder?.service||payload.service});}catch(error){showError('contacts-error',errorMessage(error));}});
  }
  async function showConversation(conversation) {
    if(!requireCustomer(openContacts))return;
    clearTimeout(state.chatTimer);state.conversation=conversation;state.chatSequence=0;
    text('chat-title',conversation.reference|| (conversation.order_id?t("order_conversation_0",{"0":()=>(conversationOrderLabel(conversation))}):t("conversation_without_an_order")));text('chat-route',t('contact_'+(conversation.category||'help')));
    $('chat-messages').replaceChildren();try{$('chat-text').value=sessionStorage.getItem(chatDraftKey())||'';}catch{$('chat-text').value='';}clearChatImage();showError('chat-error','');
    closeDialog('orders-dialog');closeDialog('contacts-dialog');openDialog('chat-dialog');await pollChat();
  }
  async function openChat(order) {
    if(!requireCustomer(()=>openChat(order)))return;
    const payload={order_id:order.id};const idem=stableKey('start-conversation',payload);
    try{const result=await api('/api/conversations',{method:'POST',data:payload,idempotency:idem.value});const conversation=result.conversation||result;if(!conversation.id)throw new Error(t("unable_to_open_the_order_conversation"));releaseKey(idem);await showConversation({...conversation,order_no:order.order_no||order.number,service:order.service});}
    catch(error){showError('orders-error',errorMessage(error));}
  }
  async function pollChat() { if(!$('chat-dialog').open||!state.conversation?.id)return;const id=state.conversation.id;try{const result=await api(`/api/conversations/${encodeURIComponent(id)}/messages?after=${state.chatSequence}`);if(state.conversation?.id!==id)return;const container=$('chat-messages');let tickets=document.getElementById('chat-ticket-status');if(!tickets){tickets=node('div','order-status-grid');tickets.id='chat-ticket-status';container.before(tickets);}tickets.replaceChildren();for(const ticket of result.tickets||[]){const card=node('div');card.append(node('b','',ticket.reference),node('span','',language==='en'?ticket.status.replaceAll('_',' '):ticket.status_label));tickets.append(card);}const nearBottom=container.scrollHeight-container.scrollTop-container.clientHeight<80;if((result.messages||[]).length)container.querySelector('.chat-empty')?.remove();for(const message of result.messages||[]){if(message.seq<=state.chatSequence)continue;const bubble=node('div',`chat-bubble${message.sender_id===state.session.user?.id?' mine':''}`);if(message.text)bubble.append(node('p','',message.text));if(message.attachment_id){const url=safeImage(message.attachment_url||message.attachment?.url||`/api/chat-attachments/${encodeURIComponent(message.attachment_id)}?context=customer`);if(url){const img=node('img');img.src=url;img.alt=t("photo_attached_to_conversation");img.loading='lazy';const link=node('a');link.href=url;link.target='_blank';link.rel='noopener';link.append(img);bubble.append(link);}}const timestamp=node('small','',dateTime(message.created_at));timestamp.dataset.datetime=message.created_at;bubble.append(timestamp);container.append(bubble);state.chatSequence=Math.max(state.chatSequence,message.seq||0);}if(!container.children.length)container.append(node('p','small muted chat-empty',t("start_your_message_here_your_conversation_remains_saved_in_your_a")));if(nearBottom)container.scrollTop=container.scrollHeight;if(state.chatSequence>0)await api(`/api/conversations/${encodeURIComponent(id)}/read`,{method:'POST',data:{through_seq:state.chatSequence}});showError('chat-error','');}catch(error){showError('chat-error',errorMessage(error));}finally{clearTimeout(state.chatTimer);if($('chat-dialog').open&&state.conversation?.id===id)state.chatTimer=setTimeout(pollChat,5000);} }
  let chatSelectedImage=null,chatPreviewUrl=null;
  function clearChatImage(){if(chatPreviewUrl)URL.revokeObjectURL(chatPreviewUrl);chatPreviewUrl=null;chatSelectedImage=null;$('chat-image').value='';$('chat-camera').value='';text('chat-image-name','');$('chat-image-preview').hidden=true;$('chat-image-preview').querySelector('img').removeAttribute('src');}
  function selectChatImage(file){if(!file)return;if(!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>5*1024*1024){showError('chat-error',t("choose_a_jpg_png_or_webp_image_up_to_5_mb"));return;}clearChatImage();chatSelectedImage=file;chatPreviewUrl=URL.createObjectURL(file);text('chat-image-name',file.name);$('chat-image-preview').querySelector('img').src=chatPreviewUrl;$('chat-image-preview').hidden=false;showError('chat-error','');}
  const readFile = (file) => new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error(t("unable_to_read_the_photo")));reader.readAsDataURL(file);});
  async function sendMessage(event) {event.preventDefault();if(!state.conversation?.id){showError('chat-error',t("wait_for_the_conversation_to_open_before_sending"));return;}const message=$('chat-text').value.trim();const file=chatSelectedImage;if(!message&&!file){showError('chat-error',t("add_a_message_or_photo_before_sending"));return;}if(file&&(!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>5*1024*1024)){showError('chat-error',t("choose_a_jpg_png_or_webp_image_up_to_5_mb"));return;}await busy($('send-message'),async()=>{try{let attachmentId=null;if(file){const upload={data_base64:await readFile(file),content_type:file.type};const uploadKey=stableKey(`chat-upload-${state.conversation.id}`,{name:file.name,size:file.size,lastModified:file.lastModified});const uploaded=await api(`/api/conversations/${encodeURIComponent(state.conversation.id)}/attachments`,{method:'POST',data:upload,idempotency:uploadKey.value});attachmentId=uploaded.id||uploaded.attachment?.id;if(!attachmentId)throw new Error(t("the_server_did_not_confirm_the_photo_was_saved"));}const payload={text:message,attachment_id:attachmentId};const idem=stableKey(`chat-message-${state.conversation.id}`,payload);await api(`/api/conversations/${encodeURIComponent(state.conversation.id)}/messages`,{method:'POST',data:payload,idempotency:idem.value});releaseKey(idem);try{sessionStorage.removeItem(chatDraftKey());}catch{}$('chat-text').value='';clearChatImage();showError('chat-error','');await pollChat();}catch(error){showError('chat-error',errorMessage(error));}});}
  async function loadBranchCatalog() {
    const selected=state.branchId,selectedService=state.service,revision=++state.branchCatalogRevision;
    state.branchCatalogId=null;state.branchCatalogPending=true;
    renderProducts();refreshProductDialog();
    const params=new URLSearchParams({brand_id:state.brand.id||'kps',service:selectedService});
    if(selected)params.set('branch_id',selected);
    try {
      const result=await api(`/api/catalog?${params}`);
      if(state.branchId!==selected||state.service!==selectedService||state.branchCatalogRevision!==revision)return;
      state.products=result.products||[];state.sourceVerified=result.source_verified===true;
      state.branchCatalogId=selected;state.branchCatalogPending=false;
      renderProducts();refreshProductDialog();renderCart();queueQuote();
    }
    catch(error){if(state.branchId===selected&&state.service===selectedService&&state.branchCatalogRevision===revision){
      state.branchCatalogPending=false;renderProducts();refreshProductDialog();
      state.quote=null;state.quoteError=errorMessage(error);renderQuote();
    }}
  }

  const motionPreference=window.matchMedia('(prefers-reduced-motion: reduce)');
  const prefersLessMotion=()=>motionPreference.matches;
  function updateTickerMotion() {
    const track=$('announcement-track'),control=$('ticker-pause'),reduced=prefersLessMotion();
    track.classList.toggle('is-paused',state.tickerPaused||reduced);
    setText(control,reduced?t("motion_paused"):state.tickerPaused?t("resume_motion"):t("pause_motion"));
    control.setAttribute('aria-pressed',String(state.tickerPaused||reduced));
    control.disabled=reduced;
    control.title=reduced?t("motion_paused_by_device_preference"):'';
  }
  function renderTicker() {
    const area=$('announcement-ticker'),track=$('announcement-track');
    const config=state.presentation||{};
    const phrases=Array.isArray(config.ticker_lines)?config.ticker_lines.filter(x=>typeof x==='string'&&x.trim()).slice(0,8):[];
    area.hidden=!config.ticker_enabled||!phrases.length;
    track.replaceChildren();
    if(area.hidden)return;
    const part=node('span','ticker-cycle');
    const addPhrases=(duplicate=false)=>{
      for(const phrase of phrases){
        const item=node('span','ticker-text',phrase);item.dir='auto';
        if(duplicate)item.setAttribute('aria-hidden','true');
        part.append(item);
        const point=node('span','ticker-separator','•');
        point.setAttribute('aria-hidden','true');
        part.append(point);
      }
    };
    addPhrases();track.append(part);
    // Each cycle covers the viewport, so there is no empty gap at the seam.
    const phraseWidth=part.getBoundingClientRect().width;
    const needed=phraseWidth>0?Math.min(12,Math.ceil(area.clientWidth/phraseWidth)):1;
    for(let i=1;i<needed;i++)addPhrases(true);
    const clone=part.cloneNode(true);clone.setAttribute('aria-hidden','true');track.append(clone);
    const seconds=Number(config.ticker_seconds);
    track.style.setProperty('--ticker-duration',`${Number.isFinite(seconds)?Math.min(90,Math.max(14,seconds)):28}s`);
    updateTickerMotion();
  }
  function stopBannerRotation() {
    if(state.bannerTimer!==null)clearTimeout(state.bannerTimer);
    state.bannerTimer=null;
  }
  function startBannerRotation() {
    stopBannerRotation();
    const area=$('banner-area');
    if(state.banners.length<2||document.hidden||prefersLessMotion()||area.matches(':hover')||area.matches(':focus-within'))return;
    const delay=Number(state.banners[state.bannerIndex]?.rotation_ms);
    state.bannerTimer=setTimeout(()=>{
      state.bannerTimer=null;
      if(document.hidden||prefersLessMotion()||area.matches(':hover')||area.matches(':focus-within')){stopBannerRotation();return;}
      state.bannerIndex=(state.bannerIndex+1)%state.banners.length;
      renderBanner();
      startBannerRotation();
    },Number.isFinite(delay)?Math.min(90000,Math.max(4000,delay)):6500);
  }
  function switchBanner(index) {
    if(state.banners.length<2)return;
    state.bannerIndex=(index+state.banners.length)%state.banners.length;
    renderBanner();
    startBannerRotation();
  }
  async function loadBanners() {
    const selected=state.branchId,selectedCity=state.cityFilter,revision=++state.bannerRevision;
    stopBannerRotation();
    try {
      const params=new URLSearchParams({brand_id:state.brand.id||'kps'});
      if(selected)params.set('branch_id',selected);
      if(selectedCity)params.set('city',state.branches.find(b=>city(b.city)===selectedCity)?.city||selectedCity);
      const result=await api(`/api/banners?${params}`);
      if(state.branchId!==selected||state.cityFilter!==selectedCity||revision!==state.bannerRevision)return;
      state.banners=result.banners||[];
      state.bannerIndex=0;
      state.presentation=result.presentation||null;
      renderTicker();
      renderBanner();
      startBannerRotation();
    } catch {
      if(state.branchId===selected&&state.cityFilter===selectedCity&&revision===state.bannerRevision){
        state.banners=[];
        state.bannerIndex=0;
        state.presentation=null;
        renderTicker();
        renderBanner();
      }
    }
  }
  function renderBanner() {
    const banner=state.banners[state.bannerIndex];
    const container=$('banner-content'),controls=$('banner-controls');
    controls.replaceChildren();
    controls.hidden=state.banners.length<2;
    container.replaceChildren();
    container.setAttribute('aria-live','off');
    if(!banner){
      container.className='intro-banner';
      const content=node('div');
      content.append(
        node('span','eyebrow',sourceText(state.brand)),
        node('h1','',t("order_directly_from_0",{"0":()=>(sourceText(state.brand))})),
        node('p','',t("browse_the_menu_then_choose_your_service_and_branch"))
      );
      // The formerly oversized KPS watermark is removed; the owner may
      // enable an approved literary phrase in the future without redeploying.
      if(state.presentation?.quote_text)content.append(node('p','hero-quote',state.presentation.quote_text));
      container.append(content);
      return;
    }
    if(window.KPSGiftCreative?.render(container,{brandId:state.brand.id,banner}))return;
    container.className='campaign-banner';
    const image=safeImage(banner.desktop_image_url||banner.image_url);
    const mobileImage=safeImage(banner.mobile_image_url||image);
    if(image){
      const picture=node('picture');
      if(mobileImage){
        const source=node('source');
        source.media='(max-width: 550px)';
        source.srcset=mobileImage;
        picture.append(source);
      }
      const img=node('img');
      img.src=image;
      img.alt=banner.title||t("announcement_for_0",{"0":()=>(sourceText(state.brand))});
      img.decoding='async';
      img.addEventListener('error',()=>picture.remove(),{once:true});
      picture.append(img);
      container.append(picture);
    }
    const content=node('div','campaign-content'),copy=node('div');
    copy.append(node('h2','',banner.title||sourceText(state.brand)));
    if(banner.body||banner.text)copy.append(node('p','',banner.body||banner.text));
    content.append(copy);
    if(banner.destination){
      const target=banner.destination;
      let campaignLink='';
      if(target.kind==='link'){
        try{const url=new URL(target.id);if(url.protocol==='https:'&&!url.username&&!url.password)campaignLink=url.href;}catch{}
      }
      const action=node(campaignLink?'a':'button','button secondary',banner.button_label||t("view_details"));
      if(campaignLink){action.href=campaignLink;action.target='_blank';action.rel='noopener noreferrer';}
      else action.type='button';
      action.addEventListener('click',()=>{
        if(target.kind==='product'){
          const targetId=state.legacyItemMappings[target.id]?.product_id||target.id;
          const p=state.products.find((p)=>p.id===targetId);
          if(p)openProduct(p);
        } else if(target.kind==='category'){
          state.category=target.id||'';
          renderCategories();renderProducts();
          $('menu').scrollIntoView({behavior:'smooth'});
        } else if(target.kind==='menu')$('menu').scrollIntoView({behavior:'smooth'});
      });
      if(target.kind!=='link'||campaignLink)content.append(action);
    }
    container.append(content);
    if(state.banners.length>1)for(let index=0;index<state.banners.length;index++){
      const button=node('button','banner-dot',number.format(index+1));
      button.type='button';
      button.setAttribute('aria-label',t("announcement_0_of_1",{"0":()=>(number.format(index+1)),"1":()=>(number.format(state.banners.length))}));
      button.setAttribute('aria-current',String(index===state.bannerIndex));
      button.addEventListener('click',()=>switchBanner(index));
      controls.append(button);
    }
  }

  // A tab-scoped draft survives refresh and OTP without exposing another customer's pin.
  function persistLocationDraft() {
    try {
      if(!state.locationDraftRestored&&!state.location)return;
      if(!state.location){sessionStorage.removeItem(locationDraftKey);return;}
      const user=state.session.user;
      if(user&&(user.role!=='customer'||user.brand_id!==requestedBrandId))return;
      sessionStorage.setItem(locationDraftKey,JSON.stringify({location:state.location,owner:user?.id||null,
        branchId:state.branchId,service:state.service,url:mapLink.appliedUrl,
        neighborhood:$('neighborhood').value.slice(0,120),address:$('address').value.slice(0,500),savedAt:Date.now()}));
    } catch {}
  }
  function forgetLocationDraft() {
    try {
      sessionStorage.removeItem(locationDraftKey);
      if(state.session.user?.id)localStorage.removeItem(`order-location:${requestedBrandId}:${state.session.user.id}`);
    } catch {}
  }
  function restoreLocationDraft() {
    try {
      const draft=JSON.parse(sessionStorage.getItem(locationDraftKey)||'null'),user=state.session.user,point=draft?.location;
      if(!draft)return false;
      if(!Number.isFinite(draft.savedAt)||Date.now()-draft.savedAt>8*60*60*1000||draft.savedAt>Date.now()+60000||
          !point||!Number.isFinite(point.lat)||!Number.isFinite(point.lng)||Math.abs(point.lat)>90||Math.abs(point.lng)>180||
          draft.owner&&draft.owner!==user?.id||user&&(user.role!=='customer'||user.brand_id!==requestedBrandId)){
        sessionStorage.removeItem(locationDraftKey);return false;
      }
      if(['delivery','pickup','car'].includes(draft.service))state.service=draft.service;
      if(state.branches.some(b=>b.id===draft.branchId))state.branchId=draft.branchId;
      renderBranches();renderService();
      if(typeof draft.neighborhood==='string'&&draft.neighborhood.length<=120)$('neighborhood').value=draft.neighborhood;
      if(typeof draft.address==='string'&&draft.address.length<=500)$('address').value=draft.address;
      saveLocation(point.lat,point.lng,typeof draft.url==='string'?draft.url:'');
      if(state.branchId)loadBranchCatalog();
      return true;
    } catch {return false;}
  }
  function cancelMapLink() {
    clearTimeout(mapLink.timer);mapLink.controller?.abort();mapLink.revision++;
    mapLink.controller=null;mapLink.promise=null;mapLink.pending=false;
    $('parse-map-url').disabled=false;$('parse-map-url').removeAttribute('aria-busy');$('locate-button').disabled=false;
  }
  function mapLinkStatus(message,error=false) {
    let status=$('map-link-status');
    if(!status){status=node('p','small');status.id='map-link-status';status.setAttribute('role','status');status.setAttribute('aria-live','polite');$('parse-map-url').after(status);}
    status.className=error?'notice error':'small';status.textContent=message;status.hidden=!message;
  }
  function pastedMapLink(raw) {
    const clean=raw.replace(/[\u200e\u200f\u202a-\u202e\u2066-\u2069]/g,'').trim();
    const links=clean.match(/https:\/\/[^\s<>"']+/g);
    return links?.length===1?links[0].replace(/[)\]}>،,؛;.!؟]+$/g,''):clean;
  }
  function scheduleMapLink() {
    cancelMapLink();mapLink.appliedUrl='';forgetLocationDraft();state.location=null;
    $('latitude').value='';$('longitude').value='';$('clear-location').hidden=true;
    const value=$('map-url').value.trim();
    text('location-state','');mapLinkStatus(value?entryText('جارٍ تحديد موقعك من الرابط…','Finding your location from the link…'):'');
    mapLink.pending=!!value;queueQuote();queueNearestBranches();
    if(value)mapLink.timer=setTimeout(resolveMapLink,500);
  }
  function resolveMapLink() {
    clearTimeout(mapLink.timer);
    const value=pastedMapLink($('map-url').value);
    if(!value){mapLinkStatus(entryText('الصق رابط موقعك من خرائط جوجل أو حدد موقعك على الخريطة.','Paste your Google Maps location link or select your location on the map.'),true);return Promise.resolve(false);}
    if(state.location&&mapLink.appliedUrl===value)return Promise.resolve(true);
    if(mapLink.promise&&mapLink.url===value)return mapLink.promise;
    cancelMapLink();
    const revision=mapLink.revision,controller=new AbortController();
    mapLink.controller=controller;mapLink.url=value;mapLink.pending=true;
    $('parse-map-url').disabled=true;$('parse-map-url').setAttribute('aria-busy','true');
    mapLinkStatus(entryText('جارٍ تحديد موقعك من الرابط…','Finding your location from the link…'));
    queueQuote();
    mapLink.promise=(async()=>{
      try {
        const result=await api('/api/location/parse-map-url',{method:'POST',data:{url:value},signal:controller.signal});
        if(revision!==mapLink.revision||value!==pastedMapLink($('map-url').value))return false;
        const point=result.location;
        if(!point||!Number.isFinite(point.lat)||!Number.isFinite(point.lng)||Math.abs(point.lat)>90||Math.abs(point.lng)>180)throw new Error(entryText('تعذر تحديد الموقع من الرابط. حدد الدبوس على الخريطة.','Unable to locate this link. Select the pin on the map.'));
        saveLocation(point.lat,point.lng,value);return true;
      } catch(error) {
        if(revision!==mapLink.revision||error.name==='AbortError')return false;
        mapLinkStatus(errorMessage(error),true);
        text('location-state',entryText('لم يُعتمد الموقع بعد. أعد المحاولة أو حدد الدبوس على الخريطة.','Location is not confirmed yet. Retry or select the pin on the map.'));
        return false;
      } finally {
        if(revision===mapLink.revision){mapLink.pending=false;mapLink.promise=null;mapLink.controller=null;$('parse-map-url').disabled=false;$('parse-map-url').removeAttribute('aria-busy');queueQuote();}
      }
    })();
    return mapLink.promise;
  }
  function bindLocationLink() {
    $('map-url').addEventListener('input',scheduleMapLink);
    $('map-url').addEventListener('change',()=>{if($('map-url').value.trim())void resolveMapLink();});
    $('map-url').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();void resolveMapLink();}});
    $('clear-location').addEventListener('click',()=>{cancelMapLink();forgetLocationDraft();mapLink.appliedUrl='';$('map-url').value='';mapLinkStatus('');},true);
    window.addEventListener('pagehide',persistLocationDraft);
  }
  function saveLocation(lat,lng,mapUrl='') {if(!Number.isFinite(lat)||!Number.isFinite(lng)||Math.abs(lat)>90||Math.abs(lng)>180){toast(t("enter_valid_location_coordinates"));return;}cancelMapLink();mapLink.appliedUrl=mapUrl;$('map-url').value=mapUrl;mapLinkStatus(mapUrl?entryText('تم اعتماد موقعك تلقائيًا ✓','Your location is confirmed ✓'):'');state.location={lat,lng};if(state.entryEnabled&&entryVerified()){try{localStorage.setItem(`order-location:${requestedBrandId}:${state.session.user.id}`,JSON.stringify(state.location));}catch{}}text('location-state',t("location_saved_for_eligible_branch_suggestions_you_choose_the_bra"));$('latitude').value=String(lat);$('longitude').value=String(lng);$('clear-location').hidden=false;queueQuote();queueNearestBranches();}
  function applyTheme(theme={}) {
    if(state.brand.id==='enbishan')theme={accent:'#267481',background:'#f4f9f8',text:'#183e43',...theme};
    const valid=(value)=>typeof value==='string'&&/^#[a-f0-9]{6}$/i.test(value);
    const lum=(value)=>{const rgb=[1,3,5].map((start)=>parseInt(value.slice(start,start+2),16)/255).map((c)=>c<=.04045?c/12.92:((c+.055)/1.055)**2.4);return .2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2];};
    const contrast=(a,b)=>(Math.max(lum(a),lum(b))+.05)/(Math.min(lum(a),lum(b))+.05);
    const root=document.documentElement;
    if(valid(theme.accent)){root.style.setProperty('--accent',theme.accent);const ink=contrast(theme.accent,'#ffffff')>=4.5?'#ffffff':'#24231f';root.style.setProperty('--accent-ink',ink);root.style.setProperty('--accent-dark',theme.accent);}
    // This storefront keeps the approved light surface and legible panel text.
    const background=valid(theme.background)&&lum(theme.background)>=.75?theme.background:'#fbf9f5';
    root.style.setProperty('--bg',background);
    if(valid(theme.text)&&contrast(theme.text,background)>=4.5&&contrast(theme.text,'#ffffff')>=4.5)root.style.setProperty('--ink',theme.text);
    root.style.setProperty('--font','"Noto Kufi Arabic",Tahoma,Arial,sans-serif');
    window.KPSAppearance?.apply(theme,state.brand.id);
  }

  function changeLanguage(){
    language=language==='ar'?'en':'ar';
    try{localStorage.setItem(languageStorageKey,language);}catch{}
    applyStaticLanguage();updatePolicyLinks();text('customer-phone-status',state.session.customer_verified?t('phone_verified'):t('unverified'));renderBrand();renderBranches();renderService();renderCategories();renderProducts();renderCart();renderNearestBranches();
    if(state.product&&$('product-dialog').open){
      text('product-title',sourceText(state.product));
      text('product-description',sourceText(state.product,'description'));
      $('product-image').alt=String(sourceText(state.product));
      renderOptions();
    }
    if($('image-dialog').open&&state.imageProduct){
      text('image-title',sourceText(state.imageProduct));
      $('image-full').alt=sourceText(state.imageProduct);
    }
    if($('orders-dialog').open){
      if(state.activeOrder)renderOrderDetail(state.activeOrder,state.activeOrderEvents||[]);
      else renderOrders(state.orders);
    }
    const selectedContactBranch=$('contact-branch').value,selectedContactOrder=$('contact-order').value;
    $('contact-branch').replaceChildren(new Option(String(t('choose_a_branch')),''));
    for(const b of state.branches)$('contact-branch').append(new Option(`${b.branch_no} · ${sourceText(b)}`,b.id));
    $('contact-branch').value=selectedContactBranch;
    $('contact-order').replaceChildren(new Option(String(t('conversation_without_an_order')),''));
    for(const order of state.contactOrders)$('contact-order').append(new Option(String(t('order_0_1',{'0':()=>order.snapshot?.order_reference||order.number||order.order_no||order.id,'1':()=>serviceName(order.service)})),order.id));
    $('contact-order').value=selectedContactOrder;
    if($('contacts-dialog').open)renderContacts();
    if(state.conversation&&$('chat-dialog').open)text('chat-route',t('contact_'+(state.conversation.category||'help')));
    for(const element of document.querySelectorAll('[data-datetime]'))element.textContent=dateTime(element.dataset.datetime);
    text('account-button-label',state.session.user&&state.session.user.role!=='customer'?t('staff_account'):t('my_account'));
    if(state.session.customer_verified)text('identity-label',t('phone_verified'));
    text('account-branch-label',branch()?sourceText(branch()):t('choose_or_change_branch'));
    syncMobileBasket();
  }

  function clearCustomerView(){
    window.KPSCustomerExtras?.reset();
    const id=state.session.user?.id;cancelMapLink();forgetLocationDraft();mapLink.appliedUrl='';$('map-url').value='';mapLinkStatus('');clearTimeout(state.chatTimer);clearTimeout(state.ordersTimer);
    try{for(const k of Object.keys(sessionStorage))if((k.startsWith('kps-contact-draft:')||k.startsWith('kps-chat-draft:'))&&k.includes(`:${id}`))sessionStorage.removeItem(k);}catch{}
    state.addressEditing=null;state.addressPin=null;$('address-form').reset();state.changingPhone=false;state.challenge=null;state.afterLogin=null;state.savedAddresses=[];state.orders=[];state.contactOrders=[];state.conversations=[];state.conversation=null;state.location=null;
    for(const field of ['customer-name','customer-phone','address','neighborhood','chat-text','contact-details','profile-name','phone','otp-name'])$(field).value='';
    $('saved-address').replaceChildren(new Option(t('choose_address'),''));$('saved-address-field').hidden=true;$('profile-addresses').replaceChildren();$('chat-messages').replaceChildren();$('orders-list').replaceChildren();$('contacts-list').replaceChildren();
  }
  const contactDraftKey=()=>`kps-contact-draft:${requestedBrandId}:${state.session.user?.id||'anonymous'}`;
  const chatDraftKey=()=>`kps-chat-draft:${requestedBrandId}:${state.session.user?.id}:${state.conversation?.id}`;
  async function loadCustomerProfile(){
    const identity=state.session.user?.id;
    try{const result=await api('/api/customer/profile');if(identity!==state.session.user?.id)return;state.savedAddresses=result.addresses||[];const select=$('saved-address'),selected=select.value;select.replaceChildren(new Option(t('choose_address'),''));$('profile-addresses').replaceChildren();for(const a of state.savedAddresses){select.append(new Option(a.label||a.address,a.id));const row=node('div','saved-address-row');row.append(node('span','',a.label?`${a.label} · ${a.address}`:a.address));const button=node('button','button quiet',t('delete_address'));button.type='button';button.addEventListener('click',()=>busy(button,async()=>{try{if(!window.confirm(t('delete_address_confirm')))return;await api(`/api/customer/addresses/${encodeURIComponent(a.id)}`,{method:'DELETE'});await loadCustomerProfile();}catch(error){showError('account-error',errorMessage(error));}}));const edit=node('button','button quiet',t('edit_address'));edit.type='button';edit.addEventListener('click',()=>editAddress(a));const actions=node('div','row');actions.append(edit,button);row.append(actions);$('profile-addresses').append(row);}if(!state.savedAddresses.length){const empty=node('p','small muted',t('no_saved_addresses'));empty.dataset.i18n='no_saved_addresses';$('profile-addresses').append(empty);}select.value=selected;$('saved-address-field').hidden=!state.savedAddresses.length;}catch(error){if($('account-dialog').open)showError('account-error',errorMessage(error));}
  }
  function syncCustomerNavigation(){
    const active=$('account-dialog').open?'account-button':$('orders-dialog').open?'orders-button':($('contacts-dialog').open||$('chat-dialog').open||$('address-dialog').open)?'account-button':'menu-button';
    for(const id of ['menu-button','orders-button','contacts-button','account-button']){
      if(id===active)$(id).setAttribute('aria-current','page');else $(id).removeAttribute('aria-current');
    }
  }
  function returnToMenu(){
    closeDialog('account-dialog');closeDialog('contacts-dialog');closeDialog('orders-dialog');closeDialog('chat-dialog');closeDialog('address-dialog');
    $('menu').scrollIntoView({behavior:prefersLessMotion()?'auto':'smooth'});
    $('menu').focus({preventScroll:true});
  }
  async function openDataRequest(){
    if(!requireCustomer(openDataRequest))return;
    await openContacts();
    if($('contacts-dialog').open){$('contact-category').value='privacy';updateContactContext();$('contact-details').focus();}
  }
  function orderListUrl(offset=0){return `/api/orders?limit=30&offset=${offset}&customer_view=${state.orderFilter||'active'}`;}
  function editAddress(address){
    state.addressEditing=address?.id||null;state.addressPin=address?.lat!=null&&address?.lng!=null?{lat:address.lat,lng:address.lng}:null;
    $('address-label').value=address?.label||'';$('address-text').value=address?.address||'';$('address-neighborhood').value=address?.neighborhood||'';$('address-map').value='';
    showError('address-error','');updateAddressPin();openDialog('address-dialog');
  }
  function updateAddressPin(){text('address-pin-status',t(state.addressPin?'pin_saved':'pin_optional'));$('address-clear-pin').hidden=!state.addressPin&&!$('address-map').value;}
  async function saveAddress(event){
    event.preventDefault();await busy($('address-save'),async()=>{try{
      if($('address-map').value.trim()){const result=await api('/api/location/parse-map-url',{method:'POST',data:{url:$('address-map').value.trim()}});state.addressPin=result.location;}
      const payload={label:$('address-label').value.trim(),address:$('address-text').value.trim(),neighborhood:$('address-neighborhood').value.trim(),lat:state.addressPin?.lat??null,lng:state.addressPin?.lng??null};
      const id=state.addressEditing,idem=stableKey('save-address-'+(id||'new'),payload);
      await api('/api/customer/addresses'+(id?'/'+encodeURIComponent(id):''),{method:id?'PATCH':'POST',data:payload,idempotency:idem.value});
      releaseKey(idem);closeDialog('address-dialog');await loadCustomerProfile();toast(t('data_saved'));
    }catch(error){showError('address-error',errorMessage(error));}});
  }
  async function prepareReorder(id){
    try{
      const identity=state.session.user?.id;
      const {order}=await api(`/api/orders/${encodeURIComponent(id)}`);
      const selected=state.branchId||order.branch_id;
      const target=state.branches.find(b=>b.id===selected);
      const service=state.branchId?state.service:order.service;
      const result=await api(`/api/catalog?brand_id=${encodeURIComponent(requestedBrandId)}&service=${encodeURIComponent(service)}${target?'&branch_id='+encodeURIComponent(selected):''}`);
      if(identity!==state.session.user?.id)return;
      const lines=[];
      for(const item of order.snapshot?.items||[]){
        const mapping=state.legacyItemMappings[item.product_id];
        const productId=mapping?.product_id||item.product_id;
        const units=item.units?.length?item.units:Array.from({length:item.quantity},()=>({options:{}}));
        for(const unit of units){
          const options={...(unit.options||{}),...(mapping?.options||{})};
          for(const k of Object.keys(options))if(Array.isArray(options[k])&&options[k].length===1)options[k]=options[k][0];
          const line=lines.find(x=>x.productId===productId&&JSON.stringify(x.options)===JSON.stringify(options)&&x.note===(item.note||'')&&x.quantity<30);
          if(line)line.quantity++;else lines.push({productId,quantity:1,options,note:item.note||'',restoredName:item.name,rowId:key()});
        }
      }
      if(!lines.length||lines.reduce((n,l)=>n+l.quantity,0)>100)throw new Error(t('review_items_whose_prices_or_options_have_changed_before_confirmi'));
      let unavailable=!target,total=0;
      for(const line of lines){
        const p=result.products.find(p=>p.id===line.productId);
        if(!p||p.enabled===false||p.available===false||!p.source_verified||!cartOptionsAvailable(line,p))unavailable=true;
        total+=linePrice(line,p||{})*line.quantity;
      }
      const content=node('div');content.append(node('p','',t('reorder_notice')),node('p','small',target?sourceText(target):t('choose_a_branch')));
      if(state.cart.length)content.append(node('p','notice',t('replace_basket_notice')));
      if(unavailable)content.append(node('p','notice',t('reorder_unavailable')));
      content.append(node('p','money',t('estimated_item_total')+' · '+money(total)));
      confirmAction({title:t('reorder_review'),content,label:t('add_to_basket'),action:async()=>{
        if(identity!==state.session.user?.id)throw new Error(t('error_SESSION_CHANGED'));
        state.cart=lines;state.branchId=target?selected:'';state.service=service;
        if(service==='delivery'&&!state.location){const c=order.customer||{};$('address').value=c.address||'';$('neighborhood').value=c.neighborhood||'';state.location=c.location||null;}
        if(service==='car'){for(const field of ['model','color','plate'])if(!$('car-'+field).value)$('car-'+field).value=order.customer?.car?.[field]||'';$('car-color').dispatchEvent(new Event('change'));}
        state.branchCatalogRevision++;state.products=result.products||[];state.branchCatalogId=state.branchId;state.branchCatalogPending=false;
        renderBranches();renderService();renderProducts();cartChanged();
        closeDialog('confirm-dialog');closeDialog('orders-dialog');showBasket();toast(t('reorder_added'));
      }});
    }catch(error){showError('orders-error',errorMessage(error));}
  }
  function bindEvents() {
    // Native dialogs keep their own destinations reachable in normal flow.
    for(const id of ['account-dialog','orders-dialog']){
      const nav=node('nav','dialog-destinations');nav.setAttribute('aria-label',t('customer_navigation'));
      for(const [destination,label] of [['menu','home'],['orders','my_orders'],['account','my_account']]){
        const button=node('button','button quiet',t(label));button.type='button';button.dataset.destination=destination;
        if(id.startsWith(destination))button.setAttribute('aria-current','page');
        button.addEventListener('click',()=>{if(id.startsWith(destination))return;closeDialog(id);if(destination==='menu')returnToMenu();else if(destination==='orders')openOrders();else{state.identityIntent='login';renderAccount();openDialog('account-dialog');}});nav.append(button);
      }
      $(id).append(nav);
    }
    $('address-add').addEventListener('click',()=>editAddress());$('address-form').addEventListener('submit',saveAddress);
    $('address-map').addEventListener('input',()=>{state.addressPin=null;updateAddressPin();});
    $('address-clear-pin').addEventListener('click',()=>{state.addressPin=null;$('address-map').value='';updateAddressPin();});
    $('address-locate').addEventListener('click',()=>{if(!navigator.geolocation){showError('address-error',t('pin_optional'));return;}navigator.geolocation.getCurrentPosition(position=>{state.addressPin={lat:position.coords.latitude,lng:position.coords.longitude};$('address-map').value='';updateAddressPin();},()=>showError('address-error',t('pin_optional')),{enableHighAccuracy:true,timeout:15000,maximumAge:0});});
    document.querySelectorAll('[data-order-filter]').forEach(button=>button.addEventListener('click',()=>{state.orderFilter=button.dataset.orderFilter;openOrders();}));
    $('menu-button').addEventListener('click',returnToMenu);
    $('account-new-order').addEventListener('click',returnToMenu);
    $('account-orders').addEventListener('click',()=>{closeDialog('account-dialog');state.identityIntent='login';openOrders();});
    for(const id of ['account-help','account-contact'])$(id).addEventListener('click',()=>{closeDialog('account-dialog');openContacts();});
    $('account-data-request').addEventListener('click',()=>{closeDialog('account-dialog');openDataRequest();});
    $('account-branch').addEventListener('click',()=>{closeDialog('account-dialog');if(requestedBrandId==='enbishan')showEnbishanLocation();else $('service-title').scrollIntoView({behavior:prefersLessMotion()?'auto':'smooth'});$('branch-select').focus({preventScroll:true});});

    $('legal-contact-button').addEventListener('click',()=>openDialog('public-contact-dialog'));
    $('public-contact-support').addEventListener('click',()=>{closeDialog('public-contact-dialog');openContacts();});
    document.querySelectorAll('[data-kps-contact]').forEach(el=>el.hidden=requestedBrandId!=='kps');
    if(location.hash==='#contact')openDialog('public-contact-dialog');
    $('footer-contact').addEventListener('click',openContacts);
    $('contact-new-order').addEventListener('click',returnToMenu);
    $('contact-category').addEventListener('change',updateContactContext);
    $('contact-details').addEventListener('input',()=>{try{sessionStorage.setItem(contactDraftKey(),$('contact-details').value);}catch{}});
    $('chat-text').addEventListener('input',()=>{try{sessionStorage.setItem(chatDraftKey(),$('chat-text').value);}catch{}});
    $('customer-profile-form').addEventListener('submit',async event=>{event.preventDefault();await busy($('profile-save'),async()=>{try{const result=await api('/api/customer/profile',{method:'PATCH',data:{display_name:$('profile-name').value.trim()}});state.session.user=result.user;$('customer-name').value=result.user.display_name;renderAccount();toast(t('data_saved'));}catch(error){showError('account-error',errorMessage(error));}});});
    $('profile-change-phone').addEventListener('click',()=>{state.changingPhone=true;$('otp-request-form').hidden=false;$('otp-verify-form').hidden=true;$('phone').value='';$('phone').focus();});
    $('saved-address').addEventListener('change',()=>{const a=state.savedAddresses?.find(x=>x.id===$('saved-address').value);if(!a)return;$('address').value=a.address;$('neighborhood').value=a.neighborhood;if(a.lat!=null&&a.lng!=null)saveLocation(a.lat,a.lng);else{state.location=null;queueQuote();queueNearestBranches();}});
$('customer-language').addEventListener('click',changeLanguage);const tickerPause=$('ticker-pause');tickerPause.addEventListener('click',()=>{state.tickerPaused=!state.tickerPaused;updateTickerMotion();});$('banner-area').addEventListener('mouseenter',stopBannerRotation);$('banner-area').addEventListener('mouseleave',startBannerRotation);$('banner-area').addEventListener('focusin',stopBannerRotation);$('banner-area').addEventListener('focusout',()=>setTimeout(startBannerRotation,0));$('banner-area').addEventListener('keydown',(event)=>{if(state.banners.length<2||!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();switchBanner(state.bannerIndex+(event.key==='ArrowLeft'?1:-1));});document.querySelectorAll('[data-close]').forEach((button)=>button.addEventListener('click',()=>closeDialog(button.closest('dialog'))));document.querySelectorAll('dialog').forEach((dialog)=>{dialog.addEventListener('click',(event)=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closeDialog(dialog);}});dialog.addEventListener('cancel',()=>{if(dialog.id==='confirm-dialog')clearConfirmation();});dialog.addEventListener('close',()=>{if(dialog.open)return;const focus=focusHistory.get(dialog);if(focus?.isConnected&&focus.getClientRects().length)focus.focus({preventScroll:true});else $('menu').focus({preventScroll:true});syncCustomerNavigation();if(dialog.id==='chat-dialog')clearTimeout(state.chatTimer);if(dialog.id==='orders-dialog'){clearTimeout(state.ordersTimer);state.activeOrder=null;}if(dialog.id==='confirm-dialog')clearConfirmation();if(requestedBrandId==='enbishan')syncMobileBasket();});});$('city-select').addEventListener('change',()=>{state.cityFilter=$('city-select').value;renderBranches();queueNearestBranches();loadBanners();});$('branch-select').addEventListener('change',()=>changeBranch($('branch-select').value));document.querySelectorAll('input[name=service]').forEach((input)=>input.addEventListener('change',()=>{state.service=input.value;renderBranches();renderService();loadBranchCatalog();queueQuote();queueNearestBranches();}));document.querySelectorAll('input[name=payment]').forEach((input)=>input.addEventListener('change',queueQuote));['customer-name','customer-phone','neighborhood','order-notes','address','car-model','car-color','car-plate'].forEach((id)=>$(id).addEventListener('input',queueQuote));$('search').addEventListener('input',renderProducts);$('product-qty').addEventListener('change',renderOptions);$('product-minus').addEventListener('click',()=>{$('product-qty').value=String(Number($('product-qty').value)-1);renderOptions();});$('product-plus').addEventListener('click',()=>{$('product-qty').value=String(Number($('product-qty').value)+1);renderOptions();});$('apply-first-option').addEventListener('click',()=>{const first={...state.optionValues[0]};state.optionValues=state.optionValues.map(()=>({...first}));renderOptions();});$('product-add').addEventListener('click',()=>busy($('product-add'),addProduct));$('checkout-button').addEventListener('click',()=>busy($('checkout-button'),reviewEnbishanCheckout));$('confirm-submit').addEventListener('click',()=>{const action=state.confirmation?.action;if(action)busy($('confirm-submit'),async()=>{try{await action();}catch(error){showError('confirm-error',errorMessage(error));}});});$('header-cart').addEventListener('click',()=>showBasket());$('mobile-basket').addEventListener('click',()=>showBasket());$('account-button').addEventListener('click',()=>{state.afterLogin=null;state.identityIntent='login';renderAccount();showError('account-error','');openDialog('account-dialog');});$('orders-button').addEventListener('click',()=>{state.identityIntent='login';openOrders();});$('contacts-button').addEventListener('click',openContacts);$('chat-inbox').addEventListener('click',openContacts);$('contact-context-form').addEventListener('submit',startContact);$('contact-order').addEventListener('change',updateContactContext);$('identity-onboarding-form').addEventListener('submit',async event=>{event.preventDefault();await busy($('identity-onboarding-submit'),async()=>{try{if(!state.emailLink)throw new Error(t("open_the_invitation_link_again"));const result=await api('/api/identity/email/verify',{method:'POST',data:{...state.emailLink,display_name:$('identity-onboarding-name').value.trim(),phone:$('identity-onboarding-phone').value.trim()}});state.emailLink=null;await establishIdentity(result);}catch(error){showError('account-error',errorMessage(error));}});});$('parse-map-url').addEventListener('click',()=>{void resolveMapLink();});$('guest-form').addEventListener('submit',requestGuest);$('email-request-form').addEventListener('submit',requestEmail);['email-login-button','link-email-button'].forEach(id=>$(id).addEventListener('click',()=>{$('email-request-form').hidden=false;$('login-email').focus();}));$('product-image-zoom').addEventListener('click',showSelectedProductImage);$('product-image').addEventListener('click',showSelectedProductImage);$('otp-request-form').addEventListener('submit',requestOTP);$('otp-verify-form').addEventListener('submit',verifyOTP);$('change-phone').addEventListener('click',()=>{state.challenge=null;$('otp-request-form').hidden=false;$('otp-verify-form').hidden=true;showError('account-error','');$('phone').focus();});$('logout-button').addEventListener('click',()=>busy($('logout-button'),async()=>{try{await api('/api/auth/logout',{method:'POST',data:{}});clearCustomerView();state.session={user:null,csrf_token:null};renderAccount();closeDialog('account-dialog');toast(t("signed_out"));}catch(error){showError('account-error',errorMessage(error));}}));$('manual-location-button').addEventListener('click',()=>{$('manual-location').hidden=!$('manual-location').hidden;$('manual-location-button').setAttribute('aria-expanded',String(!$('manual-location').hidden));if(!$('manual-location').hidden)$('map-url').focus();});$('save-location').addEventListener('click',()=>{if(!$('latitude').value.trim()||!$('longitude').value.trim()){toast(t("enter_latitude_and_longitude_before_saving"));return;}saveLocation(Number($('latitude').value),Number($('longitude').value));});$('clear-location').addEventListener('click',()=>{state.location=null;if(state.entryEnabled&&entryVerified()){try{localStorage.removeItem(`order-location:${requestedBrandId}:${state.session.user.id}`);}catch{}}$('latitude').value='';$('longitude').value='';$('clear-location').hidden=true;text('location-state',t("selected_location_cleared_you_can_choose_a_city_and_branch_manual"));queueQuote();queueNearestBranches();});$('locate-button').addEventListener('click',()=>{if(!navigator.geolocation){toast(t("this_browser_does_not_support_location_you_can_enter_it_manually"));return;}cancelMapLink();const button=$('locate-button'),request=mapLink.revision;button.disabled=true;text('location-state',t("locating_you_with_your_permission"));navigator.geolocation.getCurrentPosition((position)=>{if(request!==mapLink.revision)return;button.disabled=false;saveLocation(position.coords.latitude,position.coords.longitude);},()=>{if(request!==mapLink.revision)return;button.disabled=false;text('location-state',state.location?t("unable_to_update_your_location_your_previous_location_is_kept_you"):t("location_permission_was_denied_or_your_location_is_unavailable_ch"));$('city-select').focus();},{enableHighAccuracy:true,timeout:15000,maximumAge:0});});$('chat-form').addEventListener('submit',sendMessage);['chat-image','chat-camera'].forEach(id=>$(id).addEventListener('change',()=>selectChatImage($(id).files[0])));$('chat-image-remove').addEventListener('click',clearChatImage);$('chat-dialog').addEventListener('close',clearChatImage);document.addEventListener('visibilitychange',()=>{if(!document.hidden&&$('chat-dialog').open)pollChat();if(document.hidden)stopBannerRotation();else startBannerRotation();});window.addEventListener('online',()=>{toast(t("connection_restored_checking_saved_orders"));if($('chat-dialog').open)pollChat();if(state.cart.length)queueQuote();recoverCheckout().catch(error=>showError('basket-error',errorMessage(error)));});let touchStart=0;$('banner-area').addEventListener('touchstart',(event)=>{touchStart=event.changedTouches[0].clientX;},{passive:true});$('banner-area').addEventListener('touchend',(event)=>{const delta=event.changedTouches[0].clientX-touchStart;if(Math.abs(delta)>50&&state.banners.length>1){switchBanner(state.bannerIndex+(delta>0?1:-1));}},{passive:true});}
  function onMotionPreferenceChange() {updateTickerMotion();if(prefersLessMotion())stopBannerRotation();else startBannerRotation();}
  if(motionPreference.addEventListener)motionPreference.addEventListener('change',onMotionPreferenceChange);
  else if(motionPreference.addListener)motionPreference.addListener(onMotionPreferenceChange);
  let tickerViewportWidth=innerWidth;
  window.addEventListener('resize',()=>{if(tickerViewportWidth===innerWidth)return;tickerViewportWidth=innerWidth;clearTimeout(renderTicker.resizeTimer);renderTicker.resizeTimer=setTimeout(renderTicker,150);},{passive:true});
  // Re-measure text when the existing local Noto font finishes loading.
  if(document.fonts)document.fonts.ready.then(renderTicker);
  function renderBrand() {
    const name=sourceText(state.brand)||t("orders");document.title=t("0_orders",{"0":()=>(name)});
    document.querySelector('meta[name=description]').content=t("0_menu_delivery_branch_pickup_and_car_pickup_orders",{"0":()=>(name)});
    $('brand-home').href=requestedBrandId&&requestedBrandId!=='kps'?`/?brand_id=${encodeURIComponent(requestedBrandId)}`:'/';$('brand-home').setAttribute('aria-label',t("0_home",{"0":()=>(name)}));
    const label=$('brand-name');label.replaceChildren();label.dir=requestedBrandId==='kps'?'ltr':'auto';
    if(requestedBrandId==='kps'){label.append(document.createTextNode('KPS '),node('span','','COFFEE'));}else label.append(document.createTextNode(name));
    label.append(node('small','',t("your_order_your_way")));text('footer-brand',name);$('banner-area').setAttribute('aria-label',t("0_announcements",{"0":()=>(name)}));
    const logo=safeImage(state.brand.logo_url||(requestedBrandId==='kps'?'/assets/kps-logo-fb911a7b4bb4.png':requestedBrandId==='enbishan'?'/assets/enbishan-original.jpeg':''));
    $('brand-logo').alt=name;$('brand-logo').hidden=!logo;if(logo){$('brand-logo').src=/^\/assets\/[A-Za-z0-9_.-]+\.(jpg|jpeg|png|webp)$/.test(logo)?logo.replace('/assets/','/media/previews/'):logo;$('brand-logo').fetchPriority='high';$('brand-logo').addEventListener('error',()=>{$('brand-logo').src=logo;},{once:true});}else $('brand-logo').removeAttribute('src');
    renderBanner();
  }
  async function init() {
    // Restore identity before an early orders/contact click can start a guest flow.
    ['account-button','orders-button','contacts-button'].forEach((id)=>$(id).disabled=true);
    applyStaticLanguage();mountEnbishanJourney();bindEvents();bindLocationLink();renderBrand();
    if(!requestedBrandId){showError('site-notice',t("invalid_brand_link_use_a_link_provided_by_the_brand"));$('products').replaceChildren(node('div','empty-state',t("unable_to_identify_the_brand_from_this_link")));['account-button','orders-button','contacts-button','branch-select','city-select','locate-button','manual-location-button','save-location','parse-map-url'].forEach((id)=>$(id).disabled=true);return;}
    const params=new URLSearchParams({brand_id:requestedBrandId});
    const results=await Promise.allSettled([api(`/api/catalog?${params}`),api(`/api/branches?${params}`),api('/api/session'),api(`/api/identity/channels?${params}`),api(`/api/experience/public?${params}`)]);
    state.entryEnabled=results[4].status==='fulfilled'&&results[4].value.customer_entry_enabled===true;
    state.offerActive=results[4].status==='fulfilled'&&results[4].value.gift_offer_active===true;
    const [catalogResult,branchResult,sessionResult,channelResult]=results;if(channelResult.status==='fulfilled'){state.phoneRequired=channelResult.value.customer_phone_required;state.channels=channelResult.value.channels||channelResult.value;}
    if(catalogResult.status==='fulfilled'){
      const data=catalogResult.value;
      if(data.brand?.id!==requestedBrandId)throw new Error(t("the_brand_does_not_match_this_link"));
      state.products=data.products||[];state.legacyItemMappings=data.legacy_item_mappings||{};state.brand=data.brand;renderBrand();applyTheme(state.brand.theme);state.sourceVerified=data.source_verified===true||data.metadata?.runtime_verified===true;
      if(!state.sourceVerified&&state.products.length){showError('catalog-notice',requestedBrandId==='kps'?t("some_items_differ_from_the_approved_source_or_need_approval_appro"):t("current_prices_and_options_are_awaiting_verification_orders_can_b"));$('catalog-notice').className='notice';}
      restoreDraft();renderCategories();renderProducts();
    }else{$('products').replaceChildren(node('div','empty-state',t("unable_to_load_this_brand_s_menu_refresh_to_try_again")));showError('site-notice',errorMessage(catalogResult.reason));}
    if(branchResult.status==='fulfilled'){state.branches=branchResult.value.branches||[];state.brandServices=branchResult.value.brand_services||state.brandServices;renderBranches();}else showError('service-notice',errorMessage(branchResult.reason));
    if(sessionResult.status==='fulfilled')state.session=sessionResult.value;else showError('site-notice',t("you_can_browse_the_menu_your_sign_in_session_could_not_be_checked"));
    state.identityReady=true;renderAccount();renderService();renderCart();loadBanners();await verifyEmailLink();
    if(!restoreLocationDraft()&&state.entryEnabled&&entryVerified()){try{const point=JSON.parse(localStorage.getItem(`order-location:${requestedBrandId}:${state.session.user.id}`)||'null');if(point&&Number.isFinite(point.lat)&&Number.isFinite(point.lng))saveLocation(point.lat,point.lng);}catch{}}
    state.locationDraftRestored=true;
    renderCustomerEntry();
    try{await recoverCheckout();}catch(error){showError('basket-error',errorMessage(error));}
    window.KPSCustomerMap?.mount({api,brandId:requestedBrandId,host:document.querySelector('.branch-location-tools .row'),getLocation:()=>state.location,onConfirm:saveLocation,tr:entryText});
  }
  init().catch(()=>{state.identityReady=Boolean(requestedBrandId);renderAccount();showError('site-notice',t("unable_to_load_part_of_the_page_refresh_to_try_again"));});
})();



