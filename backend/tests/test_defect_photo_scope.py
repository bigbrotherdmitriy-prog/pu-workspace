# Codex fallback photo ownership/validation-isolation regressions.
import pytest
from sqlalchemy import select, func
from test_defects import world
from app import defects as svc
from app.models.defects import Defect, DefectPhoto
from app.models.project import Project


def seed(world,foreign_count=1):
    db=world['db'];pid=world['p'].id;foreign=world['o'].id;org=world['p'].organization_id
    d=Defect(project_id=pid,number=1,title='Synthetic photo scope',status='open')
    db.add(d);db.flush();did=d.id
    photos=[]
    for i in range(foreign_count):
        p=DefectPhoto(project_id=foreign,defect_id=did,kind='before',filename=f'foreign-{i}.jpg',mime_type='image/jpeg',size_bytes=3,data=b'bad')
        db.add(p);db.flush();photos.append(p.id)
    db.commit();db.expunge_all()
    return db,pid,foreign,did,photos,org

@pytest.mark.parametrize('operation',['read','delete'])
def test_foreign_project_photo_cannot_be_read_or_deleted_via_own_defect(world,operation):
    db,pid,foreign,did,photos,org=seed(world)
    pending=Project(name='Unrelated pending',organization_id=org);db.add(pending)
    call=svc.get_photo if operation=='read' else svc.delete_photo
    with pytest.raises(LookupError):call(db,pid,did,photos[0])
    assert pending.id is None and pending in db.new
    with db.no_autoflush:
        assert db.scalar(select(func.count()).select_from(DefectPhoto))==1
        assert db.scalar(select(DefectPhoto.project_id).where(DefectPhoto.id==photos[0]))==foreign


def test_foreign_photo_rows_do_not_consume_own_quota(world):
    db,pid,foreign,did,photos,org=seed(world,foreign_count=svc.MAX_PHOTOS)
    result=svc.add_photo(db,pid,did,'before','own.jpg','image/jpeg',b'own')
    assert len(result['photos'])==1 and result['photos'][0]['filename']=='own.jpg'
    assert db.scalar(select(func.count()).select_from(DefectPhoto).where(DefectPhoto.project_id==foreign))==svc.MAX_PHOTOS

@pytest.mark.parametrize('kind,mime',[('bad-kind','image/jpeg'),('before','text/plain')])
def test_invalid_upload_cold_lookup_preserves_pending_state(world,kind,mime):
    db,pid,foreign,did,photos,org=seed(world,foreign_count=0)
    pending=Project(name='Unrelated pending',organization_id=org);db.add(pending)
    with pytest.raises(ValueError):svc.add_photo(db,pid,did,kind,'test.jpg',mime,b'abc')
    assert pending.id is None and pending in db.new
    with db.no_autoflush:assert db.scalar(select(func.count()).select_from(DefectPhoto))==0
