# Codex fallback rejected-edit isolation, not HTTP/production acceptance.
from datetime import date
import pytest
from sqlalchemy import event
from test_defects import world
from app import defects as svc
from app.models.defects import Defect
from app.models.project import Project


def seed(world):
    db=world['db'];pid=world['p'].id
    d=Defect(project_id=pid,number=1,title='Stored title',description='Stored description',priority='normal',status='open',due_date=date(2026,11,2))
    db.add(d);db.commit()
    did=d.id;org=world['p'].organization_id
    d.title='Caller pending title'
    pending=Project(name='Unrelated pending project',organization_id=org);db.add(pending)
    return db,pid,did,d,pending


def capture(db):
    statements=[]
    def callback(conn,cursor,sql,params,context,many):statements.append(sql)
    event.listen(db.get_bind(),'before_cursor_execute',callback)
    return statements,lambda:event.remove(db.get_bind(),'before_cursor_execute',callback)

@pytest.mark.parametrize('change',[
    {'title':'Requested new title','description':'New description','priority':'invalid'},
    {'title':'Requested new title','description':'New description','priority':'high','due_date':'not-a-date'},
    {'title':'   '},
])
def test_rejected_update_preserves_target_and_caller_pending_state(world,change):
    db,pid,did,d,pending=seed(world);statements,detach=capture(db)
    try:
        with pytest.raises(ValueError):svc.update_defect(db,pid,did,change)
    finally:detach()
    assert d.title=='Caller pending title' and d.description=='Stored description'
    assert d.priority=='normal' and d.due_date==date(2026,11,2)
    assert d in db.dirty and pending in db.new and pending.id is None
    assert not any(s.lstrip().lower().startswith(('insert','update','delete')) for s in statements),statements
    db.rollback()
    assert db.get(Defect,did).title=='Stored title'


def test_foreign_lookup_does_not_flush_before_rejection(world):
    foreign=world['o'].id
    db,pid,did,d,pending=seed(world)
    db.expunge(d)  # Force the service lookup to issue SQL rather than use the identity map.
    statements,detach=capture(db)
    try:
        with pytest.raises(LookupError):svc.update_defect(db,foreign,did,{'title':'Wrong project'})
    finally:detach()
    assert pending.id is None and pending in db.new and d.title=='Caller pending title'
    assert not any(s.lstrip().lower().startswith(('insert','update','delete')) for s in statements)


def test_successful_update_is_complete_and_caller_can_rollback(world):
    db,pid,did,d,pending=seed(world)
    result=svc.update_defect(db,pid,did,{'title':'New','description':'Details','location':'Room','responsible':'Team','priority':'high','due_date':'2026-11-05','rd_version_id':999999})
    assert result['title']=='New' and result['description']=='Details' and result['priority']=='high' and result['due_date']=='2026-11-05'
    assert result['rd_version_id'] is None
    db.rollback()
    assert db.get(Defect,did).title=='Stored title' and pending not in db.new
