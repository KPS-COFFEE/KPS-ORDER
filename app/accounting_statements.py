"""Driver-owned accounting statements and immutable manager-day approvals."""
from collections import defaultdict
from datetime import datetime, timedelta, time

from . import db


def filter_statement(data, driver_ids='', variance='all'):
    """Filter an authorized statement without netting drivers or changing its source."""
    selected = {value.strip() for value in driver_ids.split(',') if value.strip()}
    if len(selected) > 250 or any(len(value) > 150 for value in selected):
        raise db.error(422, 'STATEMENT_FILTER', 'اختر عددًا صالحًا من السائقين.')
    predicates = {
        'all': lambda row: True,
        'shortage_any': lambda row: row.get('shortage', 0) > 0 or row.get('pending_shortage', 0) > 0,
        'shortage': lambda row: row.get('shortage', 0) > 0,
        'pending': lambda row: row.get('pending_shortage', 0) > 0,
        'outstanding': lambda row: row.get('outstanding_receivable_halalas', 0) > 0,
    }
    if variance not in predicates:
        raise db.error(422, 'STATEMENT_FILTER', 'فلتر العجز غير صالح.')
    rows = [row for row in data['rows'] if (not selected or row['driver_id'] in selected) and predicates[variance](row)]
    pairs = {(str(row['driver_id']), str(row['day'])) for row in rows}
    drivers = []
    for driver in data.get('drivers', []):
        entries = [row for row in rows if row['driver_id'] == driver['driver_id']]
        if entries:
            drivers.append({key: sum(row.get(key, 0) for row in entries) if type(value) is int else value
                            for key, value in driver.items()})
    result = dict(data, rows=rows, drivers=drivers,
                  applied_filters={'driver_ids': sorted(selected), 'variance': variance})
    for key in ('settlements', 'adjustment_history', 'incidents'):
        result[key] = [row for row in data.get(key, []) if (str(row['driver_id']), str(row['day'])) in pairs]
    report_ids = {row['id'] for row in result['settlements']}
    result['deadline_extensions'] = [row for row in data.get('deadline_extensions', []) if row['entity_id'] in report_ids]
    return result


def source_rows(c,user,bids,ds=None,de=None,manager_shift_id='',manager_shift_ids=None):
    q="""SELECT r.*,s.manager_shift_id,s.submission,s.scheduled_end_at,s.delivery_window_snapshot,
      s.closed_at shift_closed_at,s.shift_no,mu.display_name manager_name,
      u.display_name driver_name,
      coalesce(m.reporting_operational_date,s.reporting_operational_date,s.delivery_operational_date,(s.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '4 hours')::date) AS "day"
      FROM closure_reports r JOIN shifts s ON s.id=r.shift_id JOIN users u ON u.id=r.user_id
      JOIN shifts m ON m.id=s.manager_shift_id JOIN users mu ON mu.id=m.user_id
      WHERE r.brand_id=%s AND r.kind='driver' AND (m.status='closed' OR to_jsonb(m)->>'staffing_ended_at' IS NOT NULL) AND r.branch_ids ?| %s"""
    args=[user['brand_id'],bids]
    if ds:
        q+=" AND coalesce(m.reporting_operational_date,s.reporting_operational_date,s.delivery_operational_date,(s.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '4 hours')::date) BETWEEN %s AND %s"
        args.extend([ds,de])
    if manager_shift_id:q+=' AND s.manager_shift_id=%s';args.append(manager_shift_id)
    if manager_shift_ids is not None:q+=' AND s.manager_shift_id=ANY(%s)';args.append(manager_shift_ids)
    rows=c.execute(q+' ORDER BY "day",r.created_at,r.id',args).fetchall()
    allowed=set(user.get('branch_ids',[]))
    return [r for r in rows if (user['role']=='owner' and not user.get('home_brand_id')) or set(r['branch_ids'])<=allowed]


def histories(c,user,rows):
    from .accounting import settlement_action
    ids=[r['id'] for r in rows]
    snapshots={r['id']:r['snapshot'] for r in rows}
    changes=defaultdict(list)
    for event in c.execute("""SELECT e.*,u.display_name actor_name FROM driver_accounting_settlements e
      JOIN users u ON u.id=e.actor_id WHERE e.brand_id=%s AND e.report_id=ANY(%s) ORDER BY e.report_id,e.revision""",
      (user['brand_id'],ids)).fetchall():
        history=changes[event['report_id']]
        history.append(dict(event,action_type=settlement_action(snapshots[event['report_id']],history[-1] if history else None,event)))
    return changes


