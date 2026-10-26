"""Owner-managed driver ring as bounded audio data, separate from bell timing."""
from __future__ import annotations

import base64
import functools
import hashlib
import io
import math
import re
import struct
import wave

from fastapi import APIRouter, Depends, Header, Response
from . import auth, db, multibrand

router = APIRouter(prefix='/api')
MAX_BYTES = 1_000_000
work_user = auth.require_roles('owner','admin','delivery_manager','delivery_staff','branch_manager','driver')


@functools.lru_cache(maxsize=4)
def approved_wave(brand_id='kps'):
    """Same original waveform as the owner's approved sample (not third-party audio)."""
    rate = 44100
    samples = [0.0] * (rate * 9)
    enbishan = brand_id == 'enbishan'
    groups = (0.15,2.60,5.05,7.50) if enbishan else (0.15,2.35,4.55,6.75)
    offsets = (0,0.55) if enbishan else (0,0.30,0.60)
    f1,f2,f3,speed = (560,780,1120,20) if enbishan else (740,960,1480,32)
    for group in groups:
        for offset in offsets:
            length = 0.38 if enbishan else 0.22
            for i in range(int(rate * length)):
                t = i / rate
                edge = min(t / 0.008, 1.0, (length - t) / 0.015)
                tremolo = 0.72 + 0.28 * math.sin(2 * math.pi * speed * t)
                signal = (0.55 * math.sin(2 * math.pi * f1 * t)
                          + 0.30 * math.sin(2 * math.pi * f2 * t)
                          + 0.10 * math.sin(2 * math.pi * f3 * t))
                samples[round((group + offset) * rate) + i] += 0.72 * edge * tremolo * signal
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        target.writeframes(b''.join(struct.pack('<h', round(x * 32767)) for x in samples))
    return output.getvalue()


def valid_wave(data):
    # Accept only canonical, metadata-free PCM WAV. Mirrors native SoundPolicy.
    if not 46 <= len(data) <= MAX_BYTES or data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        return False
    if struct.unpack_from('<I', data, 4)[0] + 8 != len(data):
        return False
    at, rate, frames, fmt, audio = 12, 0, 0, False, False
    while at + 8 <= len(data):
        kind, size = data[at:at+4], struct.unpack_from('<I', data, at+4)[0]
        end = at + 8 + size
        if end > len(data):
            return False
        if kind == b'fmt ':
            if fmt or size != 16:
                return False
            encoding, channels, rate, byte_rate, block, bits = struct.unpack_from('<HHIIHH', data, at+8)
            if (encoding, channels, block, bits) != (1, 1, 2, 16) or rate not in (22050,44100,48000) or byte_rate != rate*2:
                return False
            fmt = True
        elif kind == b'data':
            if audio or size < 2 or size % 2:
                return False
            frames, audio = size // 2, True
        else:
            return False
        at = end + (size & 1)
        if at != len(data) and at + 8 > len(data):
            return False
    return fmt and audio and rate // 10 <= frames <= rate * 10


def _sound(conn, brand_id):
    row = conn.execute('SELECT name,sha256,audio FROM driver_order_audio WHERE brand_id=%s', (brand_id,)).fetchone()
    if row:
        data = bytes(row['audio'])
        if valid_wave(data) and hashlib.sha256(data).hexdigest() == row['sha256']:
            return data, row['name'], False
    return approved_wave(brand_id), ('Enbishan' if brand_id=='enbishan' else 'KPS')+' — جرس الطلب / Order ring', True


def descriptor(data, name, default, brand_id):
    digest = hashlib.sha256(data).hexdigest()
    return {'name': name, 'sha256': digest, 'bytes': len(data), 'default': default, 'brand_id':brand_id,
            'path': '/api/operations/driver-order-sound/brands/'+brand_id+'/audio/' + digest}


def sound_scope(conn,user,brand_id):
    brand=brand_id or user['brand_id']
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}',brand) or not multibrand.project(conn,user,brand):
        raise db.error(404,'DRIVER_SOUND_NOT_FOUND','الصوت غير متاح ضمن صلاحياتك.')
    return brand


