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


def compare_stored_raster_versions(db, store, project_id: int, volume_id: int,
                                   previous_id: int, current_id: int, *,
                                   byte_budget: int, pixel_budget: int) -> dict:
    """Resolve both immutable versions before reading scoped CAS content.

    Caller must authorize viewer access to project before invoking this service.
    This scope check does not itself grant user permission.
    """
    import re
    from sqlalchemy import select, func
    from app.models.rd import RdVolume, RdVolumeVersion
    if any(type(value) is not int or value <= 0 for value in
           (project_id, volume_id, previous_id, current_id, byte_budget, pixel_budget)):
        raise ValueError('Invalid version identifiers or comparison budgets')
    with db.no_autoflush:
        rows = db.execute(select(
            RdVolumeVersion.id, RdVolumeVersion.size_bytes,
            func.substr(RdVolumeVersion.content_sha256, 1, 65).label('digest'),
            func.substr(RdVolumeVersion.mime_type, 1, 101).label('mime'),
        ).join(RdVolume, RdVolume.id == RdVolumeVersion.volume_id).where(
            RdVolume.project_id == project_id, RdVolume.id == volume_id,
            RdVolumeVersion.id.in_({previous_id, current_id})).limit(2)).mappings().all()
    versions = {row['id']: row for row in rows}
    if previous_id not in versions or current_id not in versions:
        raise LookupError('Drawing version not found in project and volume')
    pair = [versions[previous_id], versions[current_id]]
    for row in pair:
        if (type(row['size_bytes']) is not int or row['size_bytes'] <= 0
                or row['mime'] not in ('image/png', 'image/jpeg')
                or not isinstance(row['digest'], str)
                or re.fullmatch('[0-9a-f]{64}', row['digest']) is None):
            raise ValueError('Invalid raster version metadata')
    if sum(row['size_bytes'] for row in pair) > byte_budget:
        raise ValueError('Comparison input byte budget exceeded')
    contents = []
    for row in pair:
        content = store.read(project_id, row['digest'], row['size_bytes'])
        if len(content) != row['size_bytes'] or sha256(content).hexdigest() != row['digest']:
            raise ValueError('Drawing version content mismatch')
        contents.append(content)
    result = compare_raster_versions(*contents, byte_budget=byte_budget, pixel_budget=pixel_budget)
    for key, row in zip(('previous', 'current'), pair):
        actual_mime = {'PNG': 'image/png', 'JPEG': 'image/jpeg'}[result[key]['format']]
        if actual_mime != row['mime']:
            raise ValueError('Drawing version MIME mismatch')
    return dict(result, project_id=project_id, volume_id=volume_id,
                previous_version_id=previous_id, current_version_id=current_id)
