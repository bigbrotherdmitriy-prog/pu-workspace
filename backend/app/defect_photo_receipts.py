"""Bounded raw photo upload with durable actor-bound replay/tombstone semantics."""
import hashlib
import json
import re
from sqlalchemy import select, text
from app.models.defects import Defect, DefectPhoto, DefectPhotoReceipt
from app.defects import MAX_PHOTOS, MAX_PHOTO_BYTES, ALLOWED_MIME

class PhotoReceiptConflict(ValueError):
    pass


def save_photo(db, project_id, defect_id, user_id, *, operation_key, kind, filename, mime, data):
    if any(type(v) is not int or v <= 0 for v in [project_id, defect_id, user_id]):
        raise ValueError('Invalid photo ownership')
    if not isinstance(operation_key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{8,160}', operation_key):
        raise ValueError('Invalid photo operation key')
    if kind not in ['before', 'after'] or not isinstance(filename, str) or not 1 <= len(filename) <= 300:
        raise ValueError('Invalid photo metadata')
    mime = (mime or '').split(';')[0].strip().lower()
    if mime not in ALLOWED_MIME or not isinstance(data, bytes) or not 0 < len(data) <= MAX_PHOTO_BYTES:
        raise ValueError('Invalid photo content/type/size')
    digest = hashlib.sha256(data).hexdigest()
    fingerprint = hashlib.sha256(json.dumps([defect_id, kind, filename, mime, len(data), digest], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    with db.no_autoflush:
        dialect = db.get_bind().dialect.name
        if dialect == 'sqlite':
            db.execute(text('UPDATE defects SET id=id WHERE id=:d AND project_id=:p'), {'d':defect_id,'p':project_id})
        elif dialect != 'postgresql':
            raise RuntimeError('Photo receipt requires SQLite or PostgreSQL')
        parent = db.scalar(select(Defect.id).where(Defect.id==defect_id,Defect.project_id==project_id).with_for_update())
        if parent is None: raise LookupError('Defect not found in project')
        query = select(DefectPhotoReceipt.__table__).where(DefectPhotoReceipt.project_id==project_id,DefectPhotoReceipt.user_id==user_id,DefectPhotoReceipt.operation_key==operation_key)
        row = db.execute(query).mappings().one_or_none()
        if row is not None and (row['defect_id'] != defect_id or row['request_sha256'] != fingerprint):
            raise PhotoReceiptConflict('Photo operation key already used with different data')
        if row is not None:
            photo = db.execute(select(DefectPhoto.id).where(DefectPhoto.id==row['photo_id'],DefectPhoto.project_id==project_id,DefectPhoto.defect_id==defect_id)).first()
            if photo is None: raise PhotoReceiptConflict('Photo was deleted; the original upload must not recreate it')
        else:
            from sqlalchemy import func
            count = db.scalar(select(func.count()).select_from(DefectPhoto).where(DefectPhoto.project_id==project_id,DefectPhoto.defect_id==defect_id)) or 0
            if count >= MAX_PHOTOS: raise ValueError('Photo quota reached')
            name = re.sub(r'[\\/\x00-\x1f]', '_', filename.strip())[:300] or 'photo'
            # A nested connection savepoint avoids Session.begin_nested flushing unrelated pending ORM state.
            connection = db.connection()
            with connection.begin_nested():
                photo_id = connection.execute(DefectPhoto.__table__.insert().values(project_id=project_id,defect_id=defect_id,kind=kind,filename=name,mime_type=mime,size_bytes=len(data),data=data).returning(DefectPhoto.id)).scalar_one()
                if dialect == 'sqlite':
                    from sqlalchemy.dialects.sqlite import insert
                else:
                    from sqlalchemy.dialects.postgresql import insert
                receipt_id = connection.execute(insert(DefectPhotoReceipt).values(project_id=project_id,user_id=user_id,defect_id=defect_id,operation_key=operation_key,request_sha256=fingerprint,photo_id=photo_id).on_conflict_do_nothing(index_elements=['project_id','user_id','operation_key']).returning(DefectPhotoReceipt.id)).scalar_one_or_none()
                if receipt_id is None:
                    raise PhotoReceiptConflict('Photo operation key already used for another defect')
            row = db.execute(query).mappings().one()
        return {'status':'applied','receipt_id':row['id'],'photo_id':row['photo_id'],
                'project_id':project_id,'defect_id':defect_id,'user_id':user_id,
                'operation_key':operation_key,'sha256':digest,'size_bytes':len(data),
                'kind':kind,'filename':filename,'mime_type':mime}
