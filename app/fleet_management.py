"""Audited, scoped vehicle allocation; operating history is never reassigned."""
import re
from . import db, vehicle_profile, shift_vehicle


def plate(v):
    return re.sub(r'\s+', '', str((v or {}).get('plate', '')).upper())


def driver_scope(user, driver):
    return driver['brand_id']==user['brand_id'] and (user['role']=='owner' or bool(driver['branch_ids']) and set(driver['branch_ids'])<=set(user.get('branch_ids') or []))


def car_scope(c,user,car,drivers):
    if car['brand_id']!=user['brand_id']:return False
    if user['role']=='owner':return True
    scopes=[d['branch_ids'] for d in drivers if plate(d['vehicle'])==car['plate_key']]
    scopes += [r['branch_ids'] for r in c.execute('SELECT s.branch_ids FROM fleet_operating_segments l JOIN shifts s ON s.id=l.shift_id WHERE l.vehicle_id=%s',(car['id'],)).fetchall()]
    if car['details'].get('management_branch_ids'):scopes.append(car['details']['management_branch_ids'])
    return bool(scopes) and all(bool(scope) and set(scope)<=set(user.get('branch_ids') or []) for scope in scopes)


def perform(c,user,vid,body):
    # Consistent user-before-vehicle lock order matches staff edits/shift opening.
    drivers=c.execute("SELECT * FROM users WHERE brand_id=%s AND role='driver' ORDER BY id FOR UPDATE",(user['brand_id'],)).fetchall()
    cars=c.execute('SELECT * FROM fleet_vehicles WHERE brand_id=%s ORDER BY id FOR UPDATE',(user['brand_id'],)).fetchall()
    car=next((v for v in cars if v['id']==vid),None)
    if not car or not car_scope(c,user,car,drivers):raise db.error(404,'VEHICLE_NOT_FOUND','السيارة غير موجودة ضمن نطاقك.')
    if int(car['details'].get('management_revision',0))!=body.revision:raise db.error(409,'VEHICLE_CHANGED','حدث بطاقات السيارات قبل التنفيذ.')
    assigned=[d for d in drivers if plate(d['vehicle'])==car['plate_key']]
    affected=[car];changes=[]
    if body.action in ('assign','swap','handover'):
        if car['details'].get('operating_status')=='paused':raise db.error(409,'VEHICLE_PAUSED','السيارة موقوفة؛ أعد تشغيلها أولًا.')
        target=next((d for d in drivers if d['id']==body.driver_id and d['active'] and driver_scope(user,d)),None)
        if not target:raise db.error(404,'DRIVER_NOT_FOUND','السائق غير موجود أو غير مفعل ضمن نطاقك.')
        old=next((v for v in cars if v['plate_key']==plate(target['vehicle'])),None)
        if old and not car_scope(c,user,old,drivers):raise db.error(404,'VEHICLE_NOT_FOUND','السيارة الحالية خارج نطاقك.')
        if old and old['id']==vid:raise db.error(409,'VEHICLE_ALREADY_ASSIGNED','السيارة مسندة لهذا السائق بالفعل.')
        if old:affected.append(old)
        if body.action=='swap':
            if len(assigned)!=1 or not old or old['details'].get('operating_status')=='paused':raise db.error(409,'VEHICLE_SWAP_INVALID','التبديل يحتاج سيارتين عاملتين، لكل منهما سائق واحد.')
            if len([d for d in drivers if plate(d['vehicle'])==old['plate_key']])!=1:raise db.error(409,'VEHICLE_ASSIGNMENT_CONFLICT','راجع الإسناد المتكرر للسيارة أولًا.')
            changes=[(assigned[0],old),(target,car)]
        else:
            if assigned:raise db.error(409,'VEHICLE_ASSIGNED','السيارة مسندة لسائق آخر؛ استخدم التبديل بين السائقين.')
            changes=[(target,car)]
    elif body.action=='release':
        changes=[(d,None) for d in assigned]
    if body.action=='handover':
        return handover(c,user,car,old,target,body)
    users={d['id'] for d in assigned}|{d['id'] for d,_ in changes}
    if users and c.execute("SELECT 1 FROM shifts WHERE user_id=ANY(%s) AND kind='driver' AND status IN ('open','closing')",(list(users),)).fetchone():
        raise db.error(409,'VEHICLE_SHIFT_ACTIVE','سلّم السيارة وأنهِ الشفت الجاري قبل الإيقاف أو الاستبدال أو التبديل.')
    if c.execute("SELECT 1 FROM fleet_operating_segments l JOIN shifts s ON s.id=l.shift_id WHERE l.vehicle_id=ANY(%s) AND s.status IN ('open','closing') AND l.returned_at IS NULL",([v['id'] for v in affected],)).fetchone():
        raise db.error(409,'VEHICLE_IN_USE','السيارة مستلمة في شفت جارٍ؛ سلّمها أولًا.')
    before=[{'driver_id':d['id'],'driver_name':d['display_name'],'vehicle':d['vehicle']} for d,_ in changes]
    for d,v in changes:
        if v:
            profile={k:val for k,val in v['details'].items() if k in vehicle_profile.FIELDS}
            profile['plate']=v['plate']
            last=c.execute("SELECT max((coalesce(x.after_metrics,l.metrics)->>'end_odometer')::numeric) km FROM fleet_operating_segments l LEFT JOIN LATERAL(SELECT after_metrics FROM vehicle_log_corrections WHERE shift_id=l.shift_id AND NOT l.multi_vehicle ORDER BY revision DESC LIMIT 1) x ON true WHERE l.vehicle_id=%s",(v['id'],)).fetchone()['km']
            if last is not None:profile['odometer_km']=max(int(last),profile.get('odometer_km',0) or 0)
        else:profile={}
        c.execute('UPDATE users SET vehicle=%s,updated_at=now() WHERE id=%s',(db.json(profile),d['id']))
    after=[{'driver_id':d['id'],'driver_name':d['display_name'],'plate':v['plate'] if v else None} for d,v in changes]
    for v in affected:
        scope=set(v['details'].get('management_branch_ids') or [])
        for d in drivers:
            if plate(d['vehicle'])==v['plate_key']:scope.update(d['branch_ids'])
        for d,new in changes:
            if new and new['id']==v['id']:scope.update(d['branch_ids'])
        patch={'management_revision':int(v['details'].get('management_revision',0))+1,'management_branch_ids':sorted(scope)}
        if v['id']==vid and body.action in ('pause','resume'):patch['operating_status']='paused' if body.action=='pause' else 'active'
        c.execute('UPDATE fleet_vehicles SET details=details||%s WHERE id=%s',(db.json(patch),v['id']))
        db.audit(c,user['id'],'fleet.manage','vehicle',v['id'],{'action':body.action,'reason':body.reason,'before':before,'after':after,'status_before':v['details'].get('operating_status','active'),'status_after':patch.get('operating_status',v['details'].get('operating_status','active'))})
    return {'vehicle_id':vid,'action':body.action,'revision':body.revision+1}


