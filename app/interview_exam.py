import secrets
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta
from typing import Literal
from pathlib import Path
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb
from . import auth, db, interviews
from .interview_exam_paper import grade, candidate_copy
from .interview_exam_choices import choice_paper

router = APIRouter()
EXAM_DURATION = timedelta(minutes=25)


@router.post('/api/interviews/rooms/{rid}/exam')
def issue(rid:int,user=Depends(auth.current_user)):
    with db.tx() as c:
        p=interviews.require_panel(c,user)
        room=interviews.room(c,rid,p)
        if not p['exam_opens_at'] and not room['reviewing']:
            raise db.error(409,'INTERVIEW_ACTIVE','أنهوا الأسئلة الشفهية أولًا.')
        if p['exam_opens_at'] and c.execute('SELECT clock_timestamp()>=%s AS started',(p['exam_opens_at'],)).fetchone()['started']:
            raise db.error(409,'BATCH_STARTED','بدأت الدفعة بالفعل.')
        token=secrets.token_urlsafe(32)
        tasks=choice_paper()
        created=c.execute("""INSERT INTO interview_exams(room_id,token_hash,paper) VALUES (%s,%s,%s)
            ON CONFLICT(room_id) DO UPDATE SET token_hash=excluded.token_hash,paper=excluded.paper
            WHERE interview_exams.started_at IS NULL AND interview_exams.group_opens_at IS NULL RETURNING room_id""",
            (rid,auth.token_hash(token),Jsonb(tasks))).fetchone()
        if not created:
            raise db.error(409,'EXAM_STARTED','سبق أن بدأ المرشح الاختبار.')
        return {'url':'/candidate-exam#'+token}


@router.get('/api/interviews/rooms/{rid}/exam')
def result(rid:int,user=Depends(auth.current_user)):
    with db.tx() as c:
        p=interviews.panel(c,user)
        interviews.room(c,rid,p)
        row=c.execute("""SELECT *, deadline<=clock_timestamp() AS expired FROM interview_exams WHERE room_id=%s""",(rid,)).fetchone()
        if not row:return {'status':'لم يصدر رابط'}
        if row['submitted_at'] or row['expired'] or p['ended'] and row['started_at']:
            return {'status':'انتهى الاختبار','result':grade(row['paper'],row['answers'])}
        return {'status':'جارٍ الحل' if row['started_at'] else 'لم يبدأ'}


@router.get('/candidate-exam')
def exam_page():
    return FileResponse(Path(__file__).with_name('candidate_exam.html'),headers={'Cache-Control':'private, no-store'})


class Attempt(BaseModel):
    name:str=Field(default='',max_length=160)
    password:str=Field(default='',max_length=128,repr=False)
    action:Literal['view','start','save','submit','next']
    revision:int=Field(default=0,ge=0)
    answers:dict[str,str]=Field(default_factory=dict,max_length=15)


def sequential_step(c, exam, body, now):
    questions = [(section, task, field) for section, task in enumerate(exam['paper'], 1)
                 for field in task['fields']]
    index = len(exam['answers'])
    ended = {'status': 'ended', 'candidate': exam['candidate']}
    if index >= len(questions):
        return ended
    if body.action not in ('view', 'start'):
        section, task, field = questions[index]
        key = field[0]
        if body.revision != exam['revision']:
            raise db.error(409, 'STALE', 'سبق الانتقال من هذا السؤال. حدّث الصفحة لعرض السؤال الحالي.')
        if (body.action != 'next' or set(body.answers) != {key}
                or body.answers[key] not in [str(i) for i in range(1, len(task['choices'][key]) + 1)]):
            raise db.error(422, 'CURRENT_QUESTION', 'اختر إجابة للسؤال المعروض ثم اضغط التالي.')
        answers = {**exam['answers'], key: body.answers[key]}
        index += 1
        exam['revision'] += 1
        c.execute('UPDATE interview_exams SET answers=%s,revision=%s,submitted_at=%s WHERE room_id=%s',
                  (Jsonb(answers), exam['revision'], now if index == len(questions) else None, exam['room_id']))
        if index == len(questions):
            return ended
    section, task, field = questions[index]
    return dict(status='active', candidate=exam['candidate'], seconds=(exam['deadline']-now).total_seconds(),
                revision=exam['revision'], step=dict(section=section, sections=len(exam['paper']),
                number=index+1, total=len(questions), title=task['title'], prompt=task['prompt'],
                key=field[0], question=field[1], choices=task['choices'][field[0]]))


