"""Real synthetic SQLite service transactions; no HTTP/auth-session acceptance."""
from copy import deepcopy
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine,event,select,func
from sqlalchemy.orm import Session
import app.models
from app.database import Base
from app.models.user import User
from app.models.project import Project
from app.models.project_member import ProjectMember
from app.models.organization_contract import Organization
from app.models.work_log import WorkLogEntry,WorkLogLabor
from app.models.mobile_sync import MobileSyncCommand
from app.mobile_work_log_contract import OfflineWorkLogCreateRequest
from app.mobile_work_log import submit_work_log_create

@pytest.fixture
def world():
    engine=create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine,'connect')
    def fk(c,_):c.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org=Organization(name='Synthetic offline');actor=User(name='Editor',email='offline@example.test',is_admin=False)
        db.add_all([org,actor]);db.flush()
        project=Project(name='Own synthetic',organization_id=org.id);other=Project(name='Other synthetic',organization_id=org.id)
        db.add_all([project,other]);db.flush()
        db.add(ProjectMember(project_id=project.id,user_id=actor.id,role='editor'));db.commit()
        yield db,actor,project,other
    engine.dispose()

def command(project,**data):
    return OfflineWorkLogCreateRequest.model_validate({'client_mutation_id':'synthetic-offline-command',
        'device_id':'synthetic-device','project_id':project.id,'operation':'work_log.create',
        'client_created_at':'2026-10-30T09:00:00+03:00',
        'data':{'work_date':'2026-10-30','work_name':'Synthetic job','qty':'1.250',
                'labor':[{'person_name':'Synthetic worker','hours':'2'}],**data}})

def count(db,model):return db.scalar(select(func.count()).select_from(model))

def test_create_then_lost_response_retry_returns_same_receipt(world):
    db,actor,project,_=world;request=command(project)
    first=submit_work_log_create(db,actor,request);db.commit()
    assert submit_work_log_create(db,actor,request)==first
    assert count(db,WorkLogEntry)==count(db,WorkLogLabor)==count(db,MobileSyncCommand)==1
    entry=db.get(WorkLogEntry,first['entity_id'])
    assert entry.author_user_id==actor.id and str(entry.work_date)=='2026-10-30'
    assert first['entity_id']>0 and first['status']=='applied'


def test_outer_rollback_does_not_commit_savepoint_fact(world):
    db,actor,project,_=world;request=command(project)
    submit_work_log_create(db,actor,request);db.rollback()
    assert count(db,WorkLogEntry)==count(db,WorkLogLabor)==count(db,MobileSyncCommand)==0
    submit_work_log_create(db,actor,request);db.commit()
    assert count(db,WorkLogEntry)==1


@pytest.mark.parametrize('data',[{'qty':'-1'},{'work_name':''},{'crew_id':99999}])
def test_business_rejection_leaves_no_fact_or_receipt(world,data):
    db,actor,project,_=world
    with pytest.raises((ValueError,LookupError)):
        submit_work_log_create(db,actor,command(project,**data))
    assert count(db,WorkLogEntry)==count(db,WorkLogLabor)==count(db,MobileSyncCommand)==0


def test_changed_payload_same_id_rejected(world):
    db,actor,project,_=world;first=submit_work_log_create(db,actor,command(project));db.commit()
    with pytest.raises(ValueError,match='client_mutation_id_reused'):
        submit_work_log_create(db,actor,command(project,qty='2'))
    assert count(db,WorkLogEntry)==1 and db.get(WorkLogEntry,first['entity_id']).qty==1.250


@pytest.mark.parametrize('role',['viewer',None])
def test_role_revoked_before_replay_does_not_disclose_receipt(world,role):
    db,actor,project,_=world;request=command(project)
    submit_work_log_create(db,actor,request);db.commit()
    membership=db.scalar(select(ProjectMember).where(ProjectMember.project_id==project.id))
    if role:membership.role=role
    else:db.delete(membership)
    db.commit()
    with pytest.raises(HTTPException) as exc:submit_work_log_create(db,actor,request)
    assert exc.value.status_code==403 and count(db,WorkLogEntry)==1


def test_foreign_project_cannot_create(world):
    db,actor,project,other=world
    with pytest.raises(HTTPException) as exc:submit_work_log_create(db,actor,command(other))
    assert exc.value.status_code==403 and count(db,WorkLogEntry)==0


def test_receipt_dto_cannot_mutate_persisted_result(world):
    db,actor,project,_=world;request=command(project)
    first=submit_work_log_create(db,actor,request);db.commit()
    first['result']['work_name']='Changed client copy'
    assert submit_work_log_create(db,actor,request)['result']['work_name']=='Synthetic job'


