"""Named employee capabilities over the existing role and resource policies.

An empty denial/grant list preserves legacy access. Capabilities never change the
employee's role, brand, branches, task ownership, custody or approval policies.
The route table uses resolved endpoint identities, not caller-supplied URLs.
"""

ADMIN = {'branches_manage', 'products_manage', 'banners_manage', 'appearance_manage',
         'brand_services_manage', 'vehicle_maintenance_manage', 'fleet_active_swap', 'branch_printers_manage',
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
    ('tasks_resolve_delivery', 'معالجة الطلب المتعثر وتوثيق التسليم الفعلي', 'Resolve stalled orders with verified delivery', 'delivery', MANAGER),
    ('tasks_transfer', 'تحويل المهمة إلى سائق آخر', 'Transfer a task to another driver', 'delivery', MANAGER),
    ('tasks_accept_transfer', 'قبول مهمة محولة إليك', 'Accept a transferred task', 'delivery', {'driver'}),
    ('task_photos', 'رفع صور إثبات التسليم', 'Upload delivery evidence photos', 'delivery', {'driver'}),
    ('operational_alerts', 'عرض تنبيهات التشغيل', 'View operational alerts', 'delivery', DELIVERY),
    ('drivers_view', 'عرض السائقين وبياناتهم', 'View drivers and profiles', 'staff', DELIVERY),
    ('drivers_manage', 'دعوة السائقين وتعديل بياناتهم وفروعهم', 'Invite drivers and edit their profiles and branches', 'staff', set()),
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
    ('refunds_manage', 'اعتماد وتسجيل استرداد الزبون', 'Authorize and record customer refunds', 'cash', set()),
    ('custody_view', 'عرض استلام وتسليم النقد', 'View cash handovers', 'cash', FIELD),
    ('custody_transfer', 'تسجيل تسليم النقد وتأكيد الاستلام', 'Record cash handover and confirm receipt', 'cash', MANAGER | {'driver', 'branch_manager'}),
    ('reports_view', 'عرض تقارير التقفيلة وطباعتها وتصديرها', 'View, print and export closure reports', 'reports', FIELD),
    ('chat_view', 'عرض محادثات الزبائن', 'View customer conversations', 'support', OPERATORS),
    ('chat_reply', 'الرد على الزبائن وإرسال الصور', 'Reply to customers and send photos', 'support', OPERATORS),
    ('chat_manage', 'استلام وتحويل وإغلاق المحادثات', 'Claim, transfer and close conversations', 'support', OPERATORS),
    ('support_manage', 'إدارة تذاكر الشكاوى والمطالبات والتعويض', 'Manage complaint and compensation tickets', 'support', MANAGER),
    ('branch_availability', 'إيقاف واستئناف توفر الصنف بالفرع', 'Pause or resume branch item availability', 'configuration', {'branch_manager'}),
    ('vehicle_refuel', 'تسجيل تعبئة الوقود أثناء الشفت', 'Record shift refuelling', 'vehicles', MANAGER | {'driver'}),
    ('vehicle_register', 'تسجيل مركبتك أول مرة', 'Register your vehicle for the first time', 'vehicles', set()),
    ('vehicle_faults_report', 'رفع بلاغ عطل المركبة ومتابعته', 'Report and view your vehicle faults', 'vehicles', {'driver'}),
    ('vehicle_maintenance_manage', 'إدارة السيارات وسجل التشغيل والأعطال والصيانة', 'Manage vehicles, operating logs and maintenance', 'vehicles', {'admin'}),
    ('fleet_active_swap', 'اعتماد تبديل سيارتي سائقين أثناء الشفت', 'Approve two-driver vehicle swaps during active shifts', 'vehicles', {'admin'}),
    ('branch_printers_manage', 'إدارة الطابعات', 'Manage printers', 'configuration', {'admin', 'delivery_manager'}),
    ('orders_print', 'طباعة وإعادة طباعة الطلبات', 'Print and reprint orders', 'orders', MANAGER),
    ('accounting_reports', 'تقارير المحاسبة والتصدير', 'Accounting reports and exports', 'reports', {'admin'}),
    ('accounting_review', 'مراجعة العجز وإقفال الشهر وتسجيل الملاحظات', 'Review variances, close months and record incidents', 'reports', {'admin'}),
    ('accounting_cases_followup', 'متابعة ملاحظات المحاسبة والمركبات', 'Follow up accounting and vehicle incidents', 'reports', {'admin', 'delivery_manager'}),
    ('branches_manage', 'إدارة الفروع وخدماتها', 'Manage branches and services', 'configuration', {'admin'}),
    ('products_manage', 'إدارة المنيو والأصناف', 'Manage menu and items', 'configuration', {'admin'}),
    ('banners_manage', 'إدارة الإعلانات والصور', 'Manage campaigns and images', 'configuration', {'admin'}),
    ('appearance_manage', 'إدارة التصميم والشريط', 'Manage appearance and moving strip', 'configuration', {'admin'}),
    ('brand_services_manage', 'إظهار وإيقاف خدمات البراند', 'Show or pause brand services', 'configuration', {'admin'}),
]
# Explicit cross-role grants are fail-closed. Other capabilities retain their
# existing resource/role policies until their API scope contracts are certified.
DEFINITIONS.append(('staff_catalog_view', 'عرض كتالوج الصلاحيات الإضافية فقط',
                    'View additional permission catalog only', 'staff', set()))