def settlement_audit(c,user,rows,changes):
    from .accounting import driver_settlement_values, settlement_action
    result=[]
    manager_events=defaultdict(list)
    for e in c.execute("""SELECT a.*,u.display_name actor FROM audit_events a JOIN users u ON u.id=a.actor_id
      WHERE a.action='shift.manager_amount_review' AND a.entity_id=ANY(%s) ORDER BY a.created_at,a.id""",
      ([r['shift_id'] for r in rows],)).fetchall():manager_events[e['entity_id']].append(e)
    for r in rows:
        for e in manager_events[r['shift_id']]:
            details=e['details'];before=details.get('before',{});after=details.get('after',{})
            result.append(dict(report_id=r['id'],driver_id=r['user_id'],driver=r['driver_name'],day=r['day'],
                actor=e['actor'],at=e['created_at'],reason=details.get('reason',''),action='manager_correction',
                before_bank=before.get('terminal_total_halalas',0),after_bank=after.get('terminal_total_halalas',0),
                before_cash=before.get('handover_halalas',0),after_cash=after.get('handover_halalas',0),
                before_received=None,after_received=None))
        previous=None
        for e in changes[r['id']]:
            before=driver_settlement_values(r['snapshot'],previous);after=driver_settlement_values(r['snapshot'],e)
            result.append(dict(report_id=r['id'],driver_id=r['user_id'],driver=r['driver_name'],day=r['day'],
                actor=e['actor_name'],at=e['created_at'],reason=e['note'],action=settlement_action(r['snapshot'],previous,e),
                before_bank=before['bank_halalas'],after_bank=after['bank_halalas'],
                before_cash=before['cash_due_halalas'],after_cash=after['cash_due_halalas'],
                before_received=before['cash_received_halalas'],after_received=after['cash_received_halalas']))
            previous=e
    return result


