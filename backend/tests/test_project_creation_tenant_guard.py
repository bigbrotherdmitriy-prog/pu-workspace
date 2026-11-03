# Codex fallback service tests; not HTTP/auth acceptance.
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
import app.models
from app.database import Base
from app.models.organization_contract import Organization
from app.models.workspace_tenant import TenantWorkspace, TenantWorkspaceMember
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.user import User
from app.project_creation import resolve_creation_organization

@pytest.fixture
def world():
    engine=create_engine('sqlite://')
    event.listen(engine,'connect',lambda connection,_:connection.execute('PRAGMA foreign_keys=ON'))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        actor=User(name='Actor',email='actor@example.test',is_admin=False)
        other=User(name='Other',email='other@example.test',is_admin=False)
        own=TenantWorkspace(name='Own',slug='own');foreign=TenantWorkspace(name='Foreign',slug='foreign')
        db.add_all([actor,other,own,foreign]);db.flush()
        foreign_org=Organization(name='Foreign first',tenant_workspace_id=foreign.id)
        db.add(foreign_org);db.flush()
        own_org=Organization(name='Own',tenant_workspace_id=own.id)
        legacy=Organization(name='Legacy',tenant_workspace_id=None)
        db.add_all([own_org,legacy]);db.flush()
        db.add_all([TenantWorkspaceMember(user_id=actor.id,workspace_id=own.id,role='admin'),TenantWorkspaceMember(user_id=other.id,workspace_id=foreign.id,role='admin')]);db.commit()
        yield db,actor,own_org,foreign_org,legacy
    engine.dispose()

@pytest.mark.parametrize('selected',['foreign','default'])
def test_foreign_selection_denied_without_any_write_or_flush(world,selected):
    db,actor,own,foreign,legacy=world
    uid=actor.id;oid=foreign.id if selected=='foreign' else None
    own_id=own.id;own.name='Pending edit'
    pending=Project(name='Caller pending',organization_id=own_id);db.add(pending)
    statements=[]
    def capture(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
    event.listen(db.get_bind(),'before_cursor_execute',capture)
    try:
        with pytest.raises(PermissionError):resolve_creation_organization(db,uid,False,oid)
    finally:event.remove(db.get_bind(),'before_cursor_execute',capture)
    assert pending in db.new and pending.id is None
    assert own in db.dirty and own.name=='Pending edit'
    assert not any(s.lstrip().lower().startswith(('insert','update','delete')) for s in statements)
    db.rollback()
    assert db.scalars(select(Project.id)).all()==[]
    assert db.scalars(select(ProjectMember.id)).all()==[]


def test_own_selection_preserves_tenant_binding(world):
    db,actor,own,foreign,legacy=world
    resolved=resolve_creation_organization(db,actor.id,False,own.id)
    assert (resolved.id,resolved.tenant_workspace_id)==(own.id,own.tenant_workspace_id)
    assert not db.new and not db.dirty

@pytest.mark.parametrize('selected',['foreign','default'])
def test_existing_global_admin_selection_preserved(world,selected):
    db,actor,own,foreign,legacy=world
    result=resolve_creation_organization(db,actor.id,True,foreign.id if selected=='foreign' else None)
    assert result.id==foreign.id


def test_existing_explicit_legacy_selection_preserved(world):
    db,actor,own,foreign,legacy=world
    result=resolve_creation_organization(db,actor.id,False,legacy.id)
    assert result.id==legacy.id and result.tenant_workspace_id is None


def test_missing_organization_rejected(world):
    db,actor,own,foreign,legacy=world
    with pytest.raises(LookupError):resolve_creation_organization(db,actor.id,False,999999)


def test_unflushed_membership_does_not_grant_access(world):
    db,actor,own,foreign,legacy=world
    uid=actor.id;oid=foreign.id
    pending=TenantWorkspaceMember(user_id=uid,workspace_id=foreign.tenant_workspace_id,role='admin');db.add(pending)
    with pytest.raises(PermissionError):resolve_creation_organization(db,uid,False,oid)
    assert pending.id is None and pending in db.new


def test_deleted_membership_denies_next_resolution(world):
    db,actor,own,foreign,legacy=world
    uid=actor.id;oid=own.id
    membership=db.scalar(select(TenantWorkspaceMember).where(TenantWorkspaceMember.user_id==uid))
    db.delete(membership);db.commit()
    with pytest.raises(PermissionError):resolve_creation_organization(db,uid,False,oid)