DEFINITIONS.append(('orders_operational_view', 'عرض حالة الطلبات وتسلسلها التشغيلي ضمن الفروع',
                    'View branch operational order status and history', 'orders', set()))
DEFINITIONS.append(('driver_hours_reports', 'عرض وتصدير ساعات عمل السائقين ضمن الفروع',
                    'View and export scoped driver work hours', 'reports', {'delivery_manager'}))
DEFINITIONS.append(('driver_support_approve', 'اعتماد أو رفض طلب دعم سائق',
                    'Approve or reject scoped driver support requests', 'shifts', set()))
DEFINITIONS.append(('complimentary_approve', 'اعتماد وإلغاء أوامر الضيافة والتعويض والاستكمال غير المنفذة',
                    'Authorize and revoke unused courtesy fulfillment commands', 'orders', set()))
DEFINITIONS.append(('driver_duty_manage', 'تحديد ساعات دوام السائقين', 'Configure driver duty hours', 'staff', set()))
REGISTRY = {row[0]: row for row in DEFINITIONS}
EXTRA_GRANT_ROLES = {
    'driver_duty_manage': {'admin', 'delivery_manager'},
    'complimentary_approve': {'admin', 'delivery_manager'},
    'driver_support_approve': {'admin', 'delivery_manager'},
    'branch_availability': {'driver', 'delivery_staff', 'delivery_manager', 'admin'},
    'branch_printers_manage': {'driver', 'branch_manager', 'delivery_manager', 'delivery_staff', 'admin'},
    'staff_catalog_view': {'driver', 'branch_manager', 'delivery_staff', 'delivery_manager', 'admin'},
    'orders_operational_view': {'driver', 'branch_manager', 'delivery_staff', 'delivery_manager', 'admin'},
    'driver_hours_reports': {'driver', 'branch_manager', 'delivery_staff', 'admin'},
}

# Accounting staff use the existing admin role. These are separate, explicit
# owner grants; neither accounting access nor a read grant implies a mutation.
SHIFT_DELEGATIONS = {'shifts_view', 'shifts_open', 'shifts_close',
                     'shifts_approve', 'shifts_delegate_close', 'shifts_handover'}
EXTRA_GRANT_ROLES.update({code: {'admin'} for code in SHIFT_DELEGATIONS})


