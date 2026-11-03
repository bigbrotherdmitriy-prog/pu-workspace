# Codex fallback regression for metadata SELECTs; not HTTP permission testing.
import pytest
from sqlalchemy import event
from test_defects import world
from app import defects as svc
from app.models.defects import Defect, DefectPhoto, DefectComment


def seed(world):
    db=world['db'];pid=world['p'].id;other=world['o'].id
    defect=Defect(project_id=pid,number=1,title='Synthetic defect',status='open')
    db.add(defect);db.flush()
    own=DefectPhoto(project_id=pid,defect_id=defect.id,filename='own.bin',mime_type='image/png',size_bytes=524288,data=b'x'*524288)
    foreign=DefectPhoto(project_id=other,defect_id=defect.id,filename='foreign.bin',mime_type='image/png',size_bytes=3,data=b'bad')
    db.add_all([own,foreign,DefectComment(project_id=pid,defect_id=defect.id,text='Own comment'),DefectComment(project_id=other,defect_id=defect.id,text='Foreign comment')]);db.commit()
    return pid,defect.id,own.id


def test_child_metadata_filters_project_and_keeps_response_shape(world):
    pid,did,photoid=seed(world);result=svc.get_defect(world['db'],pid,did)
    assert [p['id'] for p in result['photos']]==[photoid]
    assert result['photos'][0]=={'id':photoid,'kind':'before','filename':'own.bin','mime_type':'image/png','size_bytes':524288}
    assert [c['text'] for c in result['comments']]==['Own comment']


def test_detail_photo_metadata_select_does_not_fetch_binary(world):
    pid,did,_=seed(world);db=world['db'];sql=[]
    def capture(conn,cursor,statement,parameters,context,executemany):sql.append(statement)
    event.listen(db.get_bind(),'before_cursor_execute',capture)
    try:result=svc.get_defect(db,pid,did)
    finally:event.remove(db.get_bind(),'before_cursor_execute',capture)
    photos=[s.lower() for s in sql if s.lstrip().lower().startswith('select') and 'from defect_photos' in s.lower()]
    assert photos and all('defect_photos.data' not in s for s in photos),photos
    assert 'data' not in result['photos'][0]
