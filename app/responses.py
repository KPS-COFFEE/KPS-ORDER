"""Encode already-authorized database projections once in the endpoint worker."""
import json

from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse


class DatabaseJSONResponse(JSONResponse):
    def render(self, content):
        # The standard encoder handles nested JSON scalars/containers directly.
        # Only database dates or other unsupported values need FastAPI's exact
        # conversion rules. Do not traverse every menu/snapshot field twice or
        # move that CPU work back onto the shared event-loop thread.
        return json.dumps(content, ensure_ascii=False, allow_nan=False,
                          indent=None, separators=(',', ':'),
                          default=jsonable_encoder).encode('utf-8')