# Contracts are descriptive, never caller-selectable route authorization rules.
EXTRA_API_CONTRACTS = {
    'driver_duty_manage': {'methods':['GET','PUT'], 'paths':['/api/driver-duty','/api/driver-duty/{identifier}'], 'scope':'current_brand_assigned_driver_branches_next_shift_only_no_financial_authority'},
    'complimentary_approve': {'methods':['GET','POST'], 'paths':['/api/orders/complimentary/quote','/api/orders/complimentary/authorizations','/api/orders/complimentary/authorizations/{identifier}/revoke'], 'scope':'explicit_owner_grant_current_brand_all_original_and_fulfillment_branches_no_refund_or_cash_permission'},
    'driver_support_approve': {'methods':['GET','POST'], 'paths':['/api/driver-support','/api/driver-support/{rid}/accept','/api/driver-support/{rid}/resolve'], 'scope':'owner_granted_staff_identity_all_affected_brand_and_branch_scopes_no_cash_transfer'},
    'staff_catalog_view': {'methods':['GET'], 'paths':['/api/identity/permissions/catalog'], 'scope':'catalog_only_no_personnel_no_grant'},
    'orders_operational_view': {'methods':['GET'], 'paths':['/api/staff/scoped/orders','/api/staff/scoped/orders/{order_id}/history'], 'scope':'current_brand_and_assigned_branches_operational_projection'},
    'driver_hours_reports': {'methods':['GET'], 'paths':['/driver-hours','/api/driver-work-hours/report','/api/driver-work-hours/export.xlsx','/api/driver-work-hours/export.pdf'], 'scope':'current_brand_and_assigned_branches_reports_only'},
    'branch_availability': {'methods':['POST'], 'paths':['/api/branches/{branch_id}/products/{product_id}/availability'], 'scope':'current_brand_and_assigned_branches_stock_only'},
    'branch_printers_manage': {'methods':['GET','POST'], 'paths':['/api/printing/devices','/api/printing/setup','/api/printing/devices/{identifier}/revoke','/api/printing/devices/{identifier}/test','/api/printing/devices/{identifier}/approve','/api/printing/jobs','/api/printing/windows-bridge.zip','/api/printing/migration-status'], 'scope':'current_brand_and_assigned_branches_printer_management_only'},
}
EXTRA_API_CONTRACTS.update({
    'shifts_view': {'methods': ['GET'], 'paths': ['/api/shifts', '/api/shifts/current', '/api/shifts/{sid}', '/api/shifts/{sid}/draft', '/api/manager-handovers'], 'scope': 'current_brand_and_all_shift_branches_read_only'},
    'shifts_open': {'methods': ['POST'], 'paths': ['/api/shifts'], 'scope': 'open_for_existing_eligible_staff_in_assigned_branches_no_opening_cash_override'},
    'shifts_close': {'methods': ['POST'], 'paths': ['/api/shifts/{sid}/closing', '/api/shifts/{sid}/submit'], 'scope': 'scoped_manager_closure_no_receipt_or_approval_override'},
    'shifts_approve': {'methods': ['POST'], 'paths': ['/api/shifts/{sid}/approve', '/api/shifts/{sid}/return'], 'scope': 'all_shift_branches_existing_receipt_and_financial_guards'},
    'shifts_delegate_close': {'methods': ['POST'], 'paths': ['/api/shifts/{sid}/closing', '/api/shifts/{sid}/submit'], 'scope': 'scoped_driver_submission_with_reason_no_receipt_or_approval_override'},
    'shifts_handover': {'methods': ['POST'], 'paths': ['/api/shifts/{sid}/handover-manager'], 'scope': 'scoped_manager_handover_request_actual_recipient_must_accept'},
})


def grantable(role):
    return {code for code, roles in EXTRA_GRANT_ROLES.items() if role in roles}

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
    if user.get('_morning_manager_context'):
        from .morning_manager_context import capabilities
        return set(capabilities(user))
    role = user.get('role')
    if role == 'owner':
        return eligible(role)
    base = set(user.get('permissions') or []) & ADMIN if role == 'admin' else eligible(role)
    routed = (set(user.get('_message_capabilities') or []) & {'chat_view', 'chat_reply', 'chat_manage'}
              if role in {'admin', 'delivery_manager', 'delivery_staff'} else set())
    return (base | routed | (set(user.get('extra_permissions') or []) & grantable(role))) - set(user.get('permission_denials') or [])


