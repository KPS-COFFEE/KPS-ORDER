"""Private interview sessions for an owner and two selected staff accounts."""
from .interview_bank import private_bank
import os
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb
from . import auth, db
from .staff_geo_access import _target

router = APIRouter()
BASE = Path(__file__).resolve().parent
DEADLINE = '2026-11-28T20:00:00Z'


def panel(c, user, *, lock=False):
    sql = """SELECT *, (closed OR expires_at<=clock_timestamp()) AS ended
             FROM interview_panels WHERE %s=ANY(member_ids)"""
    return c.execute(sql + (' FOR UPDATE' if lock else ''), (user['id'],)).fetchone()


class Members(BaseModel):
    members: list[str] = Field(min_length=2, max_length=2)


@router.post('/api/interviews/panel')
def open_panel(body: Members, user=Depends(auth.require_roles('owner'))):
    ids = [user['id'], *body.members]
    if len(set(ids)) != 3:
        raise db.error(422, 'PANEL_MEMBERS', 'اختر حسابين مختلفين.')
    with db.tx() as c:
        auth.require_owner_sms_step_up(c, user)
        c.execute("SELECT pg_advisory_xact_lock(41852901)")
        for uid in body.members:
            _target(c, uid, user)
        if c.execute("SELECT 1 FROM interview_panels WHERE member_ids && %s::text[]", (ids,)).fetchone():
            raise db.error(409, 'PANEL_EXISTS', 'توجد لجنة مرتبطة بأحد الحسابات.')
        r = c.execute("""INSERT INTO interview_panels(owner_id,member_ids,expires_at)
            SELECT %s,%s,least(now()+interval '10 days',%s::timestamptz)
            WHERE now()<%s::timestamptz RETURNING owner_id""",(ids[0],ids,DEADLINE,DEADLINE)).fetchone()
        if not r:
            raise db.error(409,'PANEL_EXPIRED','انتهت مدة المقابلات.')
    return {'ok':True}


def exam_prefix(c, panel):
    if panel.get('exam_code_prefix'):
        return panel['exam_code_prefix']
    configured = os.getenv('INTERVIEW_EXAM_CODE_PREFIX', '')
    if not configured:
        raise db.error(503,'EXAM_CONFIG','إعداد كلمات مرور المرشحين غير مكتمل.')
    return c.execute("""UPDATE interview_panels SET exam_code_prefix=COALESCE(exam_code_prefix,%s)
        WHERE owner_id=%s RETURNING exam_code_prefix""", (configured,panel['owner_id'])).fetchone()['exam_code_prefix']


def reserved_exam_lanes(c, owner_id):
    rows = c.execute("""SELECT DISTINCT e.exam_lane, e.group_opens_at AS opens FROM interview_exams e
        JOIN interview_rooms r ON r.id=e.room_id
        WHERE r.owner_id=%s AND e.exam_lane IS NOT NULL AND e.submitted_at IS NULL
          AND e.group_opens_at + interval '25 minutes' > clock_timestamp()
        ORDER BY opens, e.exam_lane""", (owner_id,)).fetchall()
    return rows


@router.get('/api/interviews')
def lobby(user=Depends(auth.current_user)):
    with db.tx() as c:
        p = panel(c,user)
        if not p:
            if user['role'] != 'owner':
                raise db.error(403,'PANEL_PRIVATE','اللجنة خاصة.')
            staff = c.execute("""SELECT id,display_name,role FROM users WHERE brand_id=%s AND active
                AND invitation_status='active' AND role<>'customer' AND id<>%s ORDER BY display_name""",
                (user['brand_id'],user['id'])).fetchall()
            return {'staff':staff}
        rooms = c.execute('SELECT r.id,r.exam_number,r.candidate,r.reviewing,e.room_id IS NOT NULL AS has_exam FROM interview_rooms r LEFT JOIN interview_exams e ON e.room_id=r.id WHERE r.owner_id=%s ORDER BY r.id DESC',(p['owner_id'],)).fetchall()
        if rooms:
            prefix = exam_prefix(c,p)
            for row in rooms:
                row['exam_code'] = prefix + str(row['exam_number'])
        return {'owner':p['owner_id']==user['id'],'closed':p['ended'],'rooms':rooms,'bookings':reserved_exam_lanes(c,p['owner_id']),'questions':private_bank()['finance'],'hr_questions':private_bank()['hr']}


@router.get('/ops/interviews')
def page(user=Depends(auth.current_user)):
    with db.tx() as c:
        if not panel(c,user) and user['role']!='owner':
            raise db.error(403,'PANEL_PRIVATE','اللجنة خاصة.')
    return FileResponse(BASE/'interviews.html',headers={'Cache-Control':'private, no-store'})


def require_panel(c,user):
    p=panel(c,user,lock=True)
    if not p or p['ended']:
        raise db.error(403,'PANEL_CLOSED','الغرفة مغلقة.')
    return p


