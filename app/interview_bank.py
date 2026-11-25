import base64
import gzip
import json
import os
from functools import lru_cache

from . import db


@lru_cache(maxsize=1)
def private_bank():
    packed = os.environ.get('INTERVIEW_BANK_GZIP')
    if not packed:
        raise db.error(503, 'BANK_CONFIG', 'محتوى المقابلات غير متاح.')
    return json.loads(gzip.decompress(base64.b64decode(packed)))
