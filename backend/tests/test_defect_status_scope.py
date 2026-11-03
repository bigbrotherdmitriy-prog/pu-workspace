# Codex fallback scope/isolation regressions, not HTTP/camera acceptance.
import pytest
from sqlalchemy import event, select, func
from test_defects import world
from app import defects as svc
from app.models.defects import Defect, DefectPhoto, DefectComment
from app.models.project import Project


def seed(world,*,own_after=False,foreign_after=False):
    db=world['db'];pid=world['p'].id;foreign=world['o'].id
    d=Defect(project_id=pid,number=1,title='Status scope',status='open')
    db.add(d);db.flush()
    for project,include in [(pid,own_after),(foreign,foreign_after)]:
        if include:db.add(DefectPhoto(project_id=project,defect_id=d.id,kind='after',filename='synthetic.jpg',mime_type='image/jpeg',size_bytes=3,data=b'abc'))
    db.commit();did=d.id;org=world['p'].organization_id
    return db,pid,did,d,org


def test_foreign_after_photo_is_not_evidence_for_fix(world):
    db,pid,did,d,org=seed(world,foreign_after=True)
    with pytest.raises(ValueError):svc.change_status(db,pid,None,did,'fixed',None)
    assert d.status=='open' and db.scalar(select(func.count()).select_from(DefectComment))==0


def test_missing_evidence_rejection_preserves_pending_state(world):
    db,pid,did,d,org=seed(world)
    d.title='Caller edit';pending=Project(name='Pending',organization_id=org);db.add(pending)
    sql=[]
    def capture(conn,cursor,statement,params,context,many):sql.append(statement)
    event.listen(db.get_bind(),'before_cursor_execute',capture)
    try:
        with pytest.raises(ValueError):svc.change_status(db,pid,None,did,'fixed',' ')
    finally:event.remove(db.get_bind(),'before_cursor_execute',capture)
    assert pending.id is None and pending in db.new and d in db.dirty
    assert d.status=='open' and d.title=='Caller edit'
    assert not any(s.lstrip().lower().startswith(('insert','update','delete')) for s in sql)


def test_invalid_status_cold_lookup_does_not_flush(world):
    db,pid,did,d,org=seed(world)
    db.expunge(d);pending=Project(name='Pending',organization_id=org);db.add(pending)
    with pytest.raises(ValueError):svc.change_status(db,pid,None,did,'not-a-status',None)
    assert pending.id is None and pending in db.new

@pytest.mark.parametrize('own_after,comment',[(True,None),(False,'Synthetic repair explanation')])
def test_existing_own_photo_or_comment_rule_and_rollback(world,own_after,comment):
    db,pid,did,d,org=seed(world,own_after=own_after)
    result=svc.change_status(db,pid,None,did,'fixed',comment)
    assert result['status']=='fixed'
    assert [row['status_change'] for row in result['comments']]==['fixed']
    db.rollback()
    assert db.get(Defect,did).status=='open'
    assert db.scalar(select(func.count()).select_from(DefectComment))==0
