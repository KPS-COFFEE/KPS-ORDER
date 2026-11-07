"""Display-sized derivatives. Originals are never overwritten."""
import hashlib
import io
import re
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageOps
from fastapi import APIRouter
from fastapi.responses import Response, FileResponse
from . import db

router=APIRouter()
ASSETS=Path(__file__).resolve().parents[1]/'public'/'assets'

@lru_cache(maxsize=8)
def thumbnail(contents:bytes)->bytes:
    with Image.open(io.BytesIO(contents)) as original:
        image=ImageOps.exif_transpose(original)
        image.thumbnail((720,720),Image.Resampling.LANCZOS)
        if image.mode not in ('RGB','RGBA'):image=image.convert('RGBA' if 'transparency' in image.info else 'RGB')
        output=io.BytesIO();image.save(output,format='WEBP',quality=90,method=4)
        return output.getvalue()

def response(contents:bytes):
    return Response(contents,media_type='image/webp',headers={'Cache-Control':'public, max-age=86400','ETag':'"'+hashlib.sha256(contents).hexdigest()+'"'})

@router.get('/media/previews/{filename:path}')
def static_preview(filename:str):
    if not re.fullmatch(r'(?:enbishan-menu/(?:[A-Za-z0-9_-]+/)*)?[A-Za-z0-9_.-]+\.(?:jpg|jpeg|png|webp)',filename):
        raise db.error(404,'IMAGE_NOT_FOUND','الصورة غير متاحة.')
    path=ASSETS/filename
    if not path.is_file() or path.resolve()!=path:raise db.error(404,'IMAGE_NOT_FOUND','الصورة غير متاحة.')
    built=ASSETS.parent/'previews'/(filename+'.webp')
    if built.is_file():return FileResponse(built,media_type='image/webp',headers={'Cache-Control':'public, max-age=86400'})
    return response(thumbnail(path.read_bytes()))

if __name__=='__main__':
    destination=ASSETS.parent/'previews';destination.mkdir(exist_ok=True)
    for path in (*ASSETS.iterdir(), *(ASSETS/'enbishan-menu').rglob('*')):
        if path.is_file() and path.resolve()==path and path.suffix.lower() in ('.jpg','.jpeg','.png','.webp'):
            target=destination/(path.relative_to(ASSETS).as_posix()+'.webp')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(thumbnail(path.read_bytes()))
    thumbnail.cache_clear()