@router.post('/api/candidate-exam')
def attempt(body:Attempt,request:Request,x_exam_token:str=Header(default='',max_length=64)):
    auth.check_origin(request)
    with db.tx() as c:
        exam=c.execute("""SELECT e.*,r.candidate,r.exam_number,p.owner_id,p.exam_code_prefix,p.closed,p.expires_at,p.exam_opens_at
          FROM interview_exams e JOIN interview_rooms r ON r.id=e.room_id JOIN interview_panels p ON p.owner_id=r.owner_id
          WHERE e.token_hash=%s FOR UPDATE OF e,p""",(auth.token_hash(x_exam_token),)).fetchone()
        if not exam:raise db.error(404,'EXAM_LINK','رابط الاختبار غير متاح.')
        entered_name = ' '.join(body.name.split()).casefold()
        if not entered_name:
            return {'status':'identify'}
        expected = interviews.exam_prefix(c,exam) + str(exam['exam_number'])
        password_ok = secrets.compare_digest(body.password.encode(), expected.encode())
        if not password_ok or entered_name != ' '.join(exam['candidate'].split()).casefold():
            raise db.error(403,'EXAM_LOGIN','تحقق من الاسم الثلاثي وكلمة مرور الاختبار.')
        now=c.execute('SELECT clock_timestamp() AS t').fetchone()['t']
        closed=exam['closed'] or now>=exam['expires_at']
        if closed or exam['submitted_at'] or exam['deadline'] and now>=exam['deadline']:
            return {'status':'ended','candidate':exam['candidate']}
        opens=exam['group_opens_at'] or exam['exam_opens_at']
        if opens:
            if now<opens:
                return {'status':'waiting','candidate':exam['candidate'],'opens':opens,'seconds':(opens-now).total_seconds()}
            if now>=opens+EXAM_DURATION:
                return {'status':'ended','candidate':exam['candidate']}
            if not exam['started_at']:
                exam['deadline']=opens+EXAM_DURATION
                c.execute('UPDATE interview_exams SET started_at=%s,deadline=%s WHERE room_id=%s',(now,exam['deadline'],exam['room_id']))
        elif body.action=='start' and not exam['started_at']:
            exam['deadline']=min(now+EXAM_DURATION,exam['expires_at'])
            c.execute('UPDATE interview_exams SET started_at=%s,deadline=%s WHERE room_id=%s',(now,exam['deadline'],exam['room_id']))
        if not exam['deadline']:
            return {'status':'ready','candidate':exam['candidate']}
        if exam['paper'][0].get('sequential'):
            return sequential_step(c, exam, body, now)
        if body.action in ('save','submit'):
            fields={f[0] for t in exam['paper'] for f in t['fields']}
            if body.revision!=exam['revision']:raise db.error(409,'STALE','تغيرت الإجابات من نافذة أخرى. حدّث الصفحة.')
            if set(body.answers)-fields or any(len(v)>20 for v in body.answers.values()):raise db.error(422,'FIELDS','بيانات إجابة غير صالحة.')
            exam['answers'].update(body.answers)
            exam['revision']+=1
            c.execute('UPDATE interview_exams SET answers=%s,revision=%s,submitted_at=%s WHERE room_id=%s',
                      (Jsonb(exam['answers']),exam['revision'],now if body.action=='submit' else None,exam['room_id']))
            if body.action=='submit':return {'status':'ended','candidate':exam['candidate']}
        return {'status':'active','candidate':exam['candidate'],'seconds':max(0,(exam['deadline']-now).total_seconds()),
                'paper':candidate_copy(exam['paper']),'answers':exam['answers'],'revision':exam['revision']}


class GroupExam(BaseModel):
    lane: int | None = Field(default=None, ge=1, le=4)
    rooms: list[int] = Field(min_length=2, max_length=100)
    delay: int = Field(default=30, ge=1, le=30)
    cairo_start: datetime | None = None