def catalog():
    return [{'code': code, 'name_ar': ar, 'name_en': en, 'group': group,
             'group_ar': GROUPS[group][0], 'group_en': GROUPS[group][1],
             'roles': sorted(roles), 'default_roles': sorted(roles - {'admin'}),
             'delegable_roles': sorted(EXTRA_GRANT_ROLES.get(code, set())),
             'owner_approval_required': True,
             'delegation_status': 'scoped' if code in EXTRA_GRANT_ROLES else 'role_policy_required',
             'api_contract': EXTRA_API_CONTRACTS.get(code)}
            for code, ar, en, group, roles in DEFINITIONS]


# Resolved endpoint -> required capability. Existing endpoint policies still run.
ROUTES = {
    'order_financial_cases': {'nonreceipt':'payments_collect','free_item_return':'support_manage','recovery_context':'accounting_review','recover':'accounting_review','refund_context':'accounting_review','cancel':'orders_cancel','journey':{'driver':'tasks_view','delivery_manager':'orders_view','branch_manager':'orders_view','admin':'accounting_review'},'inbox':('orders_view','accounting_review'),'resolve':'accounting_review'},
    'task_recovery': {'listing':'tasks_resolve_delivery', 'detail':'tasks_resolve_delivery', 'review_revision':'tasks_resolve_delivery'},
    'driver_float': {'listing':('drivers_view','accounting_review'), 'propose':'custody_transfer', 'decide':'accounting_review'},
    'order_amendments': {'inbox':'orders_view', 'detail':'orders_view', 'preview':'orders_amend', 'request_amendment':'orders_amend', 'decide':'orders_amend',
                         'acknowledge':{'branch_manager':'orders_prepare','driver':'tasks_update','delivery_manager':'orders_amend'}},
    'driver_incentives': {'achievement': ('shifts_view','accounting_reports'), 'shift_review': ('shifts_view','accounting_reports'), 'decide': 'shifts_approve'},
    'operations': {
        'dashboard': 'dashboard_view', 'operational_alerts': 'operational_alerts',
        'open_shift': 'shifts_open', 'shifts': 'shifts_view', 'shift_detail': 'shifts_view', 'save_external_enbishan': 'shifts_delegate_close',
        'people': ('custody_view', 'drivers_view', 'delivery_assign', 'shifts_view', 'shifts_open', 'shifts_close', 'shifts_delegate_close', 'shifts_handover'), 'driver_roster': 'drivers_view',
        'refuel_shift': 'vehicle_refuel', 'request_close': 'shifts_close',
        'submit_close': 'shifts_close', 'return_close': 'shifts_approve', 'approve_close': 'shifts_approve', 'review_driver_amounts':'shifts_approve',
        'assign': 'delivery_assign', 'tasks': 'tasks_view', 'task_transition': 'tasks_update', 'resolve_delivery': 'tasks_resolve_delivery',
        'task_handover': 'tasks_transfer', 'accept_task_handover': 'tasks_accept_transfer',
        'collect': 'payments_collect', 'refund': {'delivery_manager':'refunds_manage','branch_manager':'payments_collect','admin':'accounting_review'},
        'get_payment_terminal': 'driver_terminal_view', 'set_payment_terminal': 'driver_terminal',
        'custody_transfer': 'custody_transfer', 'custody_list': 'custody_view', 'confirm_custody': 'custody_transfer',
        'simple_driver_close': {'driver': 'shifts_close', 'delivery_manager': 'shifts_delegate_close'},
        'manager_handover': 'shifts_handover', 'manager_handover_list': 'shifts_view',
        'accept_manager_handover': 'shifts_handover', 'closure_draft': 'shifts_view',
        'save_closure_draft': 'shifts_close', 'set_shift_availability': 'drivers_availability',
    },
    'orders': {'create_delivery_center_order': 'orders_create_customer', 'quote_delivery_center_order': 'orders_create_customer', 'create_complimentary':'orders_create_customer', 'quote_complimentary':('orders_create_customer','complimentary_approve'), 'authorize_complimentary':'complimentary_approve', 'revoke_complimentary_authorization':'complimentary_approve', 'list_complimentary_authorizations':('orders_create_customer','complimentary_approve'), 'list_orders': 'orders_view', 'get_order': 'orders_view',
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
                      'upload_photo': 'task_photos', 'photos': 'orders_view', 'photo': 'orders_view',
                      'read_evidence_observation':'orders_view'},
    'auth': {'staff_list': 'drivers_view', 'invite_staff': 'drivers_manage',
             'update_staff': 'drivers_manage', 'resend_invitation': 'drivers_manage',
             'revoke_invitation': 'drivers_manage', 'register_driver_vehicle': 'vehicle_register'},
    'catalog': {'nearest_branches': 'delivery_assign',
                'branch_product_availability': ('branch_availability', 'products_manage')},
    'maintenance': {name: {'delivery_manager':'support_manage','driver': 'vehicle_faults_report', 'admin': 'vehicle_maintenance_manage'}
                    for name in ('options', 'list_reports', 'report_detail', 'create_report', 'update_report')},
    'fuel_exceptions': {'request_exception':'shifts_open','confirm_ready':'vehicle_refuel','listing':{'delivery_manager':'shifts_approve'},'decide':'shifts_approve'},
    'driver_breaks': {'request_break':'shifts_open','configure':'drivers_availability','listing':{'delivery_manager':'drivers_availability'},'change':{'delivery_manager':'drivers_availability'}},
    'smart_dispatch': {'offer':'delivery_assign','auto_assign':'delivery_assign','expire':'delivery_assign','decide':'tasks_update','inbox':'tasks_view'},
    'fleet': {name: 'vehicle_maintenance_manage' for name in (
        'vehicles', 'options', 'report', 'vehicle', 'export', 'detail', 'correct')},
    'branch_printing': {name: 'branch_printers_manage' for name in (
        'pair_device', 'devices', 'guided_setup', 'revoke', 'test_printer', 'approve_printer',
        'jobs', 'download_bridge', 'migration_status')},
    'accounting': {name: {'delivery_manager': 'accounting_cases_followup'} for name in (
        'options', 'cases', 'detail', 'followup', 'upload', 'download')},
}
ROUTES['branch_printing'].update({'enqueue': 'orders_print', 'resolve_print_alert': 'orders_print', 'operational_devices': 'orders_print'})
ROUTES['branch_printing'].update({name:{'delivery_manager':'orders_print','branch_manager':'orders_prepare'}
                                for name in ('order_output_status','confirm_order_output')})


