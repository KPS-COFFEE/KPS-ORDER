"""Authorized physical shift history, grouped before counting and pagination."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query

from . import auth, db, multibrand, central_workdays as days, shift_order_counts
from . import operations as ops

router = APIRouter(tags=['central-shift-history'])
PAGE_SIZE = 50


def collapse(rows):
    """Only authorized constituent rows enter this projection."""
    anchor = next((r for r in rows if r['id'] == r.get('central_anchor_id')), rows[0])
    if len(rows) == 1 and not anchor.get('central_workday_id'):
        return anchor
    result = dict(anchor, branch_ids=list(dict.fromkeys(b for r in rows for b in r['branch_ids'])),
                  central_legs=[{'id':r['id'],'brand_id':r['brand_id'],'branch_ids':r['branch_ids']} for r in rows],
                  operational_brands=[r['brand_id'] for r in rows])
    for key in set().union(*(r.keys() for r in rows)):
        if key.endswith('_halalas') and any(isinstance(r.get(key),int) for r in rows):
            result[key]=sum(r.get(key) or 0 for r in rows)
    for name in ('submission','closure'):
        if any(r.get(name) for r in rows):
            result[name]=dict(anchor.get(name) or {})
            for key in set().union(*((r.get(name) or {}).keys() for r in rows)):
                if key.endswith('_halalas') and any(isinstance((r.get(name) or {}).get(key),int) for r in rows):
                    result[name][key]=sum((r.get(name) or {}).get(key) or 0 for r in rows)
    if any(r.get('permanent_float') for r in rows):
        result['permanent_float']={'amount':sum((r.get('permanent_float') or {}).get('amount',0) for r in rows)}
    result['order_counts']={key:sum((r.get('order_counts') or {}).get(key,0) for r in rows)
                            for key in shift_order_counts.KEYS}
    return result


@router.get('/shifts')
def history(owner_view: Literal['current','review','history'], search: str='',
            start: date | None=None, end: date | None=None, offset: int=Query(0,ge=0),
            user=Depends(auth.current_user)):
    if not multibrand.enabled() or user['role']=='customer' or user.get('_morning_manager_context'):
        raise db.error(404,'CENTRAL_WORKDAY_UNAVAILABLE','الوردية غير متاحة.')
    ops.shift_roles(user,'shifts_view','owner')
    if start and end and start>end:
        ops.fail(422,'تاريخ البداية بعد النهاية')
    with db.tx() as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        existing=c.execute('SELECT anchor_shift_id FROM central_workdays WHERE user_id=%s OR %s LIMIT 1',
                           (user['id'],user['role']=='owner' or ops.delegated_shift(user,'shifts_view'))).fetchone()
        days.require_enabled(user,'shifts_view',existing_sid=existing['anchor_shift_id'] if existing else None)
        principals={}
        scopes=[]
        scope_params=[]
        for brand in multibrand.available(c,user):
            principal=multibrand.project(c,user,brand['id'])
            if not principal:
                continue
            principals[brand['id']]=principal
            if principal['role']=='owner' and not principal.get('home_brand_id'):
                scopes.append('(s.brand_id=%s)')
                scope_params.append(brand['id'])
            else:
                scopes.append("(s.brand_id=%s AND s.branch_ids <> '[]'::jsonb AND s.branch_ids <@ %s::jsonb)")
                scope_params.extend([brand['id'],db.json(principal.get('branch_ids',[]))])
        if not scopes:
            return {'shifts':[],'total':0,'offset':offset,'next_offset':None}
        filters=['v.status=ANY(%s)']
        statuses={'current':['open'],'review':['closing','submitted'],'history':['closed']}[owner_view]
        params=scope_params+[statuses]
        if search.strip():
            filters.append('(strpos(lower(v.display_name),lower(%s))>0 OR strpos(lower(v.id),lower(%s))>0)')
            params.extend([search.strip(),search.strip()])
        day="COALESCE(v.delivery_operational_date, ((v.opened_at AT TIME ZONE 'Asia/Riyadh') - interval '16 hours')::date)"
        if start:
            filters.append(day+'>=%s');params.append(start)
        if end:
            filters.append(day+'<=%s');params.append(end)
        # Every returned leg comes from a fresh, explicit canonical brand scope.
        # A partially visible physical day cannot be opened or safely acted on;
        # omit the whole group rather than returning an incomplete ledger sum.
        source=('WITH visible AS (SELECT s.*,u.display_name,l.workday_id,w.anchor_shift_id,'
                "coalesce('day:'||w.id,'shift:'||s.id) AS group_key "
                'FROM shifts s JOIN users u ON u.id=s.user_id '
                'LEFT JOIN central_workday_legs l ON l.shift_id=s.id '
                'LEFT JOIN central_workdays w ON w.id=l.workday_id WHERE ('+' OR '.join(scopes)+')),'
                ' complete AS (SELECT v.* FROM visible v WHERE v.workday_id IS NULL OR NOT EXISTS '
                '(SELECT 1 FROM central_workday_legs x WHERE x.workday_id=v.workday_id '
                'AND NOT EXISTS (SELECT 1 FROM visible y WHERE y.id=x.shift_id))),'
                ' matched AS (SELECT DISTINCT v.group_key FROM complete v WHERE '+' AND '.join(filters)+'),'
                ' grouped AS (SELECT v.group_key,max(v.opened_at) FILTER '
                '(WHERE v.id=coalesce(v.anchor_shift_id,v.id)) AS opened_at,'
                'max(coalesce(v.anchor_shift_id,v.id)) AS anchor_id FROM complete v '
                'JOIN matched m USING(group_key) GROUP BY v.group_key) ')
        total=c.execute(source+'SELECT count(*) AS n FROM grouped',params).fetchone()['n']
        page=c.execute(source+'SELECT * FROM grouped ORDER BY opened_at DESC,anchor_id LIMIT %s OFFSET %s',
                       params+[PAGE_SIZE,offset]).fetchall()
        keys=[r['group_key'] for r in page]
        rows=c.execute(source+'SELECT v.* FROM complete v WHERE v.group_key=ANY(%s) ORDER BY v.brand_id,v.id',
                       params+[keys]).fetchall() if keys else []
        for brand,principal in principals.items():
            by_id={r['id']:r for r in rows if r['brand_id']==brand}
            for row in by_id.values():
                row['order_counts']=dict.fromkeys(shift_order_counts.KEYS,0)
            if by_id:
                for item in c.execute(shift_order_counts.QUERY,
                    (list(by_id),brand,principal['role']=='owner' and not principal.get('home_brand_id'),principal.get('branch_ids',[]))).fetchall():
                    counts=by_id[item['shift_id']]['order_counts']
                    counts[item['bucket']]+=item['count'];counts['total']+=item['count']
        days.annotate(c,rows)
        grouped={key:[] for key in keys}
        for row in rows:
            key=row.pop('group_key')
            row.pop('workday_id',None);row.pop('anchor_shift_id',None)
            grouped[key].append(row)
        return ops.safe({'shifts':[collapse(grouped[key]) for key in keys], 'total':total,'offset':offset,
                         'next_offset':offset+PAGE_SIZE if offset+PAGE_SIZE<total else None})
