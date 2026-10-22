"""Driver-owned accounting statements and immutable manager-day approvals."""
from collections import defaultdict
from datetime import datetime, timedelta, time

from . import db


def source_rows(c,user,bids,ds=None,de=None,manager_shift_id=''):
    q="""SELECT r.*,s.manager_shift_id,s.submission,s.scheduled_end_at,s.delivery_window_snapshot,
      s.closed_at shift_closed_at,mu.display_name manager_name,
      u.display_name driver_name,
      coalesce(s.delivery_operational_date,(s.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '16 hours')::date) AS "day"
      FROM closure_reports r JOIN shifts s ON s.id=r.shift_id JOIN users u ON u.id=r.user_id
      JOIN shifts m ON m.id=s.manager_shift_id JOIN users mu ON mu.id=m.user_id
      WHERE r.brand_id=%s AND r.kind='driver' AND m.status='closed' AND r.branch_ids ?| %s"""
    args=[user['brand_id'],bids]
    if ds:
        q+=" AND coalesce(s.delivery_operational_date,(s.opened_at AT TIME ZONE 'Asia/Riyadh'-interval '16 hours')::date) BETWEEN %s AND %s"
        args.extend([ds,de])
    if manager_shift_id:q+=' AND s.manager_shift_id=%s';args.append(manager_shift_id)
    rows=c.execute(q+' ORDER BY "day",r.created_at,r.id',args).fetchall()
    allowed=set(user.get('branch_ids',[]))
    return [r for r in rows if user['role']=='owner' or set(r['branch_ids'])<=allowed]


def histories(c,user,rows):
    ids=[r['id'] for r in rows]
    changes=defaultdict(list)
    for event in c.execute("""SELECT e.*,u.display_name actor_name FROM driver_accounting_settlements e
      JOIN users u ON u.id=e.actor_id WHERE e.brand_id=%s AND e.report_id=ANY(%s) ORDER BY e.report_id,e.revision""",
      (user['brand_id'],ids)).fetchall():changes[event['report_id']].append(event)
    return changes


def settlement_audit(c,user,rows,changes):
    from .accounting import driver_settlement_values
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
                actor=e['actor_name'],at=e['created_at'],reason=e['note'],action='accounting_approval',
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
                completed_orders=0,bank=0,cash_due=0,cash_received=0,pending_cash=0,shortage=0,surplus=0,
                violations=0,traffic=0,notes='',incident_notes='',traffic_notes='',adjustments='',
                pending_balance=0,pending_receipt=0,closure_count=0,manager='',status='—')
    # Delivered order IDs are selected once across all tasks, avoiding duplicate
    # counts when orders were reassigned or split across collection records.
    task_ids=defaultdict(set)
    for task in c.execute("""SELECT DISTINCT ON (t.order_id) t.order_id,t.shift_id FROM driver_tasks t
      JOIN orders o ON o.id=t.order_id WHERE o.brand_id=%s AND t.status='delivered'
      AND t.shift_id=ANY(%s) ORDER BY t.order_id,t.delivered_at DESC NULLS LAST,t.id DESC""",
      (user['brand_id'],[r['shift_id'] for r in rows])).fetchall():task_ids[task['shift_id']].add(task['order_id'])
    details=[]
    for r in rows:
        history=changes[r['id']];v=driver_settlement_values(r['snapshot'],history[-1] if history else None)
        item=daily[r['user_id'],r['day']]
        item['completed_orders']+=len(task_ids[r['shift_id']])
        item['bank']+=v['bank_halalas'] if v['balance_approved'] else 0
        item['cash_due']+=v['cash_due_halalas']
        item['cash_received']+=v['cash_received_halalas'] or 0
        item['pending_cash']+=v['pending_cash_halalas']
        item['shortage']+=v['shortage_halalas'];item['surplus']+=v['surplus_halalas']
        item['pending_balance']+=int(not v['balance_approved']);item['pending_receipt']+=int(not v['cash_recorded'])
        item['closure_count']+=1
        item['manager']+=(' / ' if item['manager'] else '')+r['manager_name']
        notes=[(r['snapshot'].get('shift',{}).get('submission') or {}).get('notes','')]
        notes.extend(e['note'] for e in history)
        item['notes']+='\n'.join(x for x in notes if x)+'\n'
        details.append(dict(id=r['id'],shift_id=r['shift_id'],manager_shift_id=r['manager_shift_id'],
            driver_id=r['user_id'],driver=r['driver_name'],day=r['day'],**v))
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
        daily[e['driver_id'],e['day']]['adjustments']+=f"{e['actor']} · {e['at'].astimezone(TZ).strftime('%Y/%m/%d %H:%M')} · {e['reason']}\n" + \
            f"{'Card' if en else 'الشبكة'}: {before} {e['before_bank']/100:.2f} / {after} {e['after_bank']/100:.2f}; " + \
            f"{'Cash due' if en else 'الكاش المطلوب'}: {before} {e['before_cash']/100:.2f} / {after} {e['after_cash']/100:.2f}; " + \
            f"{'Received' if en else 'المستلم'}: {before} {('—' if e['before_received'] is None else format(e['before_received']/100,'.2f'))} / {after} {('—' if e['after_received'] is None else format(e['after_received']/100,'.2f'))}\n"
    for item in daily.values():
        if item['closure_count']:
            item['status']=('Pending balance' if en else 'بانتظار الموازنة') if item['pending_balance'] else ('Pending cash' if en else 'بانتظار الكاش') if item['pending_receipt'] else ('Approved' if en else 'معتمد')
    output=sorted(daily.values(),key=lambda r:(r['driver'],r['driver_id'],r['day']))
    summaries=[]
    money=('completed_orders','bank','cash_due','cash_received','pending_cash','shortage','surplus','violations','traffic')
    for identifier,name in names.items():
        entries=[r for r in output if r['driver_id']==identifier]
        summaries.append(dict(driver_id=identifier,driver=name,**{k:sum(r[k] for r in entries) for k in money}))
    return safe(dict(group='driver_statement',rows=output,drivers=summaries,settlements=details,adjustment_history=audit,deadline_extensions=deadline_extensions,
        incidents=incidents,branches=bs,period={'start':ds,'end':de,'timezone':'Asia/Riyadh'},generated_at=datetime.now(TZ),
        definitions=('Each delivered order counts once. Balance papers and physical cash are approved independently. Waiting cash is not a shortage. Shortages and surpluses remain separate per driver and channel against the system ledger. Incident values show recorded amounts and responsibility, without payroll deductions.' if en else
        'كل طلب مسلّم يُعد مرة واحدة. اعتماد ورقة الموازنة مستقل عن استلام الكاش. النقد المنتظر ليس عجزًا. العجز والفائض منفصلان لكل سائق ولكل وسيلة مقابل رصيد النظام. قيم المخالفات مبالغ مسجلة مع حالة المسؤولية ولا تنشئ خصمًا من الراتب.')))


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