def statement(c,user,ds,de,bids,bs,driver_id='',lang='ar',manager_shift_id=''):
    from .accounting import driver_settlement_values,TZ,KINDS,RESP
    from .operations import safe
    en=lang=='en'
    rows=source_rows(c,user,bids,ds,de,manager_shift_id)
    if driver_id:rows=[r for r in rows if r['user_id']==driver_id]
    changes=histories(c,user,rows)
    approvals={r['manager_shift_id']:r['id'] for r in c.execute('SELECT id,manager_shift_id FROM accounting_daily_closures WHERE brand_id=%s AND manager_shift_id=ANY(%s)',(user['brand_id'],list({r['manager_shift_id'] for r in rows}))).fetchall()}
    cases=c.execute("""SELECT a.*,u.display_name driver_name FROM accounting_cases a
      JOIN users u ON u.id=a.subject_id WHERE a.brand_id=%s AND a.branch_id=ANY(%s)
      AND a.occurred_on BETWEEN %s AND %s AND u.role='driver' ORDER BY a.occurred_on,a.case_no""",
      (user['brand_id'],bids,ds,de)).fetchall()
    if driver_id:cases=[r for r in cases if r['subject_id']==driver_id]
    if manager_shift_id:
        subjects={r['user_id'] for r in rows}
        cases=[r for r in cases if r['subject_id'] in subjects]
    names={r['user_id']:r['driver_name'] for r in rows}
    names.update({r['subject_id']:r['driver_name'] for r in cases})
    if driver_id and driver_id not in names:
        from .accounting import person
        names[driver_id]=person(c,user,driver_id,bids)['display_name']
    daily={}
    for identifier,name in names.items():
        for n in range((de-ds).days+1):
            day=ds+timedelta(days=n)
            daily[identifier,day]=dict(driver_id=identifier,driver=name,day=day,label=name,
                completed_orders=0,bank=0,cash_due=0,cash_received=0,pending_cash=0,shortage=0,surplus=0,pending_shortage=0,pending_surplus=0,
                violations=0,traffic=0,notes='',incident_notes='',traffic_notes='',adjustments='',
                pending_balance=0,pending_receipt=0,closure_count=0,manager='',status=('No recorded closure' if en else 'لا توجد تقفيلة مسجلة'),
                outstanding_receivable_halalas=0,outstanding_payable_halalas=0,unresolved_channel_count=0,approved_closure_count=0,approval_references='')
            daily[identifier,day].update(system_total=0,refund_cash=0,refund_card=0,shift_references='',branch_names='')
    # Delivered order IDs are selected once across all tasks, avoiding duplicate
    # counts when orders were reassigned or split across collection records.
    task_ids=defaultdict(set)
    for task in c.execute("""SELECT DISTINCT ON (t.order_id) t.order_id,t.shift_id FROM effective_driver_tasks t
      JOIN orders o ON o.id=t.order_id WHERE o.brand_id=%s AND o.test_void IS NULL AND t.status='delivered'
      AND t.shift_id=ANY(%s) ORDER BY t.order_id,t.delivered_at DESC NULLS LAST,t.id DESC""",
      (user['brand_id'],[r['shift_id'] for r in rows])).fetchall():task_ids[task['shift_id']].add(task['order_id'])
    from .accounting import variance_confirmation
    details=[]
    for r in rows:
        history=changes[r['id']]
        from .financial_treatments import reviewed_values
        v=reviewed_values(c,r,history[-1] if history else None,user=user)
        item=daily[r['user_id'],r['day']]
        item['system_total']+=v['system_total_halalas']
        summary=r['snapshot'].get('summary') or {}
        item['refund_cash']+=summary.get('cash_refunded_halalas',sum(x.get('amount_halalas',0) for x in r['snapshot'].get('refunds',[]) if x.get('method')=='cash'))
        item['refund_card']+=summary.get('card_refunded_halalas',sum(x.get('amount_halalas',0) for x in r['snapshot'].get('refunds',[]) if x.get('method')=='card'))
        item['shift_references']+=(' / ' if item['shift_references'] else '')+('D-'+str(r['shift_no']).zfill(6) if r.get('shift_no') is not None else r['shift_id'])
        branch_names={b['id']:b['name'] for b in bs}
        item['branch_names']=' / '.join(sorted(set(item['branch_names'].split(' / ') if item['branch_names'] else []) | {branch_names.get(b,b) for b in r['branch_ids']}))
        item['completed_orders']+=len(task_ids[r['shift_id']])
        item['bank']+=v['bank_halalas'] if v['balance_approved'] else 0
        item['cash_due']+=v['cash_due_halalas']
        item['cash_received']+=v['cash_received_halalas'] or 0
        item['pending_cash']+=v['pending_cash_halalas']
        item['outstanding_receivable_halalas']+=v['outstanding_receivable_halalas']
        item['outstanding_payable_halalas']+=v['outstanding_payable_halalas']
        item['unresolved_channel_count']+=int(v['has_channel_variance'] and not v['period_approval_allowed'])
        confirmation=variance_confirmation(c,r['id'],v['revision']) if v['reconciliation_complete'] and v['net_difference_halalas'] else None
        pending=bool(v['has_channel_variance'] and not v['period_approval_allowed'])
        item['pending_shortage' if pending else 'shortage']+=v['shortage_halalas']
        item['pending_surplus' if pending else 'surplus']+=v['surplus_halalas']
        treatment=v.get('financial_treatment')
        if treatment:
            approved=treatment.get('approval') or {}
            item['adjustments']+=f"{treatment.get('requester_name','')} → {approved.get('approver_name','')} · {treatment['payload'].get('note','')} · {approved.get('evidence_reference','')}\n"
            if v['outstanding_receivable_halalas'] or v['outstanding_payable_halalas']:
                item['adjustments']+=f"{'Outstanding under follow-up' if en else 'مبلغ مستحق تحت المتابعة'} · {treatment['payload'].get('liable_party_name','')} · {treatment['payload'].get('followup_user_id','')} · {treatment['payload'].get('due_on','')}\n"
        if confirmation:
            item['adjustments']+=f"{confirmation['actor_name']} · {confirmation['created_at'].astimezone(TZ).strftime('%Y/%m/%d %H:%M')} · {confirmation['details'].get('action_taken','')} · {confirmation['details'].get('reason','')}\n"
        item['pending_balance']+=int(not v['balance_approved']);item['pending_receipt']+=int(not v['cash_recorded'])
        item['closure_count']+=1
        if r['manager_shift_id'] in approvals:
            item['approved_closure_count']+=1
            reference=approvals[r['manager_shift_id']]
            if reference not in item['approval_references'].split(' / '):item['approval_references']+=(' / ' if item['approval_references'] else '')+reference
        item['manager']+=(' / ' if item['manager'] else '')+r['manager_name']
        notes=[(r['snapshot'].get('shift',{}).get('submission') or {}).get('notes','')]
        notes.extend(e['note'] for e in history)
        item['notes']+='\n'.join(x for x in notes if x)+'\n'
        details.append(dict(id=r['id'],shift_id=r['shift_id'],manager_shift_id=r['manager_shift_id'],
            driver_id=r['user_id'],driver=r['driver_name'],day=r['day'],variance_pending=pending,variance_confirmation=confirmation,**v))
    incidents=[]
    for r in cases:
        item=daily[r['subject_id'],r['occurred_on']]
        amount=r['amount_halalas'];kind=KINDS[r['category']][en];responsibility=RESP[r['responsibility']][en]
        description=f"#{r['case_no']} · {kind} · {r['description']} · {amount/100:.2f} · {responsibility}"
        if r['category']=='traffic':item['traffic']+=amount;item['traffic_notes']+=description+'\n'
        else:item['violations']+=amount;item['incident_notes']+=description+'\n'
        incidents.append(dict(day=r['occurred_on'],driver_id=r['subject_id'],driver=r['driver_name'],
            kind=kind,amount=amount,reason=r['description'],responsibility=responsibility,action=r['action']))
    audit=settlement_audit(c,user,rows,changes)
    deadline_extensions=c.execute("SELECT a.*,u.display_name actor_name FROM audit_events a JOIN users u ON u.id=a.actor_id WHERE a.action='accounting.deadline.extended' AND a.entity_id=ANY(%s) ORDER BY a.created_at",([r['id'] for r in rows],)).fetchall()
    by_report={r['id']:r for r in rows}
    for event in deadline_extensions:
        row=by_report[event['entity_id']];extension_details=event['details']
        daily[row['user_id'],row['day']]['adjustments']+=f"{event['actor_name']} · +48h · {extension_details['original_due_at']} → {extension_details['due_at']} · {extension_details['reason']}\n"
    for e in audit:
        before='before' if en else 'قبل';after='after' if en else 'بعد'
        action={'cash_receipt':('استلام نقد فعلي','Actual cash receipt'),'cash_receipt_correction':('تصحيح الكاش المستلم — المالك','Cash receipt corrected — owner'),'correction':('تعديل المبالغ واعتمادها','Amounts corrected and approved'),'approval':('اعتماد دون تعديل','Approved unchanged'),'manager_correction':('تصحيح مدير التوصيل','Delivery manager correction')}.get(e['action'],('مراجعة','Review'))[en]
        daily[e['driver_id'],e['day']]['adjustments']+=f"{action} · {e['actor']} · {e['at'].astimezone(TZ).strftime('%Y/%m/%d %H:%M')} · {e['reason']}\n" + \
            f"{'Card' if en else 'الشبكة'}: {before} {e['before_bank']/100:.2f} / {after} {e['after_bank']/100:.2f}; " + \
            f"{'Cash due' if en else 'الكاش المطلوب'}: {before} {e['before_cash']/100:.2f} / {after} {e['after_cash']/100:.2f}; " + \
            f"{'Received' if en else 'المستلم'}: {before} {('—' if e['before_received'] is None else format(e['before_received']/100,'.2f'))} / {after} {('—' if e['after_received'] is None else format(e['after_received']/100,'.2f'))}\n"
    for item in daily.values():
        item['approval_status']=(('No recorded closure' if en else 'لا توجد تقفيلة مسجلة') if not item['closure_count'] else ('Manager-day approval saved' if en else 'اعتماد يوم المدير محفوظ') if item['approved_closure_count']==item['closure_count'] else ('Some manager days await approval' if en else 'بعض أيام المدير بانتظار الاعتماد') if item['approved_closure_count'] else ('Awaiting manager-day accounting approval' if en else 'بانتظار اعتماد المحاسبة ليوم المدير'))
        if item['closure_count']:
            item['status']=('Review approved — outstanding under follow-up' if en else 'المراجعة معتمدة — مبلغ مستحق تحت المتابعة') if item['outstanding_receivable_halalas'] or item['outstanding_payable_halalas'] else ('Channel difference under review' if en else 'فرق الكاش والشبكة تحت المراجعة') if item['unresolved_channel_count'] else ('Pending balance' if en else 'بانتظار الموازنة') if item['pending_balance'] else ('Pending cash' if en else 'بانتظار الكاش') if item['pending_receipt'] else ('Variance pending confirmation' if en else 'فرق بانتظار تأكيد المحاسب') if item['pending_shortage'] or item['pending_surplus'] else ('Cash and recorded-card review approved' if en else 'مراجعة الكاش والشبكة المسجلة معتمدة')
    output=sorted(daily.values(),key=lambda r:(r['driver'],r['driver_id'],r['day']))
    summaries=[]
    money=('completed_orders','system_total','refund_cash','refund_card','bank','cash_due','cash_received','pending_cash','shortage','surplus','pending_shortage','pending_surplus','violations','traffic','outstanding_receivable_halalas','outstanding_payable_halalas')
    for identifier,name in names.items():
        entries=[r for r in output if r['driver_id']==identifier]
        summaries.append(dict(driver_id=identifier,driver=name,**{k:sum(r[k] for r in entries) for k in money}))
    return safe(dict(group='driver_statement',rows=output,drivers=summaries,settlements=details,adjustment_history=audit,deadline_extensions=deadline_extensions,
        incidents=incidents,branches=bs,period={'start':ds,'end':de,'timezone':'Asia/Riyadh'},generated_at=datetime.now(TZ),
        definitions=('Each delivered order counts once. Balance papers and physical cash are approved independently. Waiting cash is not a shortage. Cash and card differences remain separate. Notes never settle money or assign employee liability. Independent documented treatments preserve outstanding receivables and payables until actual recovery or payment. Incident values show recorded amounts and responsibility, without payroll deductions. This is closure cash and recorded-card review only; recorded card collection does not establish receipt at the bank. Bank matching remains a separate evidence scope.' if en else
        'كل طلب مسلّم يُعد مرة واحدة. اعتماد ورقة الموازنة مستقل عن استلام الكاش. النقد المنتظر ليس عجزًا. فرق الكاش مستقل عن فرق الشبكة. الملاحظة لا تسوي المبلغ ولا تحمل الموظف مسؤولية. المعالجة المستقلة الموثقة تبقي المستحقات ظاهرة حتى التحصيل أو الرد الفعلي. قيم المخالفات مبالغ مسجلة مع حالة المسؤولية ولا تنشئ خصمًا من الراتب. نطاق هذا الكشف مراجعة الكاش والشبكة المسجلة فقط؛ تحصيل الشبكة المسجل لا يثبت وصوله للبنك، ومطابقة البنك نطاق مستقل بالأدلة.')))


