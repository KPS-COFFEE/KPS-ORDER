"""Owner-approved morning Branch 2 task receipt trials; no vehicle evidence implied."""
from datetime import datetime, timedelta, timezone
from . import auth, db


def authorize_open(c, actor, target, body, manager, branches):
    if actor['role'] != 'owner':
        raise db.error(403, 'TRIAL_OWNER_REQUIRED', 'تجربة استقبال المهام تحتاج اعتماد المالك.')
    auth.require_owner_step_up(c, actor)
    if (body.kind != 'driver' or target['role'] != 'driver' or target['brand_id'] != 'kps'
            or actor['brand_id'] != target['brand_id'] or not manager
            or (manager.get('delivery_window_snapshot') or {}).get('period') != 'morning'
            or len(branches) != 1 or branches[0]['branch_no'] != 2
            or ''.join(branches[0]['city'].split()) != 'حفرالباطن'
            or manager['branch_ids'] != [branches[0]['id']]):
        raise db.error(403, 'TRIAL_MORNING_BRANCH_SCOPE', 'التجربة لاستقبال مهام صباحي فرع 2 فقط.')
    if body.vehicle_inspection is not None or body.opening_halalas or len(body.reason.strip()) < 3:
        raise db.error(422, 'TRIAL_REASON_REQUIRED', 'سجل سبب التجربة دون قراءة عداد أو فحص أو عهدة افتراضية.')


def opening_log(c, actor, reason):
    now=c.execute('SELECT now() n').fetchone()['n']
    return {'version':1,'vehicle':{},'start':None,'refuels':[],
            'receipt_trial':{'authorized_by':actor['id'],'reason':reason.strip(),
                             'authorized_at':now.isoformat(),
                             'expires_at':(now+timedelta(minutes=20)).isoformat(),
                             'inspection_deferred':True,'receive_accept_only':True}}


def trial(shift):
    return ((shift or {}).get('vehicle_log') or {}).get('receipt_trial')


def assignment_guard(shift, order, now=None):
    mode=trial(shift)
    if not mode:return
    now=now or datetime.now(timezone.utc)
    if (datetime.fromisoformat(mode['expires_at']) <= now or order['brand_id'] != 'kps'
            or (order.get('snapshot') or {}).get('delivery_period') != 'morning'
            or (shift.get('delivery_window_snapshot') or {}).get('period') != 'morning'):
        raise db.error(409, 'TRIAL_RECEIPT_EXPIRED_OR_SCOPE', 'تجربة استقبال الطلبات انتهت أو الطلب خارج الصباحي.')


def physical_guard(shift, status):
    if trial(shift) and status in ('picked_up','delivered'):
        raise db.error(409, 'TRIAL_INSPECTION_DEFERRED', 'هذه تجربة استقبال وقبول مهام فقط؛ يلزم فحص فعلي قبل رحلة توصيل.')
