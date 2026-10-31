"""Audited, scoped vehicle allocation; operating history is never reassigned."""
import re
from . import auth, db, vehicle_profile, shift_vehicle


def plate(v):
    return re.sub(r'\s+', '', str((v or {}).get('plate', '')).upper())


def driver_scope(user, driver):
    return driver['brand_id']==user['brand_id'] and ((user['role']=='owner' and not user.get('home_brand_id')) or bool(driver['branch_ids']) and set(driver['branch_ids'])<=set(user.get('branch_ids') or []))


def car_scope(c,user,car,drivers):
    if car['brand_id']!=user['brand_id']:return False
    if (user['role']=='owner' and not user.get('home_brand_id')):return True
    scopes=[d['branch_ids'] for d in drivers if plate(d['vehicle'])==car['plate_key']]
    scopes += [r['branch_ids'] for r in c.execute('SELECT s.branch_ids FROM fleet_operating_segments l JOIN shifts s ON s.id=l.shift_id WHERE l.vehicle_id=%s',(car['id'],)).fetchall()]
    if car['details'].get('management_branch_ids'):scopes.append(car['details']['management_branch_ids'])
    return bool(scopes) and all(bool(scope) and set(scope)<=set(user.get('branch_ids') or []) for scope in scopes)


def require_active_swap(user):
    if user['role'] not in ('owner','admin') or not auth.has_permission(user,'fleet_active_swap'):
        raise db.error(403,'ACTIVE_SWAP_FORBIDDEN','يلزم اعتماد صلاحية استثناء التبديل أثناء الشفت.')


def recheck_swap_scope(c,user,drivers,body):
    # These references remain authorized before idempotent replay, too.
    expected=body.expected_driver_ids or []
    selected=[d for d in drivers if d['id'] in expected]
    other=c.execute('SELECT * FROM fleet_vehicles WHERE id=%s AND brand_id=%s',
                    (body.counterpart_vehicle_id,user['brand_id'])).fetchone()
    if len(expected)!=2 or len(set(expected))!=2 or len(selected)!=2 or not all(driver_scope(user,d) for d in selected):
        raise db.error(404,'DRIVER_NOT_FOUND','السائقان خارج نطاقك أو تغيرا.')
    if not other or not car_scope(c,user,other,drivers):
        raise db.error(404,'VEHICLE_NOT_FOUND','السيارة الأخرى خارج نطاقك.')


def driver_notice(c,user,driver,vehicle,action,reason):
    # One durable, recipient-specific notice in the same transaction as the change.
    db.audit(c,user['id'],'fleet.driver_notice','user',driver['id'],{
        'brand_id':user['brand_id'],'action':action,'reason':reason,
        'old_plate':(driver.get('vehicle') or {}).get('plate'),
        'new_plate':vehicle['plate'] if vehicle else None,
        'entered_by':user['id'],'approved_by':user['id'],
        'actor_name':user.get('display_name') or ''})



def physical_driver_shift(c,user,driver_id):
    rows=c.execute("SELECT * FROM shifts WHERE user_id=%s AND kind='driver' AND status IN ('open','closing') ORDER BY id FOR UPDATE",(driver_id,)).fetchall()
    if not rows or any(s['status']!='open' for s in rows):
        raise db.error(409,'HANDOVER_SHIFT_REQUIRED','التبديل يحتاج وردية مفتوحة غير قيد التقفيلة.')
    if len(rows)==1:return rows[0]
    from .central_workdays import association,authorized
    day=association(c,rows[0]['id'])
    if not day or any((association(c,s['id']) or {}).get('id')!=day['id'] for s in rows):
        raise db.error(409,'ACTIVE_SWAP_OPEN_SHIFTS','راجع الورديات المفتوحة قبل تبديل المركبة.')
    # Changing the one physical car requires live authority over every ledger leg.
    _,legs=authorized(c,user,day['anchor_shift_id'])
    if {s['id'] for _,s in legs}!={s['id'] for s in rows}:
        raise db.error(409,'CENTRAL_WORKDAY_INTEGRITY','تحتاج سجلات الوردية مراجعة المالك.')
    return next(s for s in rows if s['id']==day['anchor_shift_id'])