def closure_deadline(row, local, extension=None):
    """Use the frozen shift window, never the date a paper reaches accounting."""
    from .accounting import TZ
    window=row.get('delivery_window_snapshot') or {}
    morning=window.get('period')=='morning'
    ends=[datetime.fromisoformat(w['window_end']) for bid,w in window.get('branches',{}).items()
          if bid in row['branch_ids'] and w.get('window_end')]
    end=max(ends) if ends else row.get('scheduled_end_at')
    if end is None:
        end=row.get('shift_closed_at') if morning else datetime.combine(row['day']+timedelta(days=1),time(4),TZ)
    if end is None:
        raise ValueError('Morning closure has no recorded shift end')
    inside=bool(set(row['branch_ids'])&local)
    policy='morning_24h' if morning else 'local_16h' if inside else 'external_72h'
    due=(end+timedelta(hours=24) if morning else
         datetime.combine(row['day']+timedelta(days=1),time(16),TZ) if inside else end+timedelta(hours=72))
    original=due
    if extension:due=datetime.fromisoformat(extension['details']['due_at'])
    return dict(shift_end_at=end,deadline_policy=policy,original_due_at=original,due_at=due,extension=extension)


def manager_cards(c,user,bids,manager_shift_ids=None,*,page_size=None,offset=0):
    from .accounting import driver_settlement_values,variance_confirmation,cash_correction_requests,can_request_cash_correction,TZ
    from .catalog import normalize_city
    from .operations import safe
    groups={}
    branch_rows=c.execute('SELECT id,name,city FROM branches WHERE brand_id=%s',(user['brand_id'],)).fetchall()
    branch_names={b['id']:b['name'] for b in branch_rows}
    local={b['id'] for b in branch_rows if normalize_city(b['city'])=='حفر الباطن'}
    approved={r['manager_shift_id']:r for r in c.execute('SELECT id,manager_shift_id,created_at FROM accounting_daily_closures WHERE brand_id=%s',(user['brand_id'],)).fetchall()}
    # Physical completion enters accounting immediately. A manager awaiting bag
    # allocation must remain visible even without a final financial snapshot.
    q="""SELECT m.id,m.status,(to_jsonb(m)->>'staffing_ended_at')::timestamptz staffing_ended_at,m.shift_no,m.delivery_window_snapshot,
        m.branch_ids,m.opened_at,m.closed_at,m.scheduled_end_at,u.display_name manager_name,
        coalesce(m.reporting_operational_date,m.delivery_operational_date,(m.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '4 hours')::date) AS "day",
        (SELECT count(*) FROM shifts child WHERE child.manager_shift_id=m.id AND child.kind='driver') expected_driver_count,
        (SELECT r.id FROM closure_reports r WHERE r.shift_id=m.id AND r.kind='manager' ORDER BY r.version DESC,r.created_at DESC LIMIT 1) manager_report_id
        FROM shifts m JOIN users u ON u.id=m.user_id
        WHERE m.kind='manager' AND m.brand_id=%s AND (m.status='closed' OR to_jsonb(m)->>'staffing_ended_at' IS NOT NULL)
        AND m.branch_ids ?| %s"""
    args=[user['brand_id'],bids]
    if manager_shift_ids is not None:q+=' AND m.id=ANY(%s)';args.append(manager_shift_ids)
    if user['role']!='owner' or user.get('home_brand_id'):
        q+=' AND m.branch_ids <@ %s::jsonb';args.append(db.json(user.get('branch_ids',[])))
    q+=' ORDER BY EXISTS(SELECT 1 FROM accounting_daily_closures a WHERE a.manager_shift_id=m.id),"day" DESC,m.id'
    if page_size is not None:q+=' LIMIT %s OFFSET %s';args.extend([page_size+1,offset])
    manager_rows=c.execute(q,args).fetchall()
    has_more=page_size is not None and len(manager_rows)>page_size
    if page_size is not None:manager_rows=manager_rows[:page_size]
    for manager in manager_rows:
        if (user['role']!='owner' or user.get('home_brand_id')) and not set(manager['branch_ids'])<=set(user.get('branch_ids',[])):continue
        groups[manager['id']]=dict(id=manager['id'],manager_name=manager['manager_name'],day=manager['day'],
            period=(manager['delivery_window_snapshot'] or {}).get('period','evening'),
            shift_no=manager['shift_no'],manager_report_id=manager['manager_report_id'],
            opened_at=manager['opened_at'],closed_at=manager['closed_at'] or manager['staffing_ended_at'],
            scheduled_end_at=manager['scheduled_end_at'],branch_ids=manager['branch_ids'],
            branch_names=[branch_names.get(b,b) for b in manager['branch_ids']],
            operationally_closed=True,financial_ledger_closed=manager['status']=='closed',
            expected_driver_count=manager['expected_driver_count'],drivers=[],approved_day=approved.get(manager['id']),due_on=None)
    rows=source_rows(c,user,bids,manager_shift_ids=list(groups))
    changes=histories(c,user,rows)
    extensions={r['entity_id']:r for r in c.execute("SELECT a.*,u.display_name actor_name FROM audit_events a JOIN users u ON u.id=a.actor_id WHERE a.action='accounting.deadline.extended' AND a.entity_id=ANY(%s)",([r['id'] for r in rows],)).fetchall()}
    for r in rows:
        history=changes[r['id']]
        from .financial_treatments import reviewed_values
        v=reviewed_values(c,r,history[-1] if history else None,user=user)
        confirmation=variance_confirmation(c,r['id'],v['revision']) if v['reconciliation_complete'] and v['net_difference_halalas'] else None
        variance_pending=bool(v['has_channel_variance'] and not v['period_approval_allowed'])
        deadline=closure_deadline(r,local,extensions.get(r['id']))
        finished=history[-1]['created_at'] if v['stage']=='cash_received' else None
        deadline['overdue']=bool((finished or datetime.now(TZ))>=deadline['due_at'])
        deadline['can_extend']=deadline['deadline_policy']=='external_72h' and not deadline['extension'] and not finished
        group=groups.setdefault(r['manager_shift_id'],dict(id=r['manager_shift_id'],manager_name=r['manager_name'],day=r['day'],drivers=[],approved_day=approved.get(r['manager_shift_id']),due_on=None))
        tickets=cash_correction_requests(c,user,r['id'])
        may_request=can_request_cash_correction(c,user,r['id'])
        group['drivers'].append(dict(id=r['id'],shift_id=r['shift_id'],completed_orders=(r['snapshot'].get('summary') or {}).get('completed_orders',0),user_id=r['user_id'],driver_name=r['driver_name'],day=r['day'],history=history,variance_pending=variance_pending,variance_confirmation=confirmation,cash_correction_requests=tickets,can_request_cash_correction=may_request,**v,**deadline))
    # Show the precise missing driver record and its owner, rather than an
    # unexplained disabled day-approval button. Match by manager id, never clock date.
    children=defaultdict(list)
    for child in c.execute("""SELECT s.id,s.manager_shift_id,s.status,u.display_name driver_name
      FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.kind='driver' AND s.brand_id=%s
      AND s.manager_shift_id=ANY(%s) ORDER BY s.opened_at,s.id""",(user['brand_id'],list(groups))).fetchall():
        children[child['manager_shift_id']].append(child)
    for group in groups.values():
        reported={r['shift_id'] for r in group['drivers']}
        group['pending_drivers']=[dict(child,reason='driver_shift_open' if child['status']!='closed' else 'driver_report_missing',
            responsible='delivery_manager') for child in children[group['id']] if child['status']!='closed' or child['id'] not in reported]
        group['completed']=sum(r['financially_complete'] for r in group['drivers'])
        group['review_completed']=sum(r['period_approval_allowed'] for r in group['drivers'])
        group['outstanding_receivable_halalas']=sum(r['outstanding_receivable_halalas'] for r in group['drivers'])
        group['outstanding_payable_halalas']=sum(r['outstanding_payable_halalas'] for r in group['drivers'])
        group['total']=len(group['drivers'])
        group['pending_driver_count']=max(0,group.get('expected_driver_count',group['total'])-group['total'])
        group['ready']=bool(group['total']) and not group['pending_drivers'] and not group['pending_driver_count'] and group.get('financial_ledger_closed',True) and group['review_completed']==group['total']
        group['balance_completed']=sum(r['balance_approved'] for r in group['drivers'])
        group['cash_completed']=sum(r['cash_recorded'] for r in group['drivers'])
        variance_count=sum(bool(r['has_channel_variance']) for r in group['drivers'])
        confirmed_count=sum(bool(r['financial_treatment'] and r['financial_treatment']['status']=='approved' and r['period_approval_allowed']) for r in group['drivers'])
        group['completion_percent']=int(100*(group['balance_completed']+group['cash_completed']+confirmed_count)/(2*group['total']+variance_count)) if group['total'] else 0
        if group['pending_drivers'] or group['pending_driver_count'] or not group.get('financial_ledger_closed',True):group['completion_percent']=min(99,group['completion_percent'])
        group['remaining_count']=max(len(group['pending_drivers']),max(group.get('expected_driver_count',group['total']),group['total'])-group['review_completed'])
        group['review_needed_count']=sum(r['variance_pending'] for r in group['drivers'])
        group['blockers']=[]
        if not group.get('financial_ledger_closed',True):group['blockers'].append(dict(reason='manager_allocation_pending',responsible='delivery_manager'))
        if group['pending_drivers']:group['blockers'].append(dict(reason='driver_records_pending',responsible='delivery_manager'))
        if group['balance_completed']<group['total']:group['blockers'].append(dict(reason='balance_approval_pending',responsible='accounting'))
        if group['cash_completed']<group['total']:group['blockers'].append(dict(reason='cash_receipt_pending',responsible='accounting'))
        if group['review_needed_count']:group['blockers'].append(dict(reason='variance_treatment_pending',responsible='accounting'))
        from .financial_integrity import inspect_snapshot
        if any(not inspect_snapshot(r['snapshot'])['verified'] for r in rows if r['manager_shift_id']==group['id']):
            group['blockers'].append(dict(reason='financial_integrity_pending',responsible='accounting'))
            group['ready']=False
        # Reuse the approval guard only for otherwise ready cards. No writes or
        # locks occur here; the mutation rechecks the guard inside its transaction.
        if group['ready'] and not group['approved_day']:
            from fastapi import HTTPException
            from .order_financial_cases import guard_financial_closure
            try:guard_financial_closure(c,shift_id=group['id'],brand_id=user['brand_id'])
            except HTTPException as exc:
                if exc.status_code!=409:raise
                detail=exc.detail if isinstance(exc.detail,dict) else {'message':str(exc.detail)}
                group['blockers'].append(dict(reason=detail.get('code','financial_case_pending'),message=detail.get('message',''),responsible='accounting'))
                group['ready']=False
        if not group['ready']:group['completion_percent']=min(99,group['completion_percent'])
        group['completion_scope']='accounting_review'
        group['financial_completion_scope']='closure_cash_and_recorded_card'
        group['bank_settlement_included']=False
        group['financial_completion_percent']=int(100*group['completed']/group['total']) if group['total'] else 0
        pending=[r for r in group['drivers'] if r['stage']!='cash_received' or r['variance_pending']]
        group['due_at']=min((r['due_at'] for r in (pending or group['drivers'])),default=None)
        group['due_on']=group['due_at'].date() if group['due_at'] else None
        group['overdue_count']=sum(r['overdue'] for r in pending)
        group['overdue']=bool(group['overdue_count'])
    return safe({'cards':sorted(groups.values(),key=lambda g:(bool(g['approved_day']),-g['day'].toordinal(),g['id'])),'has_more':has_more,'offset':offset,'page_size':page_size})


