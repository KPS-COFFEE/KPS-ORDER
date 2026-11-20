"""Bound native image working memory across this service's Linux workers."""
import fcntl
import io
import os
import tempfile

from PIL import Image, ImageOps


def _still_webp_rgb(raw):
    """Use Pillow's bundled libwebp without its animation frame copies.

    The public libwebp Into API writes bounded RGB output and ignores alpha,
    matching the existing evidence conversion. No separate system codec is
    loaded. Pillow's normal decoder remains responsible for animated files.
    """
    import ctypes
    from PIL import _webp

    codec = ctypes.CDLL(_webp.__file__)
    get_info = codec.WebPGetInfo
    get_info.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                         ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    get_info.restype = ctypes.c_int
    width, height = ctypes.c_int(), ctypes.c_int()
    if not get_info(raw, len(raw), ctypes.byref(width), ctypes.byref(height)):
        raise ValueError('Invalid WebP evidence')
    size = width.value, height.value
    if size[0] * size[1] > 16_000_000:
        raise ValueError('Invalid evidence image')
    info = {}
    offset = 12
    while offset + 8 <= len(raw):
        kind = raw[offset:offset + 4]
        length = int.from_bytes(raw[offset + 4:offset + 8], 'little')
        end = offset + 8 + length
        if end > len(raw):
            raise ValueError('Invalid WebP chunk')
        if kind == b'ANIM':
            return None  # Preserve Pillow's first-frame behavior for animations.
        if kind == b'EXIF':
            info['exif'] = raw[offset + 8:end]
        offset = end + (length % 2)
    decode = codec.WebPDecodeRGBInto
    decode.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                       ctypes.c_size_t, ctypes.c_int]
    decode.restype = ctypes.c_void_p
    pixels = (ctypes.c_ubyte * (size[0] * size[1] * 3))()
    if not decode(raw, len(raw), pixels, len(pixels), size[0] * 3):
        raise ValueError('Invalid WebP evidence')
    image = Image.frombytes('RGB', size, pixels, 'raw', 'RGB', 0, 1)
    image.info.update(info)
    return image


def _jpeg(image):
    image.thumbnail((2000, 2000))
    ImageOps.exif_transpose(image, in_place=True)
    with io.BytesIO() as output:
        image.save(output, 'JPEG', quality=88)
        return output.getvalue()


def evidence_jpeg(raw: bytes, expected_format: str) -> bytes:
    # A compressed phone photo can allocate several full-resolution pixel
    # buffers. One lock shared by all workers prevents concurrent decoders
    # multiplying that peak. Never unlink it: waiters must share one inode.
    path = os.path.join(tempfile.gettempdir(), 'kps-order-evidence-images.lock')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if expected_format == 'WEBP':
            image = _still_webp_rgb(raw)
            if image is not None:
                with image:
                    return _jpeg(image)
        with Image.open(io.BytesIO(raw)) as source:
            if source.format != expected_format or source.width * source.height > 16_000_000:
                raise ValueError('Invalid evidence image')
            # RGB JPEG thumbnail can use draft decoding. Convert alpha/palette
            # images first: Pillow's alpha resize otherwise creates another
            # full-size premultiplied buffer. Release the original immediately.
            image = source
            if source.mode != 'RGB':
                with source.convert('RGB') as image:
                    source.close()
                    return _jpeg(image)
            else:
                return _jpeg(source)