def manager_cards(c,user,bids):
    from .accounting import driver_settlement_values,TZ
    from .catalog import normalize_city
    from .operations import safe
    rows=source_rows(c,user,bids)
    changes=histories(c,user,rows);groups={}
    local={b['id'] for b in c.execute('SELECT id,city FROM branches WHERE brand_id=%s',(user['brand_id'],)).fetchall() if normalize_city(b['city'])=='حفر الباطن'}
    approved={r['manager_shift_id']:r for r in c.execute('SELECT id,manager_shift_id,created_at FROM accounting_daily_closures WHERE brand_id=%s',(user['brand_id'],)).fetchall()}
    extensions={r['entity_id']:r for r in c.execute("SELECT a.*,u.display_name actor_name FROM audit_events a JOIN users u ON u.id=a.actor_id WHERE a.action='accounting.deadline.extended' AND a.entity_id=ANY(%s)",([r['id'] for r in rows],)).fetchall()}
    for r in rows:
        history=changes[r['id']];v=driver_settlement_values(r['snapshot'],history[-1] if history else None)
        deadline=closure_deadline(r,local,extensions.get(r['id']))
        finished=history[-1]['created_at'] if v['stage']=='cash_received' else None
        deadline['overdue']=bool((finished or datetime.now(TZ))>=deadline['due_at'])
        deadline['can_extend']=deadline['deadline_policy']=='external_72h' and not deadline['extension'] and not finished
        group=groups.setdefault(r['manager_shift_id'],dict(id=r['manager_shift_id'],manager_name=r['manager_name'],day=r['day'],drivers=[],approved_day=approved.get(r['manager_shift_id']),due_on=None))
        group['drivers'].append(dict(id=r['id'],user_id=r['user_id'],driver_name=r['driver_name'],day=r['day'],history=history,**v,**deadline))
    for group in groups.values():
        group['completed']=sum(r['stage']=='cash_received' for r in group['drivers'])
        group['total']=len(group['drivers'])
        group['ready']=group['completed']==group['total']
        group['balance_completed']=sum(r['balance_approved'] for r in group['drivers'])
        group['cash_completed']=sum(r['cash_recorded'] for r in group['drivers'])
        group['completion_percent']=int(100*(group['balance_completed']+group['cash_completed'])/(2*group['total']))
        pending=[r for r in group['drivers'] if r['stage']!='cash_received']
        group['due_at']=min(r['due_at'] for r in (pending or group['drivers']))
        group['due_on']=group['due_at'].date()
        group['overdue_count']=sum(r['overdue'] for r in pending)
        group['overdue']=bool(group['overdue_count'])
    return safe({'cards':sorted(groups.values(),key=lambda g:(bool(g['approved_day']),-g['day'].toordinal(),g['manager_name']))})


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
    from .accounting import fail,branches,driver_settlement_values,TZ
    from .operations import safe
    manager=c.execute("SELECT s.*,u.display_name manager_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.id=%s AND s.brand_id=%s AND s.kind='manager' FOR UPDATE OF s",(manager_shift_id,user['brand_id'])).fetchone()
    allowed={b['id'] for b in branches(c,user)}
    if not manager or not set(manager['branch_ids'])<=allowed:fail('DAY_SCOPE','التقفيلة خارج نطاقك.',404)
    if manager['user_id']==user['id']:fail('SELF_SETTLEMENT','الاعتماد من حساب محاسب مستقل.',403)
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
    ds=min(r['day'] for r in rows);de=max(r['day'] for r in rows)
    bs=[b for b in branches(c,user) if b['id'] in manager['branch_ids']]
    # Snapshot is generated in the same transaction as approval and never edited.
    report=statement(c,user,ds,de,[b['id'] for b in bs],bs,manager_shift_id=manager_shift_id)
    identifier=db.new_id('acd_')
    row=c.execute("""INSERT INTO accounting_daily_closures(id,manager_shift_id,brand_id,branch_ids,operational_date,snapshot,approved_by,note)
      VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at""",(identifier,manager_shift_id,user['brand_id'],db.json(manager['branch_ids']),ds,db.json(report),user['id'],note)).fetchone()
    db.audit(c,user['id'],'accounting.day.closed','accounting_day',identifier,{'manager_shift_id':manager_shift_id,'note':note})
    return safe(row)
