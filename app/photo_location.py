"""Non-blocking photo GPS evidence. Device claims are never server attestation."""
from datetime import datetime, timezone
from pydantic import BaseModel, ConfigDict, Field


class CaptureLocation(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    captured_at: datetime
    source: str = Field(pattern='^(browser_camera|device_camera_return)$')
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    accuracy_m: float | None = Field(None, ge=0, le=100000)
    location_at: datetime | None = None


def classify_capture(body: CaptureLocation | None, now=None):
    now = now or datetime.now(timezone.utc)
    if body is None:
        return {'status': 'missing', 'basis': 'legacy_client', 'received_at': now.isoformat()}
    result = body.model_dump(mode='json')
    result.update(status='missing', basis='device_reported', received_at=now.isoformat())
    stamps = (body.captured_at, body.location_at)
    if any(t is not None and t.tzinfo is None for t in stamps):
        result['status'] = 'invalid_clock'
    elif not -5 <= (now-body.captured_at).total_seconds() <= 600:
        result['status'] = 'invalid_clock'
    elif body.source != 'browser_camera':
        # File chooser return is not the shutter time. Never label it as such.
        result['status'] = 'capture_time_unverified'
    elif all(x is not None for x in (body.lat, body.lng, body.accuracy_m, body.location_at)):
        delta = (body.captured_at-body.location_at).total_seconds()
        result['location_age_seconds'] = round(delta, 3)
        result['status'] = ('stale' if not 0 <= delta <= 15 else
                            'acceptable' if body.accuracy_m <= 200 else 'weak')
    return result