def test_failure_after_business_flush_rolls_back_labor_and_entry(world,monkeypatch):
    db,actor,project,_=world
    from app import work_log
    original=work_log.add_entry
    def fail(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('Synthetic failure before receipt')
    monkeypatch.setattr(work_log,'add_entry',fail)
    with pytest.raises(RuntimeError,match='Synthetic failure'):
        submit_work_log_create(db,actor,command(project))
    assert count(db,WorkLogEntry)==count(db,WorkLogLabor)==count(db,MobileSyncCommand)==0


def test_two_editors_same_mutation_id_isolated(world):
    db,actor,project,_=world
    second=User(name='Second editor',email='second@example.test',is_admin=False)
    db.add(second);db.flush()
    db.add(ProjectMember(project_id=project.id,user_id=second.id,role='editor'));db.commit()
    request=command(project)
    first=submit_work_log_create(db,actor,request);db.commit()
    other=submit_work_log_create(db,second,request);db.commit()
    assert first['entity_id']>0 and other['entity_id']>0 and first['entity_id']!=other['entity_id']
    assert first['receipt_id']>0 and other['receipt_id']>0 and first['receipt_id']!=other['receipt_id']
    assert first['result']['author_user_id']==actor.id and other['result']['author_user_id']==second.id
    assert count(db,WorkLogEntry)==count(db,WorkLogLabor)==count(db,MobileSyncCommand)==2
    assert db.get(WorkLogEntry,first['entity_id']).author_user_id==actor.id
    assert db.get(WorkLogEntry,other['entity_id']).author_user_id==second.id
    assert db.get(MobileSyncCommand,first['receipt_id']).user_id==actor.id
    assert db.get(MobileSyncCommand,other['receipt_id']).user_id==second.id
    assert submit_work_log_create(db,actor,request)==first
    assert submit_work_log_create(db,second,request)==other
    assert count(db,WorkLogEntry)==count(db,WorkLogLabor)==count(db,MobileSyncCommand)==2


def _corrupt_result(db,receipt_id,mutate):
    row=db.get(MobileSyncCommand,receipt_id)
    result=deepcopy(row.result)
    mutate(result)
    row.result=result
    db.commit()


def _corrupt_row(db,receipt_id,mutate):
    row=db.get(MobileSyncCommand,receipt_id)
    mutate(row)
    db.commit()


def _set_entity_type(row):row.entity_type='task'
def _set_entity_id_zero(row):row.entity_id=0
def _set_entity_id_bool(row):row.entity_id=True
def _set_entity_id_negative(row):row.entity_id=-1
def _set_row_project_id(row):row.project_id=99999

def _set_result_id_99999(result):result['id']=99999
def _set_result_id_bool(result):result['id']=True
def _set_result_project_id_99999(result):result['project_id']=99999
def _set_result_project_id_bool(result):result['project_id']=True
def _set_result_author_99999(result):result['author_user_id']=99999
def _set_result_author_bool(result):result['author_user_id']=True


@pytest.mark.parametrize('mutate',[
    _set_entity_type,
    _set_entity_id_zero,
    _set_entity_id_bool,
    _set_entity_id_negative,
    _set_row_project_id,
],ids=['entity_type','entity_id_zero','entity_id_bool','entity_id_negative','row_project_id'])
def test_corrupt_row_receipt_rejected(world,mutate):
    db,actor,project,_=world;request=command(project)
    first=submit_work_log_create(db,actor,request);db.commit()
    _corrupt_row(db,first['receipt_id'],mutate)
    with pytest.raises(ValueError,match='offline_receipt_not_applied'):
        submit_work_log_create(db,actor,request)
    assert count(db,WorkLogEntry)==count(db,WorkLogLabor)==count(db,MobileSyncCommand)==1


@pytest.mark.parametrize('mutate',[
    _set_result_id_99999,
    _set_result_id_bool,
    _set_result_project_id_99999,
    _set_result_project_id_bool,
    _set_result_author_99999,
    _set_result_author_bool,
],ids=['result_id_99999','result_id_bool','result_project_id_99999','result_project_id_bool','result_author_99999','result_author_bool'])
def test_corrupt_result_receipt_rejected(world,mutate):
    db,actor,project,_=world;request=command(project)
    first=submit_work_log_create(db,actor,request);db.commit()
    _corrupt_result(db,first['receipt_id'],mutate)
    with pytest.raises(ValueError,match='offline_receipt_not_applied'):
        submit_work_log_create(db,actor,request)
    assert count(db,WorkLogEntry)==count(db,WorkLogLabor)==count(db,MobileSyncCommand)==1