def extend_deadline(c,user,report_id,note):
    from .accounting import branches,fail
    allowed=[b['id'] for b in branches(c,user)]
    # The report lock serializes extension against another extension/settlement.
    c.execute('SELECT id FROM closure_reports WHERE id=%s FOR UPDATE',(report_id,))
    row=next((r for r in source_rows(c,user,allowed) if r['id']==report_id),None)
    if not row:fail('REPORT_SCOPE','التقفيلة غير متاحة.',404)
    if row['user_id']==user['id']:fail('SELF_SETTLEMENT','التمديد من حساب محاسب مستقل.',403)
    card=next(g for g in manager_cards(c,user,allowed)['cards'] if g['id']==row['manager_shift_id'])
    driver=next(r for r in card['drivers'] if r['id']==report_id)
    if card['approved_day'] or not driver['can_extend']:fail('EXTENSION_NOT_ALLOWED','التمديد مرة واحدة للفروع الخارجية غير المكتملة فقط.',409)
    due=datetime.fromisoformat(driver['due_at'])+timedelta(hours=48)
    details=dict(reason=note,brand_id=user['brand_id'],hours=48,original_due_at=driver['due_at'],due_at=due.isoformat())
    db.audit(c,user['id'],'accounting.deadline.extended','report',report_id,details)
    return details