def active_swap(c,user,car,other,first,second,body):
    """End both vehicle intervals, then start both replacements atomically.

    Shift owners, tasks, money, breaks and availability are not reassigned.
    The two real readings describe the same cars at release and receipt.
    """
    require_active_swap(user)
    if not body.confirmed or not body.outgoing or not body.incoming or len(body.reason.strip())<3:
        raise db.error(422,'ACTIVE_SWAP_CONFIRMATION','أكد التبديل وأكمل فحص وقراءات السيارتين.')
    if body.counterpart_vehicle_id!=other['id'] or body.counterpart_revision is None or body.counterpart_revision!=int(other['details'].get('management_revision',0)):
        raise db.error(409,'VEHICLE_CHANGED','تغيرت السيارة الأخرى؛ حدّث الإسناد قبل التأكيد.')
    if sorted(body.expected_driver_ids or [])!=sorted([first['id'],second['id']]):
        raise db.error(409,'VEHICLE_CHANGED','تغير السائقان؛ راجع الطرفين قبل اعتماد التبديل.')
    if not all(driver_scope(user,d) for d in (first,second)):
        raise db.error(404,'DRIVER_NOT_FOUND','السائق خارج نطاقك.')
    if body.incoming.fuel_exception_id or body.outgoing.fuel_exception_id:
        raise db.error(422,'HANDOVER_EXCEPTION_INVALID','أدخل القراءات الفعلية للسيارتين.')
    lanes=[]
    for driver,old,new,reading in ((first,car,other,body.incoming),(second,other,car,body.outgoing)):
        shift=physical_driver_shift(c,user,driver['id'])
        if shift['brand_id']!=user['brand_id'] or not shift['branch_ids'] or ((user['role']!='owner' or user.get('home_brand_id')) and not set(shift['branch_ids'])<=set(user['branch_ids'])):
            raise db.error(404,'DRIVER_NOT_FOUND','شفت السائق خارج نطاقك.')
        log=dict(shift.get('vehicle_log') or {})
        if plate(log.get('vehicle'))!=old['plate_key'] or log.get('end'):
            raise db.error(409,'VEHICLE_CHANGED','السيارة المستلمة تغيرت؛ حدّث القراءات.')
        if reading.odometer_km<shift_vehicle.odometer_floor(log):
            raise db.error(422,'ODOMETER_DECREASE','عداد تسليم السيارة أقل من آخر قراءة لها.')
        log['end']=shift_vehicle.stamp(c,user,reading)
        log['handover']={'reason':body.reason,'actor_id':user['id'],'approved_by':user['id'],
                         'action':'active_swap','next_vehicle_id':new['id']}
        lanes.append((driver,shift,old,new,reading,log))
    occupied=c.execute("SELECT shift_id,vehicle_id FROM fleet_operating_segments WHERE vehicle_id=ANY(%s) AND returned_at IS NULL",([car['id'],other['id']],)).fetchall()
    expected={(lane[1]['id'],lane[2]['id']) for lane in lanes}
    if len(occupied)!=2 or {(r['shift_id'],r['vehicle_id']) for r in occupied}!=expected:
        raise db.error(409,'VEHICLE_IN_USE','راجع استلام السيارتين؛ يوجد تعارض في سجل التشغيل.')
    # The transaction rolls back both interval endings if either receipt fails.
    for driver,shift,old,new,reading,log in lanes:
        c.execute('UPDATE shifts SET vehicle_log=%s WHERE id=%s',(db.json(log),shift['id']))
    after=[]
    for driver,shift,old,new,reading,log in lanes:
        receiving=body.outgoing if new['id']==other['id'] else body.incoming
        profile={k:v for k,v in new['details'].items() if k in vehicle_profile.FIELDS}
        profile['plate']=new['plate']
        newlog=shift_vehicle.start(c,user,{**driver,'vehicle':profile},receiving)
        past=log.pop('previous_segments',[])
        newlog['previous_segments']=[*past,log]
        newlog['awaiting_refuel']=receiving.fuel_percent<100
        c.execute('UPDATE users SET vehicle=%s,updated_at=now() WHERE id=%s',(db.json(profile),driver['id']))
        c.execute('UPDATE shifts SET vehicle_log=%s WHERE id=%s',(db.json(newlog),shift['id']))
        after.append({'driver_id':driver['id'],'driver_name':driver['display_name'],'plate':new['plate'],'shift_id':shift['id']})
        driver_notice(c,user,driver,new,'active_swap',body.reason)
    before=[{'driver_id':driver['id'],'driver_name':driver['display_name'],'vehicle':log['vehicle']} for driver,shift,old,new,reading,log in lanes]
    for vehicle,reading in ((car,body.incoming),(other,body.outgoing)):
        patch={'management_revision':int(vehicle['details'].get('management_revision',0))+1,
               'odometer_km':max(vehicle['details'].get('odometer_km',0) or 0,reading.odometer_km),
               'management_branch_ids':sorted(set(vehicle['details'].get('management_branch_ids',[]))|set(first['branch_ids'])|set(second['branch_ids']))}
        c.execute('UPDATE fleet_vehicles SET details=details||%s WHERE id=%s',(db.json(patch),vehicle['id']))
        db.audit(c,user['id'],'fleet.manage','vehicle',vehicle['id'],{'action':'active_swap','reason':body.reason,
            'entered_by':user['id'],'approved_by':user['id'],'before':before,'after':after})
    return {'vehicle_id':car['id'],'action':'active_swap','revision':body.revision+1,
            'entered_by':user['id'],'approved_by':user['id'],'drivers':after}


