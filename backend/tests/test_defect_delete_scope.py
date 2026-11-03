# Codex fallback deletion safety with actual FK-enabled synthetic SQLite.
import pytest
from sqlalchemy import event, select, func, inspect
from test_defects import bind_world
from app import defects as svc
from app.models.defects import Defect, DefectPhoto, DefectComment
from app.models.project import Project
from app.models.rd import RdVolumeVersion


def seed(world,corrupt=None):
    db=world['db'];pid=world['p'].id;foreign=world['o'].id;org=world['p'].organization_id;version=world['ver_p'].id
    d=Defect(project_id=pid,number=1,title='Delete safety',status='open',rd_version_id=version)
    db.add(d);db.flush();did=d.id
    ownphoto=DefectPhoto(project_id=pid,defect_id=did,filename='own.bin',mime_type='image/png',size_bytes=524288,data=b'x'*524288)
    owncomment=DefectComment(project_id=pid,defect_id=did,text='Own')
    db.add_all([ownphoto,owncomment]);db.flush();photoid=ownphoto.id
    if corrupt=='photo':db.add(DefectPhoto(project_id=foreign,defect_id=did,filename='foreign',mime_type='image/png',size_bytes=3,data=b'bad'))
    if corrupt=='comment':db.add(DefectComment(project_id=foreign,defect_id=did,text='Foreign'))
    db.commit()
    return db,pid,foreign,did,org,version,photoid,ownphoto


def capture(db):
    sql=[]
    def cb(conn,cursor,statement,params,context,many):sql.append(statement)
    event.listen(db.get_bind(),'before_cursor_execute',cb)
    return sql,lambda:event.remove(db.get_bind(),'before_cursor_execute',cb)

@pytest.mark.parametrize('child',['photo','comment'])
def test_inconsistent_foreign_child_blocks_whole_delete_without_side_effects(bind_world,child):
    db,pid,foreign,did,org,version,photoid,photo=seed(bind_world,child)
    db.expunge_all();pending=Project(name='Pending',organization_id=org);db.add(pending)
    sql,detach=capture(db)
    try:
        with pytest.raises(LookupError):svc.delete_defect(db,pid,did)
    finally:detach()
    assert pending.id is None and pending in db.new
    assert not any(s.lstrip().lower().startswith(('insert','update','delete')) for s in sql)
    with db.no_autoflush:
        assert db.get(Defect,did) is not None
        assert db.scalar(select(func.count()).select_from(DefectPhoto))==(2 if child=='photo' else 1)
        assert db.scalar(select(func.count()).select_from(DefectComment))==(2 if child=='comment' else 1)


def test_valid_delete_avoids_blob_fetch_preserves_version_and_can_rollback(bind_world):
    db,pid,foreign,did,org,version,photoid,photo=seed(bind_world)
    sql,detach=capture(db)
    try:svc.delete_defect(db,pid,did)
    finally:detach()
    assert not any('defect_photos.data' in s.lower() for s in sql),sql
    assert inspect(photo).deleted
    assert db.get(Defect,did) is None
    assert db.get(RdVolumeVersion,version) is not None
    assert db.scalar(select(func.count()).select_from(DefectPhoto))==0
    assert db.scalar(select(func.count()).select_from(DefectComment))==0
    db.rollback()
    assert db.get(Defect,did) is not None and db.get(DefectPhoto,photoid).data==b'x'*524288


def test_foreign_parent_rejection_does_not_autoflush(bind_world):
    db,pid,foreign,did,org,version,photoid,photo=seed(bind_world)
    db.expunge_all();pending=Project(name='Pending',organization_id=org);db.add(pending)
    with pytest.raises(LookupError):svc.delete_defect(db,foreign,did)
    assert pending.id is None and pending in db.new