def approve_day(c,user,manager_shift_id,note):
    from .accounting import fail,branches,driver_settlement_values,variance_confirmation,TZ
    from .operations import safe
    manager=c.execute("SELECT s.*,u.display_name manager_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.id=%s AND s.brand_id=%s AND s.kind='manager' FOR UPDATE OF s",(manager_shift_id,user['brand_id'])).fetchone()
    allowed={b['id'] for b in branches(c,user)}
    if not manager or not set(manager['branch_ids'])<=allowed:fail('DAY_SCOPE','التقفيلة خارج نطاقك.',404)
    if manager['user_id']==user['id']:fail('SELF_SETTLEMENT','الاعتماد من حساب محاسب مستقل.',403)
    from .order_financial_cases import guard_financial_closure
    guard_financial_closure(c,shift_id=manager_shift_id,brand_id=user['brand_id'])
    if manager['status']!='closed':fail('OPEN_SHIFTS','أقفل شفت مدير التوصيل أولًا.',409)
    previous=c.execute('SELECT * FROM accounting_daily_closures WHERE manager_shift_id=%s',(manager_shift_id,)).fetchone()
    if previous:fail('DAY_CLOSED','تقفيلة اليوم معتمدة مسبقًا.',409)
    rows=source_rows(c,user,list(allowed),manager_shift_id=manager_shift_id)
    children=c.execute("SELECT id,status FROM shifts WHERE manager_shift_id=%s AND kind='driver'",(manager_shift_id,)).fetchall()
    if any(r['status']!='closed' for r in children) or {r['shift_id'] for r in rows}!={r['id'] for r in children}:
        fail('PENDING_DRIVERS','توجد تقفيلات سائقين غير مكتملة أو خارج نطاق المحاسب.',409)
    if not rows:fail('NO_DRIVER_CLOSURES','لا توجد تقفيلات سائقين لاعتمادها.',409)
    changes=histories(c,user,rows)
    if any(driver_settlement_values(r['snapshot'],changes[r['id']][-1] if changes[r['id']] else None)['stage']!='cash_received' for r in rows):
        fail('PENDING_SETTLEMENTS','أكمل الموازنة والكاش لكل سائق أولًا.',409)
    for r in rows:
        from .financial_integrity import require_verified
        require_verified(r['snapshot'])
        from .financial_treatments import reviewed_values
        v=reviewed_values(c,r,changes[r['id']][-1],user=user)
        if not v['period_approval_allowed']:
            fail('FINANCIAL_TREATMENT_REQUIRED','أكمل معالجة مستقلة موثقة؛ الملاحظة وحدها لا تسوي الفرق.',409)
    ds=min(r['day'] for r in rows);de=max(r['day'] for r in rows)
    bs=[b for b in branches(c,user) if b['id'] in manager['branch_ids']]
    # Snapshot is generated in the same transaction as approval and never edited.
    report=statement(c,user,ds,de,[b['id'] for b in bs],bs,manager_shift_id=manager_shift_id)
    identifier=db.new_id('acd_')
    for item in report['rows']:
        if item.get('closure_count'):
            item['approved_closure_count']=item['closure_count']
            item['approval_status']='اعتماد يوم المدير محفوظ'
            item['approval_references']=identifier
    report['financial_state']='review_approved_outstanding' if any(r.get('outstanding_receivable_halalas') or r.get('outstanding_payable_halalas') for r in report.get('settlements',[])) else 'matched'
    report['outstanding_receivable_halalas']=sum(r.get('outstanding_receivable_halalas',0) for r in report.get('settlements',[]))
    report['outstanding_payable_halalas']=sum(r.get('outstanding_payable_halalas',0) for r in report.get('settlements',[]))
    row=c.execute("""INSERT INTO accounting_daily_closures(id,manager_shift_id,brand_id,branch_ids,operational_date,snapshot,approved_by,note)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at""",(identifier,manager_shift_id,user['brand_id'],db.json(manager['branch_ids']),ds,db.json(report),user['id'],note)).fetchone()
    db.audit(c,user['id'],'accounting.day.closed','accounting_day',identifier,{'manager_shift_id':manager_shift_id,'note':note})
    return safe(row)