def perform(c,user,vid,body):
    # Consistent user-before-vehicle lock order matches staff edits/shift opening.
    drivers=c.execute("SELECT * FROM users WHERE brand_id=%s AND role='driver' ORDER BY id FOR UPDATE",(user['brand_id'],)).fetchall()
    c.execute("SELECT id FROM shifts WHERE brand_id=%s AND kind='driver' AND status IN ('open','closing') ORDER BY id FOR UPDATE",(user['brand_id'],)).fetchall()
    cars=c.execute('SELECT * FROM fleet_vehicles WHERE brand_id=%s ORDER BY id FOR UPDATE',(user['brand_id'],)).fetchall()
    car=next((v for v in cars if v['id']==vid),None)
    if not car or not car_scope(c,user,car,drivers):raise db.error(404,'VEHICLE_NOT_FOUND','السيارة غير موجودة ضمن نطاقك.')
    if int(car['details'].get('management_revision',0))!=body.revision:raise db.error(409,'VEHICLE_CHANGED','حدث بطاقات السيارات قبل التنفيذ.')
    assigned=[d for d in drivers if plate(d['vehicle'])==car['plate_key']]
    affected=[car];changes=[]
    if body.action in ('assign','transfer','swap','handover','active_swap'):
        if car['details'].get('operating_status')=='paused':raise db.error(409,'VEHICLE_PAUSED','السيارة موقوفة؛ أعد تشغيلها أولًا.')
        target=next((d for d in drivers if d['id']==body.driver_id and d['active'] and driver_scope(user,d)),None)
        if not target:raise db.error(404,'DRIVER_NOT_FOUND','السائق غير موجود أو غير مفعل ضمن نطاقك.')
        old=next((v for v in cars if v['plate_key']==plate(target['vehicle'])),None)
        if old and not car_scope(c,user,old,drivers):raise db.error(404,'VEHICLE_NOT_FOUND','السيارة الحالية خارج نطاقك.')
        if old and old['id']==vid:raise db.error(409,'VEHICLE_ALREADY_ASSIGNED','السيارة مسندة لهذا السائق بالفعل.')
        if old:affected.append(old)
        if body.action=='transfer':
            if body.expected_driver_ids is None or sorted(body.expected_driver_ids)!=sorted(d['id'] for d in assigned):
                raise db.error(409,'VEHICLE_CHANGED','تغير إسناد السيارة؛ حدّث القائمة وأكد النقل مجددًا.')
            if any(not driver_scope(user,d) for d in assigned):raise db.error(404,'DRIVER_NOT_FOUND','السائق خارج نطاقك.')
            changes=[(d,None) for d in assigned]+[(target,car)]
        elif body.action in ('swap','active_swap'):
            if len(assigned)!=1 or not old or old['details'].get('operating_status')=='paused':raise db.error(409,'VEHICLE_SWAP_INVALID','التبديل يحتاج سيارتين عاملتين، لكل منهما سائق واحد.')
            if len([d for d in drivers if plate(d['vehicle'])==old['plate_key']])!=1:raise db.error(409,'VEHICLE_ASSIGNMENT_CONFLICT','راجع الإسناد المتكرر للسيارة أولًا.')
            changes=[(assigned[0],old),(target,car)]
        else:
            if assigned:raise db.error(409,'VEHICLE_ASSIGNED','السيارة مسندة لسائق آخر؛ استخدم التبديل بين السائقين.')
            changes=[(target,car)]
    elif body.action=='release':
        changes=[(d,None) for d in assigned]
    if body.action=='active_swap':
        return active_swap(c,user,car,old,assigned[0],target,body)
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
            checkpoint=v['details'].get('odometer_checkpoint') or {}
            last=c.execute("SELECT max((coalesce(x.after_metrics,l.metrics)->>'end_odometer')::numeric) km FROM fleet_operating_segments l LEFT JOIN LATERAL(SELECT after_metrics FROM vehicle_log_corrections WHERE shift_id=l.shift_id AND NOT l.multi_vehicle ORDER BY revision DESC LIMIT 1) x ON true WHERE l.vehicle_id=%s AND (%s::timestamptz IS NULL OR l.returned_at>%s::timestamptz)",(v['id'],checkpoint.get('recorded_at'),checkpoint.get('recorded_at'))).fetchone()['km']
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
    for driver,vehicle in changes:
        driver_notice(c,user,driver,vehicle,body.action,body.reason)
    return {'vehicle_id':vid,'action':body.action,'revision':body.revision+1}


def handover(c,user,car,old,target,body):
    if not old or not body.outgoing or not body.incoming:
        raise db.error(422,'HANDOVER_READINGS_REQUIRED','أكمل فحص وعداد وبنزين المركبة المسلّمة والمستلمة.')
    if body.outgoing.fuel_exception_id or body.incoming.fuel_exception_id:
        raise db.error(422,'HANDOVER_EXCEPTION_INVALID','سجّل القراءة الفعلية؛ استثناء البنزين يطلب بعد الاستلام.')
    shift=physical_driver_shift(c,user,target['id'])
    if shift['brand_id']!=user['brand_id']:
        raise db.error(404,'DRIVER_NOT_FOUND','المركبة الفعلية خارج نطاق البراند.')
    log=dict(shift.get('vehicle_log') or {})
    if plate(log.get('vehicle'))!=old['plate_key']:
        raise db.error(409,'VEHICLE_CHANGED','حدّث بيانات السائق؛ المركبة الحالية تغيرت.')
    floor=shift_vehicle.odometer_floor(log)
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
    driver_notice(c,user,target,car,'handover',body.reason)
    return {'vehicle_id':car['id'],'shift_id':shift['id'],'action':'handover','revision':body.revision+1}