class Candidate(BaseModel):
    track:Literal['finance','hr']='finance'
    name:str=Field(min_length=1,max_length=160)


@router.post('/api/interviews/rooms')
def create_room(body:Candidate,user=Depends(auth.current_user)):
    with db.tx() as c:
        p=require_panel(c,user)
        name = ' '.join(body.name.split())
        if len(name.split()) < 3 or not all(any(c.isalpha() for c in part) for part in name.split()):
            raise db.error(422,'NAME','أدخل الاسم الثلاثي للمرشح كما سيكتبه عند الدخول.')
        prefix = exam_prefix(c,p)
        row = c.execute("""INSERT INTO interview_rooms(owner_id,candidate,track,exam_number)
            SELECT %s,%s,%s,COALESCE(max(exam_number),0)+1 FROM interview_rooms WHERE owner_id=%s
            RETURNING id,exam_number""", (p['owner_id'],name,body.track,p['owner_id'])).fetchone()
        return dict(id=row['id'],exam_code=prefix+str(row['exam_number']))


@router.post('/api/interviews/close')
def close(user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        require_panel(c,user)
        c.execute('UPDATE interview_panels SET closed=true WHERE owner_id=%s',(user['id'],))
    return {'ok':True}


def room(c,rid,p):
    r=c.execute('SELECT * FROM interview_rooms WHERE id=%s AND owner_id=%s',(rid,p['owner_id'])).fetchone() if p else None
    if not r:
        raise db.error(404,'ROOM','الغرفة غير متاحة.')
    return r


@router.get('/api/interviews/rooms/{rid}')
def read_room(rid:int,user=Depends(auth.current_user)):
    with db.tx() as c:
        p=panel(c,user)
        r=room(c,rid,p)
        mine=c.execute('SELECT answers,submitted FROM interview_responses WHERE room_id=%s AND member_id=%s',(rid,user['id'])).fetchone()
        results=[]
        if user['id']==p['owner_id']:
            results=c.execute("""SELECT u.display_name,e.answers FROM interview_responses e JOIN users u ON u.id=e.member_id
                WHERE e.room_id=%s AND e.submitted""",(rid,)).fetchall()
        return {**r,'closed':p['ended'],'mine':mine,'results':results}


class Change(BaseModel):
    action:Literal['ask','review','save','submit']
    version:int=Field(ge=0)
    question:int=Field(default=0,ge=0,le=15)
    values:dict[str,str]=Field(default_factory=dict,max_length=15)


@router.post('/api/interviews/rooms/{rid}')
def change(rid:int,body:Change,user=Depends(auth.current_user)):
    with db.tx() as c:
        p=require_panel(c,user)
        r=room(c,rid,p)
        turns=r['turns']
        if body.action in ('ask','review'):
            if r['reviewing'] or r['version']!=body.version:
                raise db.error(409,'ROOM_UPDATED','تغيرت حالة المقابلة. حدّث العرض.')
            if body.action=='ask':
                if body.question==15:
                    raise db.error(422,'QUESTION','اختر سؤالًا.')
                turns=turns+[{'q':body.question,'speaker':user['display_name']}]
            c.execute('UPDATE interview_rooms SET turns=%s, reviewing=%s,version=version+1 WHERE id=%s',
                      (Jsonb(turns),body.action=='review',rid))
        else:
            current=c.execute('SELECT * FROM interview_responses WHERE room_id=%s AND member_id=%s',(rid,user['id'])).fetchone()
            if current and current['submitted']:
                raise db.error(409,'SUBMITTED','تم تسليم تقييمك.')
            answers=current['answers'] if current else {}
            fields={'choice','outcome','note','extra','answer','score'} if body.question<15 else {'score','traits','note','decision','bio'}
            if (set(body.values)-fields or any(len(v)>4000 for v in body.values.values()) or
                body.values.get('score','') not in ['',*map(str,range(11))]):
                raise db.error(422,'FIELDS','راجع بيانات التقييم.')
            if body.question<15 and not any(t['q']==body.question for t in turns):
                raise db.error(409,'UNASKED','السؤال لم يُطرح.')
            answers.setdefault(str(body.question),{}).update(body.values)
            submit=body.action=='submit'
            if submit and (not r['reviewing'] or not turns or not answers.get('15',{}).get('decision') or
                           any(not answers.get(str(n),{}).get('score') for n in {15,*[t['q'] for t in turns]})):
                raise db.error(409,'INCOMPLETE','أكمل درجات الأسئلة المطروحة والدرجة النهائية والتوصية.')
            c.execute("""INSERT INTO interview_responses VALUES (%s,%s,%s,%s) ON CONFLICT(room_id,member_id)
                      DO UPDATE SET answers=excluded.answers,submitted=excluded.submitted""",(rid,user['id'],Jsonb(answers),submit))
    return {'ok':True}


@router.get('/api/interviews/access')
def access(user=Depends(auth.current_user)):
    with db.tx() as c:
        return {'allowed':user['role']=='owner' or bool(panel(c,user))}
