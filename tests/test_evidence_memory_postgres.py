"""High-resolution evidence retains scope, replay, durable bytes and delivery."""
import base64
import io

import pytest
from PIL import Image

from app import db
from test_operations import ops


@pytest.mark.postgres
@pytest.mark.parametrize('format,mime', [('JPEG', 'image/jpeg'), ('PNG', 'image/png'), ('WEBP', 'image/webp')])
def test_large_phone_photo_replay_and_delivery(ops, format, mime):
    actor, post, order, shifts = ops
    manager, driver = shifts()
    oid = order()
    task = post('manager', f'/api/orders/{oid}/assign', {'driver_shift_id': driver})['task']['id']
    post('driver', f'/api/tasks/{task}/transition', {'status': 'accepted'})
    with db.tx() as conn:
        conn.execute("UPDATE orders SET preparation_status='handed_over' WHERE id=%s", (oid,))
    with io.BytesIO() as raw:
        with Image.new('RGB', (4000, 4000), 'white') as image:
            image.save(raw, format)
        encoded = base64.b64encode(raw.getvalue()).decode()
    for stage in ('picked_up', 'delivered'):
        payload = {'stage': stage, 'content_type': mime, 'data_base64': encoded}
        path = f'/api/tasks/{task}/photos'
        photo = post('driver', path, payload, key='large-phone-' + stage)
        assert post('driver', path, payload, key='large-phone-' + stage) == photo
        assert actor('other').get('/api/delivery-photos/' + photo['id']).status_code == 404
        response = actor('manager').get('/api/delivery-photos/' + photo['id'])
        assert response.status_code == 200
        with Image.open(io.BytesIO(response.content)) as saved:
            assert saved.format == 'JPEG' and saved.size == (2000, 2000)
        post('driver', f'/api/tasks/{task}/transition', {'status': stage, 'photo_id': photo['id']})
        if stage == 'picked_up':
            post('driver', f'/api/orders/{oid}/collect', {'method': 'cash', 'amount_halalas': 1000, 'tendered_halalas': 1000})
    with db.tx() as conn:
        assert conn.execute('SELECT count(*) n FROM delivery_photos WHERE task_id=%s AND used_at IS NOT NULL', (task,)).fetchone()['n'] == 2
        assert conn.execute('SELECT status FROM driver_tasks WHERE id=%s', (task,)).fetchone()['status'] == 'delivered'
        assert conn.execute('SELECT count(*) n FROM collections WHERE order_id=%s', (oid,)).fetchone()['n'] == 1
