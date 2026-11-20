"""Explicit simulated rehearsal, isolated from operational and financial tables.

Only the normal audit log and existing printer test queue are shared. Nothing
here changes delivery admission, the clock, permissions, orders or real shifts.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import re
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import auth, catalog, db

router = APIRouter(prefix='/api/morning-trial')
KEY = 'morning_trial_sessions'
ROLES = {'manager': 'delivery_manager', 'preparer': 'branch_manager', 'driver': 'driver'}


class Start(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    branch_id: str = Field(min_length=1, max_length=100)
    manager_user_id: str = Field(min_length=1, max_length=100)
    preparer_user_id: str = Field(min_length=1, max_length=100)
    driver_user_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=500)


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['accept', 'preparing', 'ready', 'driver_accept', 'picked_up', 'delivered', 'close', 'stop']
    reason: str = Field(default='', max_length=500)


def _principal(user):
    if (not user.get('active', True) or user.get('invitation_status', 'active') != 'active'
            or user.get('brand_id') != 'kps' or user.get('role') not in {'owner', *ROLES.values()}):
        raise db.error(403, 'MORNING_TRIAL_FORBIDDEN', 'التجربة متاحة لحسابات كبز المعتمدة فقط.')


def _store(c, *, lock=False):
    row = c.execute('SELECT settings FROM app_settings WHERE brand_id=%s' + (' FOR UPDATE' if lock else ''), ('kps',)).fetchone()
    if not row:
        raise db.error(409, 'MORNING_TRIAL_SETTINGS', 'إعدادات كبز غير متاحة.')
    settings = deepcopy(row['settings'] or {})
    return settings, settings.setdefault(KEY, {})


def _save(c, settings):
    c.execute('UPDATE app_settings SET settings=%s WHERE brand_id=%s', (db.json(settings), 'kps'))


def _branch(c, branch_id):
    row = c.execute('SELECT id,brand_id,branch_no,name,city FROM branches WHERE id=%s AND brand_id=%s', (branch_id, 'kps')).fetchone()
    if not row or row['branch_no'] != 2 or catalog.normalize_city(row['city']) != 'حفر الباطن':
        raise db.error(403, 'MORNING_TRIAL_BRANCH', 'التجربة معزولة لفرع 2 بحفر الباطن فقط.')
    return row


def _eligible_employee(c, uid):
    employee = c.execute('SELECT id,display_name,phone,active,invitation_status,brand_id,role,branch_ids FROM users WHERE id=%s', (uid,)).fetchone()
    if (not employee or not employee['active'] or employee['invitation_status'] != 'active'
            or employee['brand_id'] != 'kps' or employee['role'] not in ROLES.values()
            or not employee['phone']):
        return None
    try:
        phone = auth.normalize_phone(employee['phone'])
    except HTTPException:
        return None
    if not re.fullmatch(r'\+9665[0-9]{8}', phone):
        return None
    matches = c.execute("SELECT id FROM users WHERE brand_id='kps' AND role<>'customer' AND normalized_identity_phone(phone)=%s", (phone,)).fetchall()
    if len(matches) != 1 or matches[0]['id'] != uid:
        return None
    return dict(employee, phone=phone)


def _member(user, trial):
    if user['role'] == 'owner':
        return 'owner'
    for role, member in trial['participants'].items():
        if member['id'] == user['id'] and user['role'] == ROLES[role]:
            return role
    raise db.error(404, 'MORNING_TRIAL_NOT_FOUND', 'التجربة غير متاحة لحسابك.')


def _fresh(c, user, trial, *, write=False):
    _principal(user)
    role = _member(user, trial)
    _branch(c, trial['branch_id'])
    if role != 'owner':
        viewer = _eligible_employee(c, user['id'])
        if (not viewer or viewer['role'] != ROLES[role] or trial['branch_id'] not in viewer['branch_ids']):
            raise db.error(404, 'MORNING_TRIAL_NOT_FOUND', 'التجربة غير متاحة لحسابك.')
    # The owner's explicit trial grant is independent of normal morning intake.
    # Read-only reports remain available after expiry or stop.
    if not write:
        return role
    for name, member in trial['participants'].items():
        employee = _eligible_employee(c, member['id'])
        if (not employee or employee['role'] != ROLES[name] or trial['branch_id'] not in employee['branch_ids']):
            raise db.error(403, 'MORNING_TRIAL_MEMBER_REVOKED', 'تغير اعتماد أحد المشاركين؛ أوقف التجربة وراجع الحسابات.')
    if c.execute("SELECT 1 FROM shifts WHERE user_id=%s AND status IN ('open','closing','submitted') LIMIT 1", (trial['participants']['driver']['id'],)).fetchone():
        raise db.error(409, 'MORNING_TRIAL_DRIVER_BUSY', 'السائق لديه شفت فعلي؛ اختر سائقًا متفرغًا للتجربة المعزولة.')
    return role


def _load(c, user, trial_id, *, lock=False, write=False):
    settings, sessions = _store(c, lock=lock)
    trial = sessions.get(trial_id)
    if not trial:
        raise db.error(404, 'MORNING_TRIAL_NOT_FOUND', 'التجربة غير متاحة لحسابك.')
    return settings, trial, _fresh(c, user, trial, write=write)


def _view(c, trial):
    result = deepcopy(trial)
    now = c.execute('SELECT now() n').fetchone()['n']
    result['expired'] = datetime.fromisoformat(result['expires_at']) <= now
    job = None
    if trial['order']['print_job_id']:
        job = c.execute("SELECT id,status FROM branch_print_jobs WHERE id=%s AND kind='test' AND payload->>'morning_trial_id'=%s", (trial['order']['print_job_id'], trial['id'])).fetchone()
    return {'trial': result, 'print_job': job}


def _owner(user):
    _principal(user)
    if user['role'] != 'owner':
        raise db.error(403, 'MORNING_TRIAL_OWNER', 'هذا الإجراء للمالك فقط.')


@router.get('/setup')
def setup(user=Depends(auth.current_user)):
    _owner(user)
    with db.tx() as c:
        branch = c.execute("SELECT id FROM branches WHERE brand_id='kps' AND branch_no=2").fetchone()
        branch = _branch(c, (branch or {}).get('id', ''))
        result = {'branch': branch, 'managers': [], 'preparers': [], 'drivers': []}
        users = c.execute("SELECT id,display_name,phone,role FROM users WHERE active AND brand_id='kps' AND branch_ids ? %s AND role=ANY(%s) ORDER BY display_name,id", (branch['id'], list(ROLES.values()))).fetchall()
        for person in users:
            employee = _eligible_employee(c, person['id'])
            if not employee:
                continue
            entry = {key: employee[key] for key in ('id', 'display_name', 'phone')}
            if person['role'] == 'delivery_manager':
                result['managers'].append(entry)
            elif person['role'] == 'branch_manager':
                result['preparers'].append(entry)
            elif person['role'] == 'driver':
                result['drivers'].append(entry)
        return result


@router.get('/current')
def current(user=Depends(auth.current_user)):
    _principal(user)
    with db.tx() as c:
        _, sessions = _store(c)
        permitted = [trial for trial in sessions.values() if user['role'] == 'owner' or any(member['id'] == user['id'] for member in trial['participants'].values())]
        if not permitted:
            return {'trial': None, 'print_job': None}
        trial = max(permitted, key=lambda row: row['created_at'])
        _fresh(c, user, trial)
        return _view(c, trial)


@router.post('/start')
def start(body: Start, user=Depends(auth.current_user), idem: str | None = Header(None, alias='Idempotency-Key')):
    _owner(user)
    if len({body.manager_user_id, body.preparer_user_id, body.driver_user_id}) != 3:
        raise db.error(422, 'MORNING_TRIAL_MEMBERS', 'اختر ثلاثة حسابات مختلفة بالأدوار الصحيحة.')
    with db.tx() as c:
        auth.require_owner_step_up(c, user)
        settings, sessions = _store(c, lock=True)
        branch = _branch(c, body.branch_id)
        participants = {}
        for name in ROLES:
            uid = getattr(body, name + '_user_id')
            row = c.execute('SELECT id,display_name FROM users WHERE id=%s', (uid,)).fetchone()
            if not row:
                raise db.error(403, 'MORNING_TRIAL_MEMBER_REVOKED', 'اختر حسابًا موجودًا ومعتمدًا.')
            participants[name] = dict(row)
        now = c.execute('SELECT now() n').fetchone()['n']
        candidate = {'id': db.new_id('trial_'), 'status': 'open', 'created_at': now.isoformat(),
                     'expires_at': (now + timedelta(minutes=20)).isoformat(), 'branch_id': branch['id'],
                     'branch_name': branch['name'], 'participants': participants, 'reason': body.reason,
                     'authorized_by': user['id'], 'flow_version': 2, 'simulated': True, 'financial_effect': False,
                     'order': {'id': 'TRIAL-' + db.new_id()[:12], 'status': 'new', 'item_label': 'طلب تجريبي — لا يتم التجهيز الفعلي', 'print_job_id': None},
                     'events': []}
        _fresh(c, user, candidate, write=True)
        def perform():
            if any(t['status'] == 'open' and datetime.fromisoformat(t['expires_at']) > now for t in sessions.values()):
                raise db.error(409, 'MORNING_TRIAL_ALREADY_OPEN', 'توجد تجربة مفتوحة؛ أكملها أو أوقفها أولًا.')
            if len(sessions) >= 30:
                raise db.error(409, 'MORNING_TRIAL_HISTORY_LIMIT', 'بلغ سجل التجارب الحد المسموح؛ يلزم مراجعة المالك.')
            sessions[candidate['id']] = candidate
            _save(c, settings)
            db.audit(c, user['id'], 'morning.trial.started', 'morning_trial', candidate['id'], {'branch_id': branch['id'], 'participants': {key: value['id'] for key, value in participants.items()}, 'reason': body.reason, 'simulated': True})
            return _view(c, candidate)
        result = db.idempotent(c, user['id'], idem, 'morning.trial.start', body.model_dump(), perform)
        persisted = sessions.get(result['trial']['id'])
        if (not persisted or persisted['status'] != 'open'
                or datetime.fromisoformat(persisted['expires_at']) <= now):
            raise db.error(409, 'MORNING_TRIAL_EXPIRED', 'انتهت التجربة؛ التقرير متاح للقراءة فقط.')
        _fresh(c, user, persisted, write=True)
        return _view(c, persisted)


@router.get('/{trial_id}')
def read(trial_id: str, user=Depends(auth.current_user)):
    with db.tx() as c:
        _, trial, _ = _load(c, user, trial_id)
        return _view(c, trial)


@router.post('/{trial_id}/action')
def action(trial_id: str, body: Action, user=Depends(auth.current_user), idem: str | None = Header(None, alias='Idempotency-Key')):
    with db.tx() as c:
        settings, trial, role = _load(c, user, trial_id, lock=True, write=body.action != 'stop')
        if body.action == 'stop':
            _owner(user)
        now = c.execute('SELECT now() n').fetchone()['n']
        if body.action != 'stop' and (trial['status'] != 'open' or datetime.fromisoformat(trial['expires_at']) <= now):
            raise db.error(409, 'MORNING_TRIAL_EXPIRED', 'انتهت التجربة؛ التقرير متاح للقراءة فقط.')
        allowed = {'accept': {'manager', 'preparer'}, 'preparing': {'manager', 'preparer'},
                   'ready': {'manager', 'preparer'}, 'driver_accept': {'driver'}, 'picked_up': {'driver'},
                   'delivered': {'driver'}, 'close': {'manager', 'owner'}, 'stop': {'owner'}}
        if role not in allowed[body.action]:
            raise db.error(403, 'MORNING_TRIAL_ACTION', 'هذا الإجراء ليس ضمن دورك في التجربة.')
        fallback = role == 'manager' and body.action in {'preparing', 'ready'}
        reason = body.reason.strip()
        if fallback and len(reason) < 3:
            raise db.error(422, 'MORNING_TRIAL_FALLBACK_REASON', 'اكتب سبب قيام المدير بالتجهيز بدل المحضّر المتأخر.')
        if reason and not fallback:
            raise db.error(422, 'MORNING_TRIAL_REASON_SCOPE', 'سبب الإنابة متاح فقط لتجهيز المدير بدل المحضّر.')
        def perform():
            previous = trial['order']['status']
            if trial['status'] != 'open':
                raise db.error(409, 'MORNING_TRIAL_ENDED', 'التجربة منتهية؛ التقرير للقراءة فقط.')
            transitions = {'accept': ('new', 'assigned'), 'driver_accept': ('assigned', 'driver_accepted'),
                           'preparing': ('driver_accepted', 'preparing'), 'ready': ('preparing', 'ready'),
                           'picked_up': ('ready', 'picked_up'), 'delivered': ('picked_up', 'delivered'),
                           'close': ('delivered', 'delivered')}
            if body.action != 'stop':
                before, after = transitions[body.action]
                if previous != before:
                    raise db.error(409, 'MORNING_TRIAL_TRANSITION', 'تغيرت مرحلة التجربة؛ حدّث الشاشة.')
                if trial.get('flow_version') != 2:
                    raise db.error(409, 'MORNING_TRIAL_OLD_FLOW', 'هذه تجربة قديمة؛ أوقفها وابدأ تجربة جديدة. التقرير محفوظ.')
                trial['order']['status'] = after
            if body.action == 'accept':
                devices = c.execute("SELECT id,label FROM branch_print_devices WHERE brand_id='kps' AND branch_id=%s AND revoked_at IS NULL AND token_hash IS NOT NULL AND approved_at IS NOT NULL ORDER BY id FOR UPDATE", (trial['branch_id'],)).fetchall()
                if len(devices) != 1:
                    raise db.error(409, 'MORNING_PRINTER_NOT_READY', 'يلزم وجود طابعة واحدة مرتبطة ومعتمدة بفرع 2.')
                job_id = db.new_id('print_')
                lines = [{'text': text, 'bold': True, 'center': True} for text in ('تجربة صباحية معزولة / SIMULATED MORNING TRIAL', 'ليست فاتورة — لا بيع ولا تحصيل', 'NOT AN INVOICE — DO NOT PREPARE', trial['branch_name'], trial['order']['id'], trial['order']['item_label'], 'اختبار قبول الطلب والطباعة فقط')]
                payload = {'language': 'ar', 'kind': 'test', 'morning_trial_id': trial['id'], 'lines': lines}
                c.execute("INSERT INTO branch_print_jobs(id,brand_id,branch_id,device_id,kind,payload,requested_by) VALUES(%s,'kps',%s,%s,'test',%s,%s)", (job_id, trial['branch_id'], devices[0]['id'], db.json(payload), user['id']))
                trial['order']['print_job_id'] = job_id
                trial['order']['assigned_driver_id'] = trial['participants']['driver']['id']
                trial['order']['assigned_at'] = now.isoformat()
            if body.action == 'driver_accept':
                trial['order']['driver_accepted_by'] = user['id']
                trial['order']['driver_accepted_at'] = now.isoformat()
            if body.action in {'stop', 'close'}:
                trial['status'] = 'stopped' if body.action == 'stop' else 'closed'
                trial['ended_at'] = now.isoformat()
                if body.action == 'stop' and trial['order']['print_job_id']:
                    c.execute("UPDATE branch_print_jobs SET status='cancelled',finished_at=now() WHERE id=%s AND kind='test' AND status='queued' AND payload->>'morning_trial_id'=%s", (trial['order']['print_job_id'], trial['id']))
            event = {'action': body.action, 'actor_id': user['id'], 'actor_name': user.get('display_name', ''), 'role': role, 'at': now.isoformat(), 'simulated': True}
            if fallback:
                event.update(on_behalf_of=trial['participants']['preparer']['id'], reason=reason)
            trial['events'].append(event)
            if body.action == 'accept':
                trial['events'].append({'action': 'auto_assign', 'actor_id': user['id'],
                    'actor_name': user.get('display_name', ''), 'role': role, 'at': now.isoformat(),
                    'driver_id': trial['participants']['driver']['id'], 'automatic': True,
                    'simulated': True})
            _save(c, settings)
            db.audit(c, user['id'], 'morning.trial.' + body.action, 'morning_trial', trial['id'], {'simulated': True, 'previous': previous, 'status': trial['order']['status'], 'branch_id': trial['branch_id'], 'reason': reason if fallback else None,
                'on_behalf_of': trial['participants']['preparer']['id'] if fallback else None,
                'auto_assigned_driver_id': trial['participants']['driver']['id'] if body.action == 'accept' else None})
            return _view(c, trial)
        db.idempotent(c, user['id'], idem, 'morning.trial.action:' + trial_id, body.model_dump(), perform)
        return _view(c, trial)
