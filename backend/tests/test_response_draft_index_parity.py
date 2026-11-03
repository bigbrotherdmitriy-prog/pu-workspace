# Codex fallback: match actual legacy migration index contract, no sends/providers.
import hashlib
import pytest
from sqlalchemy import create_engine,event,inspect,select,func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import app.models
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.user import User
from app.models.response_draft import ResponseDraft

@pytest.fixture
def world():
    engine=create_engine('sqlite://')
    event.listen(engine,'connect',lambda connection,_:connection.execute('PRAGMA foreign_keys=ON'))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org=Organization(name='Synthetic index check');user=User(name='Reviewer',email='reviewer@example.test',is_admin=False)
        db.add_all([org,user]);db.flush();project=Project(name='Synthetic',organization_id=org.id);db.add(project);db.flush()
        yield db,project.id,user.id
    engine.dispose()


def draft(world,sequence,**fields):
    db,pid,uid=world
    return ResponseDraft(project_id=pid,reviewer_user_id=uid,subject='Synthetic',body='Synthetic only',status='draft',source_file_id=f'file-{sequence}',source_file_name='synthetic.txt',source_excerpt='Synthetic',source_excerpt_hash=hashlib.sha256(str(sequence).encode()).hexdigest(),confidence=1.0,**fields)


def test_fresh_metadata_matches_two_existing_migration_unique_indexes(world):
    inspector=inspect(world[0].get_bind());actual={i['name']:(tuple(i['column_names']),bool(i['unique'])) for i in inspector.get_indexes('response_drafts')}
    # Independent oracle: a12e94c71b20 and b01c9a7d4e21 create these indexes.
    assert actual.get('ix_response_drafts_sent_external_id')==(('sent_external_id',),True)
    assert actual.get('uq_response_drafts_send_idempotency_key')==(('send_idempotency_key',),True)
    constraints=inspector.get_unique_constraints('response_drafts')
    assert not any(tuple(c['column_names']) in [('sent_external_id',),('send_idempotency_key',)] for c in constraints)
    assert any(c['name']=='uq_response_source_excerpt' for c in constraints)

@pytest.mark.parametrize('column',['sent_external_id','send_idempotency_key'])
def test_duplicate_external_identity_still_rejected(world,column):
    db,pid,uid=world;first=draft(world,1,**{column:'synthetic-unique'});db.add(first);db.commit()
    with pytest.raises(IntegrityError):
        with db.begin_nested():db.add(draft(world,2,**{column:'synthetic-unique'}));db.flush()
    assert db.scalar(select(func.count()).select_from(ResponseDraft))==1


def test_multiple_unsent_null_identities_still_allowed(world):
    db,pid,uid=world;db.add_all([draft(world,1),draft(world,2)]);db.flush()
    assert db.scalar(select(func.count()).select_from(ResponseDraft))==2
