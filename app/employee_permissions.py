"""Named employee capabilities over the existing role and resource policies.

An empty denial list preserves legacy access. Capabilities never change the
employee's role, brand, branches, task ownership, custody or approval policies.
The route table uses resolved endpoint identities, not caller-supplied URLs.
"""

ADMIN = {'branches_manage', 'products_manage', 'banners_manage', 'appearance_manage',
         'brand_services_manage', 'vehicle_maintenance_manage', 'branch_printers_manage',
         'accounting_reports', 'accounting_review', 'accounting_cases_followup'}
MANAGER = {'delivery_manager'}
DELIVERY = MANAGER | {'delivery_staff'}
OPERATORS = DELIVERY | {'branch_manager'}
FIELD = OPERATORS | {'driver'}

# code, Arabic name, English name, group, eligible roles
DEFINITIONS = [
    ('dashboard_view', 'عرض نظرة التشغيل والإحصائيات', 'View operational overview', 'orders', OPERATORS),
    ('orders_create_customer', 'إنشاء طلب نيابة عن العميل', 'Place an order for a customer', 'orders', MANAGER),
    ('orders_amend', 'مراجعة واعتماد تعديل الطلب بطلب العميل', 'Review customer-requested amendments', 'orders', MANAGER | {'branch_manager'}),
    ('orders_view', 'عرض الطلبات وتفاصيلها', 'View orders and details', 'orders', FIELD),
    ('orders_prepare', 'تحديث تجهيز الطلب وتسليمه', 'Update preparation and handover', 'orders', {'branch_manager'}),
    ('orders_cancel', 'رفض وإلغاء الطلبات غير المحصلة', 'Reject or cancel unpaid orders', 'orders', MANAGER | {'branch_manager'}),
    ('orders_contact', 'تسجيل نتيجة التواصل مع الزبون', 'Record customer contact outcome', 'orders', MANAGER | {'branch_manager'}),
    ('delivery_assign', 'إسناد الطلبات للسائقين', 'Assign orders to drivers', 'delivery', DELIVERY),
    ('tasks_view', 'عرض مهام التوصيل', 'View delivery tasks', 'delivery', DELIVERY | {'driver'}),
    ('tasks_update', 'تحديث المهمة وإبلاغ مشكلة', 'Update task or report a problem', 'delivery', MANAGER | {'driver'}),
    ('tasks_transfer', 'تحويل المهمة إلى سائق آخر', 'Transfer a task to another driver', 'delivery', MANAGER),
    ('tasks_accept_transfer', 'قبول مهمة محولة إليك', 'Accept a transferred task', 'delivery', {'driver'}),
    ('task_photos', 'رفع صور إثبات التسليم', 'Upload delivery evidence photos', 'delivery', {'driver'}),
    ('operational_alerts', 'عرض تنبيهات التشغيل', 'View operational alerts', 'delivery', DELIVERY),
    ('drivers_view', 'عرض السائقين وبياناتهم', 'View drivers and profiles', 'staff', DELIVERY),
    ('drivers_manage', 'دعوة السائقين وتعديل بياناتهم وفروعهم', 'Invite drivers and edit their profiles and branches', 'staff', MANAGER),
    ('drivers_availability', 'تفعيل وإيقاف استقبال السائق للمهام', 'Control driver task availability', 'staff', MANAGER),
    ('driver_terminal', 'إدارة جهاز الشبكة المسند للسائق', 'Manage assigned driver payment terminal', 'staff', MANAGER),
    ('driver_terminal_view', 'عرض بيانات جهاز الشبكة المسند', 'View assigned payment terminal', 'cash', MANAGER | {'driver'}),
    ('shifts_view', 'عرض الشفتات وتفاصيل التقفيلات', 'View shifts and closure details', 'shifts', DELIVERY | {'driver'}),
    ('shifts_open', 'فتح الشفت', 'Open a shift', 'shifts', MANAGER | {'driver'}),
    ('shifts_close', 'إرسال تقفيلة شفتك', 'Submit your shift closure', 'shifts', MANAGER | {'driver'}),
    ('shifts_approve', 'استلام واعتماد تقفيلات السائقين', 'Receive and approve driver closures', 'shifts', MANAGER),
    ('shifts_delegate_close', 'إقفال شفت السائق بالنيابة مع سبب', 'Close a driver shift on their behalf with a reason', 'shifts', MANAGER),
    ('shifts_handover', 'تسليم واستلام مسؤولية مناوبة المدير', 'Hand over or receive manager shift responsibility', 'shifts', MANAGER),
    ('payments_collect', 'تسجيل تحصيل الكاش والشبكة', 'Record cash and card collection', 'cash', MANAGER | {'driver', 'branch_manager'}),
    ('refunds_manage', 'اعتماد وتسجيل استرداد الزبون', 'Authorize and record customer refunds', 'cash', MANAGER),
    ('custody_view', 'عرض استلام وتسليم النقد', 'View cash handovers', 'cash', FIELD),
    ('custody_transfer', 'تسجيل تسليم النقد وتأكيد الاستلام', 'Record cash handover and confirm receipt', 'cash', MANAGER | {'driver', 'branch_manager'}),
    ('reports_view', 'عرض تقارير التقفيلة وطباعتها وتصديرها', 'View, print and export closure reports', 'reports', FIELD),
    ('chat_view', 'عرض محادثات الزبائن', 'View customer conversations', 'support', OPERATORS),
    ('chat_reply', 'الرد على الزبائن وإرسال الصور', 'Reply to customers and send photos', 'support', OPERATORS),
    ('chat_manage', 'استلام وتحويل وإغلاق المحادثات', 'Claim, transfer and close conversations', 'support', OPERATORS),
    ('support_manage', 'إدارة تذاكر الشكاوى والمطالبات والتعويض', 'Manage complaint and compensation tickets', 'support', MANAGER),
    ('branch_availability', 'إيقاف واستئناف توفر الصنف بالفرع', 'Pause or resume branch item availability', 'configuration', {'branch_manager'}),
    ('vehicle_refuel', 'تسجيل تعبئة الوقود أثناء الشفت', 'Record shift refuelling', 'vehicles', MANAGER | {'driver'}),
    ('vehicle_register', 'تسجيل مركبتك أول مرة', 'Register your vehicle for the first time', 'vehicles', {'driver'}),
    ('vehicle_faults_report', 'رفع بلاغ عطل المركبة ومتابعته', 'Report and view your vehicle faults', 'vehicles', {'driver'}),
    ('vehicle_maintenance_manage', 'إدارة السيارات وسجل التشغيل والأعطال والصيانة', 'Manage vehicles, operating logs and maintenance', 'vehicles', {'admin'}),
    ('branch_printers_manage', 'إدارة الطابعات وطباعة وإعادة طباعة الطلبات', 'Manage printers, print and reprint orders', 'configuration', {'admin', 'delivery_manager'}),
    ('accounting_reports', 'تقارير المحاسبة والتصدير', 'Accounting reports and exports', 'reports', {'admin'}),
    ('accounting_review', 'مراجعة العجز وإقفال الشهر وتسجيل الملاحظات', 'Review variances, close months and record incidents', 'reports', {'admin'}),
    ('accounting_cases_followup', 'متابعة ملاحظات المحاسبة والمركبات', 'Follow up accounting and vehicle incidents', 'reports', {'admin', 'delivery_manager'}),
    ('branches_manage', 'إدارة الفروع وخدماتها', 'Manage branches and services', 'configuration', {'admin'}),
    ('products_manage', 'إدارة المنيو والأصناف', 'Manage menu and items', 'configuration', {'admin'}),
    ('banners_manage', 'إدارة الإعلانات والصور', 'Manage campaigns and images', 'configuration', {'admin'}),
    ('appearance_manage', 'إدارة التصميم والشريط', 'Manage appearance and moving strip', 'configuration', {'admin'}),
    ('brand_services_manage', 'إظهار وإيقاف خدمات البراند', 'Show or pause brand services', 'configuration', {'admin'}),
]
REGISTRY = {row[0]: row for row in DEFINITIONS}
GROUPS = {
    'orders': ('الطلبات', 'Orders'), 'delivery': ('التوصيل', 'Delivery'),
    'staff': ('السائقون', 'Drivers'), 'shifts': ('الشفتات والتقفيلات', 'Shifts and closures'),
    'cash': ('النقد والتحصيل', 'Cash and collection'), 'reports': ('التقارير والمحاسبة', 'Reports and accounting'),
    'support': ('المحادثات والمتابعة', 'Conversations and support'),
    'vehicles': ('السيارات', 'Vehicles'), 'configuration': ('الإعدادات', 'Configuration'),
}


