"""Real decoder peak-memory and cross-process admission regression witnesses."""
import fcntl
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest
from PIL import Image

from app.evidence_images import evidence_jpeg


@pytest.mark.parametrize('mode,format', [('RGB', 'JPEG'), ('RGBA', 'PNG'), ('RGB', 'WEBP')])
def test_phone_image_decoder_peak_is_bounded(tmp_path, mode, format):
    path = tmp_path / 'phone-image'
    with Image.new(mode, (4000, 4000), 'white') as image:
        image.save(path, format)
    script = '''import json, sys
from app.evidence_images import evidence_jpeg
data = evidence_jpeg(open(sys.argv[1], 'rb').read(), sys.argv[2])
# Linux ru_maxrss can include the parent's pre-exec high-water mark. VmHWM
# measures this executed decoder, excluding the fixture's WebP encoder peak.
peak = int(next(line.split()[1] for line in open('/proc/self/status') if line.startswith('VmHWM:')))
print(json.dumps({'peak_kib': peak, 'bytes': len(data)}))
'''
    result = subprocess.run([sys.executable, '-c', script, str(path), format],
                            check=True, capture_output=True, text=True)
    observation = json.loads(result.stdout)
    # Original 16MP JPEG/PNG pipeline peaks above 190 MiB in this isolated
    # process. The bound includes Python, Pillow and decoded native buffers.
    assert observation['peak_kib'] < 160 * 1024, observation
    assert observation['bytes'] > 0


@pytest.mark.parametrize('format', ['JPEG', 'PNG', 'WEBP'])
@pytest.mark.parametrize('orientation', [1, 6, 8])
def test_formats_rotation_metadata_and_invalid_input(format, orientation):
    with io.BytesIO() as raw:
        with Image.new('RGB', (120, 80), 'red') as image:
            exif = Image.Exif(); exif[274] = orientation
            image.save(raw, format, exif=exif)
        data = raw.getvalue()
    with Image.open(io.BytesIO(evidence_jpeg(data, format))) as saved:
        assert saved.format == 'JPEG' and saved.mode == 'RGB'
        assert saved.size == ((80, 120) if orientation in (6, 8) else (120, 80))
        assert saved.getexif().get(274) is None
    with pytest.raises(ValueError):
        evidence_jpeg(data, 'MISMATCH')
    # An exception cannot leave the shared decoder lock held.
    assert evidence_jpeg(data, format)


def test_decoder_waits_for_other_process_without_holding_database_connection(tmp_path):
    sample = tmp_path / 'sample.png'
    with Image.new('RGB', (20, 20), 'white') as image:
        image.save(sample)
    marker = tmp_path / 'entered'
    script = '''from pathlib import Path
import sys
from app.evidence_images import evidence_jpeg
Path(sys.argv[2]).write_text('ready')
evidence_jpeg(Path(sys.argv[1]).read_bytes(), 'PNG')
print('done', flush=True)
'''
    lock_path = Path(tempfile.gettempdir()) / 'kps-order-evidence-images.lock'
    with lock_path.open('ab') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        process = subprocess.Popen([sys.executable, '-c', script, str(sample), str(marker)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            import time
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert marker.exists()
            with pytest.raises(subprocess.TimeoutExpired):
                process.communicate(timeout=.15)
            fcntl.flock(lock, fcntl.LOCK_UN)
            out, err = process.communicate(timeout=5)
            assert process.returncode == 0 and out.strip() == 'done', err
        finally:
            if process.poll() is None:
                process.kill(); process.wait()


def test_animated_webp_still_saves_first_frame():
    with io.BytesIO() as raw:
        with Image.new('RGB', (40, 30), 'red') as first, Image.new('RGB', (40, 30), 'blue') as second:
            first.save(raw, 'WEBP', save_all=True, append_images=[second], duration=100, loop=0)
        with Image.open(io.BytesIO(evidence_jpeg(raw.getvalue(), 'WEBP'))) as saved:
            assert saved.size == (40, 30) and saved.getpixel((20, 15))[0] > 200


@pytest.mark.parametrize('format', ['JPEG', 'PNG', 'WEBP'])
def test_original_pixel_limit_is_still_enforced(format):
    with io.BytesIO() as raw:
        with Image.new('RGB', (4001, 4000), 'white') as image:
            image.save(raw, format)
        with pytest.raises(ValueError):
            evidence_jpeg(raw.getvalue(), format)
