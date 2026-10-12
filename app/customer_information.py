"""Canonical customer information; no providers, GPS, or fabricated ratings."""
import math
import re
from urllib.parse import urlsplit
from . import db, catalog

SOCIAL_HOSTS = {'instagram.com','www.instagram.com','x.com','www.x.com','twitter.com','www.twitter.com','tiktok.com','www.tiktok.com','snapchat.com','www.snapchat.com','wa.me','www.whatsapp.com','api.whatsapp.com'}
GOOGLE_HOSTS = catalog.MAP_HOSTS | {'g.page','search.google.com'}

def safe_link(value, kind='social'):
    if not isinstance(value,str) or len(value)>1500 or any(ord(c)<33 for c in value) or '\\' in value:
        return None
    try:
        p=urlsplit(value)
        if p.username or p.password or p.port not in (None,443): return None
        if kind=='contact' and p.scheme=='tel' and re.fullmatch(r'\+?[0-9]{7,15}',p.path): return value
        if kind=='contact' and p.scheme=='mailto' and re.fullmatch(r'[^?\s@]+@[^?\s@]+\.[^?\s@]+',p.path): return value
        hosts=GOOGLE_HOSTS if kind in {'review','maps'} else SOCIAL_HOSTS
        if p.scheme=='https' and p.hostname in hosts: return value
    except ValueError: pass
    return None


def validate_information(value):
    if not isinstance(value,dict) or set(value)-{'review_url','contact_links','google_rating','google_profile_url','name_en','city_en','opening_information'}:
        raise db.error(422,'CUSTOMER_INFORMATION_INVALID','بيانات الفرع غير صالحة.')
    for key in ('review_url','google_profile_url'):
        if value.get(key) is not None and not safe_link(value[key],'review'):
            raise db.error(422,'CUSTOMER_LINK_INVALID','رابط Google غير صالح.')
    for key in ('name_en','city_en','opening_information'):
        if key in value and (not isinstance(value[key],str) or len(value[key])>500):
            raise db.error(422,'CUSTOMER_INFORMATION_INVALID','النص غير صالح.')
    if 'contact_links' in value: validate_links(value['contact_links'])
    rating=value.get('google_rating')
    if rating is not None:
        if not isinstance(rating,dict) or set(rating)-{'value','review_count','as_of','profile_url','source'}:
            raise db.error(422,'GOOGLE_RATING_INVALID','بيانات التقييم غير صالحة.')
        if (type(rating.get('value')) not in (int,float) or not math.isfinite(rating['value']) or not 0<=rating['value']<=5
            or type(rating.get('review_count')) is not int or rating['review_count']<0
            or rating.get('source')!='approved_cached' or not safe_link(rating.get('profile_url'),'review')):
            raise db.error(422,'GOOGLE_RATING_INVALID','يلزم تقييم موثق ومصدره وتاريخ تحديثه.')
        from datetime import datetime, timezone
        try:
            stamp=datetime.fromisoformat(rating.get('as_of','').replace('Z','+00:00'))
            if stamp.tzinfo is None or stamp>datetime.now(timezone.utc): raise ValueError()
        except (ValueError,TypeError): raise db.error(422,'GOOGLE_RATING_INVALID','تاريخ التقييم غير صالح.')
    return value


def validate_links(values):
    if not isinstance(values,list) or len(values)>20:
        raise db.error(422,'CUSTOMER_LINK_INVALID','روابط التواصل غير صالحة.')
    for item in values:
        if (not isinstance(item,dict) or set(item)-{'id','label','label_en','url','kind'}
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',str(item.get('id','')))
            or not isinstance(item.get('label'),str) or not 1<=len(item['label'])<=200
            or item.get('kind') not in {'social','contact','maps'} or not safe_link(item.get('url'),item['kind'])
            or ('label_en' in item and (not isinstance(item['label_en'],str) or len(item['label_en'])>200))):
            raise db.error(422,'CUSTOMER_LINK_INVALID','رابط التواصل غير صالح.')
    if len({x['id'] for x in values})!=len(values): raise db.error(422,'CUSTOMER_LINK_INVALID','معرف الرابط مكرر.')
    return values


def public_information(branch):
    value=branch['settings'].get('customer_information') or {}
    # Legacy corrupt or unverified values fail closed. They remain stored.
    try: validate_information(value)
    except Exception: value={}
    result={k:value[k] for k in ('name_en','city_en','opening_information','contact_links','google_profile_url') if k in value}
    result['review_url']=safe_link(value.get('review_url'),'review')
    rating=value.get('google_rating')
    identity=value.get('google_profile_url') or value.get('review_url')
    result['google_rating']=rating if rating and rating.get('profile_url')==identity else None
    return result


def import_approved_links(conn):
    """One-time snapshot import after canonical seed, preserving later admin edits."""
    from pathlib import Path
    if not conn.execute("SELECT 1 FROM brands WHERE id='kps'").fetchone(): return
    inserted=conn.execute("INSERT INTO menu_link_imports(brand_id) VALUES('kps') ON CONFLICT DO NOTHING RETURNING brand_id").fetchone()
    if inserted:
        conn.execute((Path(__file__).resolve().parents[1]/'data'/'menu_customer_links.sql').read_text())
        db.audit(conn,None,'menu.approved_links_imported','brand','kps',{'source':'https://menu.kps.sa/','observed_date':'2026-10-12','ratings_present':False,'review_links':20})
