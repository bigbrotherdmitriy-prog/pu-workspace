"""Explicit content retention primitive; no provider/API or cross-resource transaction.

Bytes are stored before metadata insertion. Caller owns the DB transaction.
Rollback may leave a quota-counted immutable blob; no automatic destructive cleanup.
This primitive does not validate a file codec or authorize a user.
"""
from hashlib import sha256
from sqlalchemy import literal, select
from app.models.rd import RdVolume, RdVolumeVersion


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
