import pytest
from sqlalchemy import select,func
from app.defect_photo_receipts import save_photo,PhotoReceiptConflict
from app.models.defects import DefectPhoto,DefectPhotoReceipt
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.user import User
from app.defects import create_defect,delete_photo,delete_defect

@pytest.fixture
def photo_case(db_session):
 db=db_session;org=Organization(name='SYNTHETIC');db.add(org);db.flush();p=Project(name='SYNTHETIC',organization_id=org.id);u=User(name='SYNTHETIC',email='synthetic@example.test');db.add_all([p,u]);db.flush();d=create_defect(db,p.id,u.id,{'title':'SYNTHETIC'});db.commit();return db,p.id,u.id,d['id']

def upload(case,**kw):
 db,p,u,d=case;args=dict(operation_key='synthetic-photo-one',kind='before',filename='synthetic.png',mime='image/png',data=b'SYNTHETIC bytes');args.update(kw);return save_photo(db,p,d,u,**args)

def test_replay_after_commit_and_deleted_tombstone(photo_case):
 db,p,u,d=photo_case;first=upload(photo_case);db.commit();assert upload(photo_case)==first
 assert db.scalar(select(func.count()).select_from(DefectPhoto))==1
 delete_photo(db,p,d,first['photo_id']);db.commit()
 assert db.scalar(select(DefectPhotoReceipt.photo_id)) is None
 with pytest.raises(PhotoReceiptConflict,match='deleted'):upload(photo_case)
 assert db.scalar(select(func.count()).select_from(DefectPhoto))==0

@pytest.mark.parametrize('change',[{'data':b'changed'},{'filename':'other.png'},{'kind':'after'},{'mime':'image/jpeg'}])
def test_key_never_reused_with_changed_payload(photo_case,change):
 upload(photo_case);photo_case[0].commit()
 with pytest.raises(PhotoReceiptConflict):upload(photo_case,**change)
 assert photo_case[0].scalar(select(func.count()).select_from(DefectPhoto))==1

def test_replay_at_quota_and_new_command_rejected(photo_case):
 db,p,u,d=photo_case;first=upload(photo_case)
 for i in range(11):upload(photo_case,operation_key=f'synthetic-photo-{i:02d}')
 db.commit();assert upload(photo_case)==first
 with pytest.raises(ValueError,match='quota'):upload(photo_case,operation_key='synthetic-over-limit')
 assert db.scalar(select(func.count()).select_from(DefectPhoto))==12

def test_scope_parent_delete_and_no_unrelated_flush(photo_case):
 db,p,u,d=photo_case;unrelated=User(name='SYNTHETIC pending',email='pending@example.test');db.add(unrelated)
 first=upload(photo_case);assert unrelated.id is None
 with pytest.raises(LookupError):save_photo(db,p+999,d,u,operation_key='synthetic-foreign',kind='before',filename='x.png',mime='image/png',data=b'x')
 db.expunge(unrelated);db.commit();delete_defect(db,p,d);db.commit()
 assert db.scalar(select(func.count()).select_from(DefectPhotoReceipt))==0
 with pytest.raises(LookupError):upload(photo_case)


def test_other_parent_reusing_key_does_not_create_orphan(photo_case):
 db,p,u,d=photo_case;first=upload(photo_case);db.commit();other=create_defect(db,p,u,{'title':'SYNTHETIC other'});db.commit()
 with pytest.raises(PhotoReceiptConflict):upload((db,p,u,other['id']))
 assert db.scalar(select(func.count()).select_from(DefectPhoto))==1
 assert db.scalar(select(func.count()).select_from(DefectPhotoReceipt))==1

@pytest.mark.parametrize('change',[{'operation_key':'x'},{'data':b''},{'mime':'text/html'},{'data':b'x'*(10*1024*1024+1)},{'filename':''},{'kind':'bad'}])
def test_invalid_commands_make_no_rows(photo_case,change):
 with pytest.raises(ValueError):upload(photo_case,**change)
 assert photo_case[0].scalar(select(func.count()).select_from(DefectPhoto))==0
