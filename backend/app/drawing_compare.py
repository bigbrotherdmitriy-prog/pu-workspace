"""Bounded raw decoded-pixel comparison for two PNG/JPEG versions.

No alignment/resizing, PDF rendering, OCR, file storage, API authorization or UI.
RGBA compares all channels, including RGB values hidden by transparent alpha.
"""
from hashlib import sha256
from io import BytesIO
from PIL import Image, UnidentifiedImageError


def compare_raster_versions(previous: bytes, current: bytes, *, byte_budget: int, pixel_budget: int) -> dict:
    for value in (byte_budget, pixel_budget):
        if type(value) is not int or value <= 0:
            raise ValueError('Comparison budgets must be positive integers')
    if not isinstance(previous, bytes) or not isinstance(current, bytes):
        raise ValueError('Comparison inputs must be bytes')
    if not previous or not current or len(previous) + len(current) > byte_budget:
        raise ValueError('Invalid or excessive comparison input bytes')
    images = []
    rgba = []
    try:
        for content in (previous, current):
            image = Image.open(BytesIO(content), formats=('PNG', 'JPEG'))
            images.append(image)
            if getattr(image, 'n_frames', 1) != 1:
                raise ValueError('Animated/multiframe images are not supported')
        if sum(image.width * image.height for image in images) > pixel_budget:
            raise ValueError('Comparison pixel budget exceeded')
        # Both headers are budgeted before either full decode.
        for image in images:
            image.load()
            rgba.append(image.convert('RGBA'))
        old, new = rgba
        result = {
            'comparison': 'raw_rgba_pixels_no_alignment',
            'previous': {'format': images[0].format, 'size': list(old.size), 'sha256': sha256(previous).hexdigest()},
            'current': {'format': images[1].format, 'size': list(new.size), 'sha256': sha256(current).hexdigest()},
            'dimensions_changed': old.size != new.size,
            'same_pixels': False, 'changed_pixels': None, 'changed_bbox': None,
        }
        if old.size != new.size:
            return result
        a, b = old.tobytes(), new.tobytes()
        changed = 0
        left, top, right, bottom = old.width, old.height, 0, 0
        for offset in range(0, len(a), 4):
            if a[offset:offset + 4] == b[offset:offset + 4]:
                continue
            changed += 1
            y, x = divmod(offset // 4, old.width)
            left, top = min(left, x), min(top, y)
            right, bottom = max(right, x + 1), max(bottom, y + 1)
        result.update(same_pixels=changed == 0, changed_pixels=changed,
                      changed_bbox=[left, top, right, bottom] if changed else None)
        return result
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError('Invalid or unsupported raster image') from error
    finally:
        for image in rgba + images:
            image.close()