def handover(c,user,car,old,target,body):
    if not old or not body.outgoing or not body.incoming:
        raise db.error(422,'HANDOVER_READINGS_REQUIRED','أكمل فحص وعداد وبنزين المركبة المسلّمة والمستلمة.')
    if body.outgoing.fuel_exception_id or body.incoming.fuel_exception_id:
        raise db.error(422,'HANDOVER_EXCEPTION_INVALID','سجّل القراءة الفعلية؛ استثناء البنزين يطلب بعد الاستلام.')
    shift=c.execute("SELECT * FROM shifts WHERE user_id=%s AND kind='driver' AND status IN ('open','closing') FOR UPDATE",(target['id'],)).fetchone()
    if not shift or shift['status']!='open':
        raise db.error(409,'HANDOVER_SHIFT_REQUIRED','التبديل أثناء الدوام يحتاج شفتًا مفتوحًا غير قيد التقفيلة.')
    log=dict(shift.get('vehicle_log') or {})
    if plate(log.get('vehicle'))!=old['plate_key']:
        raise db.error(409,'VEHICLE_CHANGED','حدّث بيانات السائق؛ المركبة الحالية تغيرت.')
    floor=max([log.get('start',{}).get('odometer_km',0),log.get('fuel_readiness',{}).get('odometer_km',0)]+[r['odometer_km'] for r in log.get('refuels',[])])
    if body.outgoing.odometer_km<floor:
        raise db.error(422,'ODOMETER_DECREASE','عداد تسليم المركبة أقل من آخر قراءة مسجلة لها.')
    profile={k:v for k,v in car['details'].items() if k in vehicle_profile.FIELDS}
    profile['plate']=car['plate']
    newlog=shift_vehicle.start(c,user,{**target,'vehicle':profile},body.incoming)
    past=log.pop('previous_segments',[])
    log['end']=shift_vehicle.stamp(c,user,body.outgoing)
    log['handover']={'reason':body.reason,'actor_id':user['id'],'next_vehicle_id':car['id']}
    newlog['previous_segments']=[*past,log]
    newlog['awaiting_refuel']=body.incoming.fuel_percent<100
    c.execute('UPDATE users SET vehicle=%s,updated_at=now() WHERE id=%s',(db.json(profile),target['id']))
    c.execute('UPDATE shifts SET vehicle_log=%s WHERE id=%s',(db.json(newlog),shift['id']))
    for vehicle,km in ((old,body.outgoing.odometer_km),(car,body.incoming.odometer_km)):
        details=vehicle['details']
        scope=sorted(set(details.get('management_branch_ids',[]))|set(target['branch_ids']))
        patch={'odometer_km':max(details.get('odometer_km',0) or 0,km),'management_branch_ids':scope,'management_revision':int(details.get('management_revision',0))+1}
        c.execute('UPDATE fleet_vehicles SET details=details||%s WHERE id=%s',(db.json(patch),vehicle['id']))
        db.audit(c,user['id'],'fleet.manage','vehicle',vehicle['id'],{'action':'handover','shift_id':shift['id'],'reason':body.reason,'before':[{'driver_id':target['id'],'vehicle':log['vehicle']}],'after':[{'driver_id':target['id'],'driver_name':target['display_name'],'plate':car['plate']}],'outgoing':log['end'],'incoming':newlog['start']})
    return {'vehicle_id':car['id'],'shift_id':shift['id'],'action':'handover','revision':body.revision+1}
