"""Explicit content retention primitive; no provider/API or cross-resource transaction.

Bytes are stored before metadata insertion. Caller owns the DB transaction.
Rollback may leave a quota-counted immutable blob; no automatic destructive cleanup.
This primitive does not validate a file codec or authorize a user.
"""
from hashlib import sha256
from sqlalchemy import func, literal, select
from app.models.rd import RdVolume, RdVolumeVersion

_ALLOWED_MIME = ('image/png', 'image/jpeg', 'application/pdf')


def retain_version_content(db, store, project_id: int, volume_id: int, content: bytes,
                           file_name: str, mime_type: str, *, byte_budget: int) -> dict:
    if any(type(value) is not int or value <= 0 for value in (project_id, volume_id, byte_budget)):
        raise ValueError('Invalid project, volume or content budget')
    if type(content) is not bytes or not content or len(content) > byte_budget:
        raise ValueError('Invalid or excessive version content')
    for value, limit in ((file_name, 500), (mime_type, 100)):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
            raise ValueError('Invalid version metadata')
    dialect = db.get_bind().dialect.name
    if dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    elif dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        raise RuntimeError('Version retention requires SQLite or PostgreSQL')
    with db.no_autoflush:
        volume = db.scalar(select(RdVolume.id).where(
            RdVolume.id == volume_id, RdVolume.project_id == project_id).with_for_update())
        if volume is None:
            raise LookupError('Drawing volume not found in project')
        digest = sha256(content).hexdigest()
        if store.put(project_id, content) != digest:
            raise ValueError('Content store digest mismatch')
        # Scope is also checked atomically by INSERT SELECT; SQLite ignores FOR UPDATE.
        source = select(RdVolume.id, literal(digest), literal(len(content)),
                        literal(file_name.strip()), literal(mime_type.strip())).where(
                            RdVolume.id == volume_id, RdVolume.project_id == project_id)
        db.execute(insert(RdVolumeVersion).from_select(
            ['volume_id', 'content_sha256', 'size_bytes', 'file_name', 'mime_type'], source,
        ).on_conflict_do_nothing(index_elements=['volume_id', 'content_sha256']))
        row = db.execute(select(RdVolumeVersion.__table__).join(
            RdVolume, RdVolume.id == RdVolumeVersion.volume_id).where(
            RdVolume.id == volume_id, RdVolume.project_id == project_id,
            RdVolumeVersion.content_sha256 == digest)).mappings().one_or_none()
        if row is None:
            raise LookupError('Drawing volume not found in project')
        if row['size_bytes'] != len(content):
            raise ValueError('Existing version size mismatch')
        return {'id': row['id'], 'volume_id': row['volume_id'],
                'content_sha256': row['content_sha256'], 'size_bytes': row['size_bytes'],
                'file_name': row['file_name'], 'mime_type': row['mime_type'],
                'created_at': row['created_at'].isoformat()}


def read_retained_version_content(db, store, project_id: int, version_id: int, *,
                                  byte_budget: int) -> dict:
    """Read retained version bytes for a future authenticated download.

    Read-only: performs no commit, flush, rollback or write. The caller MUST
    authorize the requesting user as a project viewer before invocation; this
    helper grants no user access itself. The later API layer MUST also enforce
    no-store, nosniff and safe attachment response headers.

    MIME type is retained metadata only; bytes integrity is verified, but safe
    inline rendering is NOT established and no codec/render/page validation is
    claimed. Missing or foreign-scoped versions are indistinguishable and raise
    LookupError before any store access. Corrupt, truncated or oversized stored
    data raises ValueError; specific store errors propagate unchanged.
    """
    if any(type(value) is not int or value <= 0
           for value in (project_id, version_id, byte_budget)):
        raise ValueError('Invalid project, version or content budget')
    with db.no_autoflush:
        row = db.execute(select(
            RdVolumeVersion.id,
            RdVolumeVersion.volume_id,
            RdVolumeVersion.size_bytes,
            func.substr(RdVolumeVersion.content_sha256, 1, 65),
            func.substr(RdVolumeVersion.mime_type, 1, 101),
        ).join(RdVolume, RdVolume.id == RdVolumeVersion.volume_id).where(
            RdVolumeVersion.id == version_id,
            RdVolume.project_id == project_id,
        )).one_or_none()
    if row is None:
        raise LookupError('Drawing version not found in project')
    volume_id, declared_size, digest, mime_type = row[1], row[2], row[3], row[4]
    if type(declared_size) is not int or declared_size <= 0 or declared_size > byte_budget:
        raise ValueError('Invalid retained version size')
    if (not isinstance(digest, str) or len(digest) != 64
            or any(ch not in '0123456789abcdef' for ch in digest)):
        raise ValueError('Invalid retained version digest')
    if mime_type not in _ALLOWED_MIME:
        raise ValueError('Invalid retained version MIME type')
    content = store.read(project_id, digest, declared_size)
    if type(content) is not bytes:
        raise ValueError('Retained version content is not bytes')
    if len(content) != declared_size:
        raise ValueError('Retained version content size mismatch')
    if sha256(content).hexdigest() != digest:
        raise ValueError('Retained version content digest mismatch')
    return {'project_id': project_id, 'version_id': version_id,
            'volume_id': volume_id, 'content_sha256': digest,
            'size_bytes': declared_size, 'mime_type': mime_type,
            'content': content}
