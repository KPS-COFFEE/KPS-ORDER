"""Authenticated ring metadata and audio for native driver clients."""
from __future__ import annotations

import functools
import hashlib
import io
import math
import re
import struct
import wave

from fastapi import APIRouter, Depends, Response
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


def selected_sound(user, brand_id):
    brand = brand_id or user['brand_id']
    with db.tx() as conn:
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', brand) or not multibrand.project(conn, user, brand):
            raise db.error(404, 'DRIVER_SOUND_NOT_FOUND', 'الصوت غير متاح ضمن صلاحياتك.')
        # Optional older installations stored a custom WAV per brand. The
        # standard rings require no new tables in the current application.
        has_custom_table = conn.execute(
            "SELECT to_regclass(format('%I.driver_order_audio', current_schema())) IS NOT NULL AS present"
        ).fetchone()['present']
        row = None
        if has_custom_table:
            row = conn.execute(
                'SELECT name,sha256,audio FROM driver_order_audio WHERE brand_id=%s', (brand,)
            ).fetchone()

    if row:
        data = bytes(row['audio'])
        if valid_wave(data) and hashlib.sha256(data).hexdigest() == row['sha256']:
            return descriptor(data, row['name'], False, brand), data
    return default_sound(brand)


@functools.lru_cache(maxsize=4)
def default_sound(brand):
    data = approved_wave(brand)
    name = ('Enbishan' if brand == 'enbishan' else 'KPS') + ' — جرس الطلب / Order ring'
    return descriptor(data, name, True, brand), data


def descriptor(data, name, default, brand):
    digest = hashlib.sha256(data).hexdigest()
    return {'name': name, 'sha256': digest, 'bytes': len(data), 'default': default, 'brand_id': brand,
            'path': f'/api/operations/driver-order-sound/brands/{brand}/audio/{digest}'}


@router.get('/operations/driver-order-sound')
def read_sound(response: Response, brand_id: str | None = None, user=Depends(work_user)):
    response.headers['Cache-Control'] = 'private, no-store'
    metadata, _ = selected_sound(user, brand_id)
    return {'sound': metadata}


@router.get('/operations/driver-order-sound/audio/{digest}')
@router.get('/operations/driver-order-sound/brands/{brand_id}/audio/{digest}')
def read_audio(digest: str, user=Depends(work_user), brand_id: str | None = None):
    if not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise db.error(404, 'DRIVER_SOUND_NOT_FOUND', 'الصوت غير متاح.')
    metadata, data = selected_sound(user, brand_id)
    if metadata['sha256'] != digest:
        raise db.error(404, 'DRIVER_SOUND_NOT_FOUND', 'الصوت غير متاح.')
    return Response(data, media_type='audio/wav', headers={'Cache-Control': 'private, no-store'})