def eligible(role):
    if role == 'owner':
        return set(REGISTRY)
    return {code for code, _, _, _, roles in DEFINITIONS if role in roles}


def effective(user):
    role = user.get('role')
    if role == 'owner':
        return eligible(role)
    base = set(user.get('permissions') or []) & ADMIN if role == 'admin' else eligible(role)
    return base - set(user.get('permission_denials') or [])


def catalog():
    return [{'code': code, 'name_ar': ar, 'name_en': en, 'group': group,
             'group_ar': GROUPS[group][0], 'group_en': GROUPS[group][1],
             'roles': sorted(roles), 'default_roles': sorted(roles - {'admin'})}
            for code, ar, en, group, roles in DEFINITIONS]


# Resolved endpoint -> required capability. Existing endpoint policies still run.
ROUTES = {
    'driver_float': {'listing':('drivers_view','accounting_review'), 'propose':'custody_transfer', 'decide':'accounting_review'},
    'order_amendments': {'inbox':'orders_view', 'detail':'orders_view', 'preview':'orders_amend', 'request_amendment':'orders_amend', 'decide':'orders_amend',
                         'acknowledge':{'branch_manager':'orders_prepare','driver':'tasks_update','delivery_manager':'orders_amend'}},
    'driver_incentives': {'achievement': ('shifts_view','accounting_reports'), 'shift_review': ('shifts_view','accounting_reports'), 'decide': 'shifts_approve'},
    'operations': {
        'dashboard': 'dashboard_view', 'operational_alerts': 'operational_alerts',
        'open_shift': 'shifts_open', 'shifts': 'shifts_view', 'shift_detail': 'shifts_view',
        'people': ('custody_view', 'drivers_view', 'delivery_assign', 'shifts_close'), 'driver_roster': 'drivers_view',
        'refuel_shift': 'vehicle_refuel', 'request_close': 'shifts_close',
        'submit_close': 'shifts_close', 'return_close': 'shifts_approve', 'approve_close': 'shifts_approve',
        'assign': 'delivery_assign', 'tasks': 'tasks_view', 'task_transition': 'tasks_update',
        'task_handover': 'tasks_transfer', 'accept_task_handover': 'tasks_accept_transfer',
        'collect': 'payments_collect', 'refund': {'delivery_manager':'refunds_manage','branch_manager':'payments_collect'},
        'get_payment_terminal': 'driver_terminal_view', 'set_payment_terminal': 'driver_terminal',
        'custody_transfer': 'custody_transfer', 'custody_list': 'custody_view', 'confirm_custody': 'custody_transfer',
        'simple_driver_close': {'driver': 'shifts_close', 'delivery_manager': 'shifts_delegate_close'},
        'manager_handover': 'shifts_handover', 'manager_handover_list': 'shifts_view',
        'accept_manager_handover': 'shifts_handover', 'closure_draft': 'shifts_view',
        'save_closure_draft': 'shifts_close', 'set_shift_availability': 'drivers_availability',
    },
    'orders': {'create_delivery_center_order': 'orders_create_customer', 'list_orders': 'orders_view', 'get_order': 'orders_view',
               'preparation': {'delivery_manager': 'orders_cancel', 'branch_manager': 'orders_prepare'}, 'operational_confirmation': 'orders_contact'},
    'reporting': {name: 'reports_view' for name in (
        'report_list', 'report_detail', 'print_report', 'download_pdf', 'download_csv',
        'retry', 'original_pdf', 'v5_download', 'v5_retry')},
    'chat': {name: permission for permission, names in (
        ('tasks_view', ('driver_customer_messages','driver_customer_download')),
        ('tasks_update', ('driver_customer_send','driver_customer_upload')),
        ('chat_view', ('inbox', 'reply_alerts', 'messages', 'download', 'mark_read')),
        ('chat_reply', ('create_conversation', 'send', 'upload')),
        ('chat_manage', ('transfer', 'claim', 'status'))
    ) for name in names},
    'order_support': {'tickets': 'support_manage', 'create_ticket': 'support_manage',
                      'update_ticket': 'support_manage', 'daily': 'support_manage',
                      'upload_photo': 'task_photos', 'photos': 'orders_view', 'photo': 'orders_view'},
    'auth': {'staff_list': 'drivers_view', 'invite_staff': 'drivers_manage',
             'update_staff': 'drivers_manage', 'resend_invitation': 'drivers_manage',
             'revoke_invitation': 'drivers_manage', 'register_driver_vehicle': 'vehicle_register'},
    'catalog': {'nearest_branches': 'delivery_assign',
                'branch_product_availability': {'branch_manager': 'branch_availability', 'admin': 'products_manage'}},
    'maintenance': {name: {'driver': 'vehicle_faults_report', 'admin': 'vehicle_maintenance_manage'}
                    for name in ('options', 'list_reports', 'report_detail', 'create_report', 'update_report')},
    'fleet': {name: 'vehicle_maintenance_manage' for name in (
        'vehicles', 'options', 'report', 'vehicle', 'export', 'detail', 'correct')},
    'branch_printing': {name: 'branch_printers_manage' for name in (
        'pair_device', 'devices', 'guided_setup', 'revoke', 'test_printer', 'approve_printer',
        'enqueue', 'jobs', 'download_bridge')},
    'accounting': {name: {'delivery_manager': 'accounting_cases_followup'} for name in (
        'options', 'cases', 'detail', 'followup', 'upload', 'download')},
}


def required_for(endpoint, role):
    if role in {'owner', 'customer'} or endpoint is None:
        return None
    module = endpoint.__module__.rsplit('.', 1)[-1]
    value = ROUTES.get(module, {}).get(endpoint.__name__)
    return value.get(role) if isinstance(value, dict) else value