@router.get('/operations/driver-order-sound')
def read_sound(response: Response, brand_id: str | None=None, user=Depends(work_user)):
    response.headers['Cache-Control'] = 'private, no-store'
    with db.tx() as conn:
        brand=sound_scope(conn,user,brand_id)
        return {'sound': descriptor(*_sound(conn,brand),brand)}


@router.get('/operations/driver-order-sound/audio/{digest}')
def read_audio(digest: str, user=Depends(work_user), brand_id: str | None=None):
    if not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise db.error(404, 'DRIVER_SOUND_NOT_FOUND', 'الصوت غير متاح.')
    with db.tx() as conn:
        brand=sound_scope(conn,user,brand_id)
        data, _, _ = _sound(conn,brand)
    if hashlib.sha256(data).hexdigest() != digest:
        raise db.error(404, 'DRIVER_SOUND_NOT_FOUND', 'الصوت غير متاح.')
    return Response(data, media_type='audio/wav', headers={'Cache-Control':'private, no-store', 'X-Content-Type-Options':'nosniff'})


@router.get('/operations/driver-order-sound/brands/{brand_id}/audio/{digest}')
def read_brand_audio(brand_id: str, digest: str, user=Depends(work_user)):
    return read_audio(digest,user,brand_id)


@router.patch('/admin/driver-order-sound')
def update_sound(body: dict, brand_id: str | None=None, user=Depends(auth.require_roles('owner')),
                 idem: str | None=Header(None, alias='Idempotency-Key')):
    with db.tx() as conn:
        brand=sound_scope(conn,user,brand_id)
    reset = set(body)=={'reset'} and body.get('reset') is True
    if reset:
        data, name = approved_wave(brand), ('Enbishan' if brand=='enbishan' else 'KPS')+' — جرس الطلب / Order ring'
    else:
        if set(body) != {'name','data_base64'} or not isinstance(body.get('name'),str) or not isinstance(body.get('data_base64'),str):
            raise db.error(422, 'DRIVER_SOUND_INVALID', 'اختر ملف WAV صالحًا واسمًا للصوت.')
        name = body['name'].strip()
        if not 1 <= len(name) <= 80 or any(ord(c)<32 for c in name) or len(body['data_base64']) > 4*((MAX_BYTES+2)//3):
            raise db.error(422, 'DRIVER_SOUND_INVALID', 'حجم الملف أو اسمه غير صالح.')
        try:
            data = base64.b64decode(body['data_base64'], validate=True)
        except ValueError:
            raise db.error(422, 'DRIVER_SOUND_INVALID', 'ملف الصوت غير صالح.') from None
        if not valid_wave(data):
            raise db.error(422, 'DRIVER_SOUND_INVALID', 'يلزم WAV أحادي PCM 16-bit، من 0.1 إلى 10 ثوانٍ، وأقل من 1 MB.')
    meta = descriptor(data, name, reset,brand)
    fingerprint = {'sha256':meta['sha256'], 'name':name, 'reset':reset,'brand_id':brand}
    with db.tx() as conn:
        def perform():
            sound_scope(conn,user,brand)
            conn.execute('SELECT id FROM brands WHERE id=%s FOR UPDATE', (brand,)).fetchone()
            old = conn.execute('SELECT sha256 FROM driver_order_audio WHERE brand_id=%s', (brand,)).fetchone() or {}
            if reset:
                conn.execute('DELETE FROM driver_order_audio WHERE brand_id=%s', (brand,))
            else:
                conn.execute('INSERT INTO driver_order_audio(brand_id,name,sha256,audio,updated_by) VALUES(%s,%s,%s,%s,%s) '
                             'ON CONFLICT(brand_id) DO UPDATE SET name=EXCLUDED.name,sha256=EXCLUDED.sha256,'
                             'audio=EXCLUDED.audio,updated_by=EXCLUDED.updated_by,updated_at=now()',
                             (brand,name,meta['sha256'],data,user['id']))
            db.audit(conn,user['id'],'driver.sound.updated','brand',brand,
                     {'before_sha256':old.get('sha256'),'after':fingerprint})
            return {'sound':meta}
        return db.idempotent(conn,user['id'],idem,'driver.sound.update:'+brand,fingerprint,perform)