ROUTES['aggregate_driver_closure'] = {'detail':('shifts_view','accounting_review'),'submit':{'driver':'shifts_close','delivery_manager':'shifts_delegate_close'},'receive':'shifts_delegate_close','return_declaration':'shifts_approve','allocate':'accounting_review','inbox':'accounting_review'}
ROUTES['driver_duty'] = {'listing':'driver_duty_manage','configure':'driver_duty_manage'}
ROUTES['driver_support'] = {'list_requests': ('shifts_view','driver_support_approve'), 'request_support': 'shifts_handover', 'accept_support': 'driver_support_approve', 'resolve_request': ('shifts_handover','driver_support_approve')}

def required_for(endpoint, role, *, user=None):
    if role in {'owner', 'customer'} or endpoint is None:
        return None
    module = endpoint.__module__.rsplit('.', 1)[-1]
    if (role == 'delivery_manager' and user and user.get('_morning_manager_context')
            and module == 'orders' and endpoint.__name__ == 'preparation'):
        return 'orders_prepare'
    if role == 'admin' and module == 'operations' and endpoint.__name__ in ('request_close', 'submit_close'):
        return ('shifts_close', 'shifts_delegate_close')
    value = ROUTES.get(module, {}).get(endpoint.__name__)
    return value.get(role) if isinstance(value, dict) else value