@router.post('/api/interviews/groups')
def create_group(body: GroupExam, user=Depends(auth.current_user)):
    with db.tx() as c:
        panel = interviews.require_panel(c, user)
        now = c.execute('SELECT clock_timestamp() AS t').fetchone()['t']
        opens = now + timedelta(minutes=body.delay)
        if body.cairo_start is not None:
            opens = body.cairo_start
            if opens.tzinfo is None:
                opens = opens.replace(tzinfo=ZoneInfo('Africa/Cairo'))
        if opens <= now:
            raise db.error(422, 'PAST_TIME', 'اختر موعدًا مستقبليًا بتوقيت مصر.')
        if panel['exam_opens_at'] or opens + EXAM_DURATION > panel['expires_at']:
            raise db.error(409, 'GROUP_TIME', 'تعذر تخصيص موعد مستقل يكفي للاختبار قبل الإقفال.')
        if len(set(body.rooms)) != len(body.rooms):
            raise db.error(422, 'GROUP_DUPLICATE', 'اختر كل مرشح مرة واحدة.')
        bookings = interviews.reserved_exam_lanes(c, panel['owner_id'])
        occupied = {b['exam_lane'] for b in bookings if
                    opens < b['opens'] + EXAM_DURATION and b['opens'] < opens + EXAM_DURATION}
        available = [n for n in range(1, 5) if n not in occupied]
        lane = body.lane if body.lane is not None else next(iter(available), None)
        if lane not in available:
            raise db.error(409, 'LANE_BUSY', 'اللجنة محجوزة. حدّث قائمة اللجان واختر لجنة متاحة.')
        links = []
        for rid in body.rooms:
            room = interviews.room(c, rid, panel)
            token = secrets.token_urlsafe(32)
            saved = c.execute("""INSERT INTO interview_exams(room_id,token_hash,paper,group_opens_at,exam_lane)
                VALUES (%s,%s,%s,%s,%s) ON CONFLICT(room_id) DO NOTHING RETURNING room_id""",
                (rid, auth.token_hash(token), Jsonb(choice_paper()), opens, lane)).fetchone()
            if not saved:
                raise db.error(409, 'EXAM_EXISTS', 'سبق تخصيص اختبار لأحد المختارين. راجع القائمة.')
            links.append(dict(room_id=rid,candidate=room['candidate'], url='/candidate-exam#'+token))
        return dict(opens=opens, lane=lane, links=links)


class BatchSchedule(BaseModel):
    opens: datetime


@router.post('/api/interviews/batch')
def schedule(body:BatchSchedule,user=Depends(auth.require_roles('owner'))):
    with db.tx() as c:
        p=interviews.require_panel(c,user)
        now=c.execute('SELECT clock_timestamp() AS t').fetchone()['t']
        if p['owner_id']!=user['id'] or body.opens.tzinfo is None or not now<body.opens<=p['expires_at']-EXAM_DURATION:
            raise db.error(422,'BATCH_TIME','حدّد موعدًا مستقبليًا يكفي لاختبار كامل قبل إقفال الدورة.')
        if p['exam_opens_at'] or c.execute('SELECT 1 FROM interview_rooms r JOIN interview_exams e ON r.id=e.room_id WHERE r.owner_id=%s AND (e.started_at IS NOT NULL OR e.group_opens_at IS NOT NULL) LIMIT 1',(user['id'],)).fetchone():
            raise db.error(409,'BATCH_LOCKED','سبق تثبيت الموعد أو بدأ اختبار فردي.')
        c.execute('UPDATE interview_panels SET exam_opens_at=%s WHERE owner_id=%s',(body.opens,user['id']))
        return {'opens':body.opens}


@router.get('/api/interviews/batch/results')
def batch_results(user=Depends(auth.current_user)):
    with db.tx() as c:
        p=interviews.panel(c,user)
        if not p:
            raise db.error(403,'PANEL_PRIVATE','اللجنة خاصة.')
        now=c.execute('SELECT clock_timestamp() AS t').fetchone()['t']
        opens=p['exam_opens_at']
        done=p['ended'] or bool(opens and now>=opens+EXAM_DURATION)
        rows=c.execute("""SELECT r.candidate,e.* FROM interview_exams e JOIN interview_rooms r ON r.id=e.room_id
            WHERE r.owner_id=%s ORDER BY r.exam_number""",(p['owner_id'],)).fetchall()
        results=[]
        for r in rows:
            finished=done or bool(r['submitted_at'] or r['deadline'] and now>=r['deadline'])
            if not r['started_at']:
                status='غائب' if done or (r['group_opens_at'] and now >= r['group_opens_at']+EXAM_DURATION) else 'بانتظار البدء'
            else:
                status='سلّم' if r['submitted_at'] else 'انتهى الوقت' if finished else 'جارٍ الحل'
            report=grade(r['paper'],r['answers']) if finished and r['started_at'] else None
            results.append(dict(candidate=r['candidate'],status=status,score=report['points'] if report else None, maximum=report['maximum'] if report else None, sections=report['sections'] if report else []))
        results.sort(key=lambda r: -(r['score']/r['maximum'] if r['score'] is not None else -1))
        prev=None
        for idx,r in enumerate(results,1):
            if r['score'] is None:break
            fraction=r['score']/r['maximum']
            if fraction!=prev:rank=idx
            r['rank']=rank
            prev=fraction
        return {'opens':opens,'results':results}


@router.get('/api/interviews/batch/results.pdf')
def results_pdf(user=Depends(auth.current_user)):
    from fastapi.responses import Response
    from .interview_results_pdf import render
    rows = [r for r in batch_results(user)['results'] if r['score'] is not None]
    if not rows:
        raise db.error(409, 'NO_RESULTS', 'يُتاح الكشف بعد انتهاء أول اختبار.')
    return Response(render(rows), media_type='application/pdf', headers={
        'Content-Disposition':'attachment; filename="KPS-Exam-Results.pdf"', 'Cache-Control':'private, no-store'})
