"""Real TestClient, synthetic users/projects/storage only; no app startup."""
from datetime import date
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import Base,get_db
from app.core.auth import require_user
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.user import User
from app.models.project_member import ProjectMember
from app.models.purchase import PurchaseItem
from app.models.work_log import WorkLogEntry
from app.api.purchase import router


@pytest.fixture
def api_stock():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org=Organization(name='SYNTHETIC');db.add(org);db.flush()
        p=Project(name='SYNTHETIC own',organization_id=org.id);other=Project(name='SYNTHETIC foreign',organization_id=org.id)
        own=User(email='manager@example.test',name='SYNTHETIC manager');foreign=User(email='foreign@example.test',name='SYNTHETIC foreign')
        db.add_all([p,other,own,foreign]);db.flush();db.add(ProjectMember(project_id=p.id,user_id=own.id,role='manager'))
        item=PurchaseItem(project_id=p.id,group_key='s',name='SYNTHETIC',unit='m');entry=WorkLogEntry(project_id=p.id,work_date=date(2026,10,5),work_name='SYNTHETIC',qty=1)
        db.add_all([item,entry]);db.commit();current={'id':own.id};app=FastAPI();app.include_router(router);
        from app.api.defects import router as defects_router
        app.include_router(defects_router);app.dependency_overrides[get_db]=lambda:db;app.dependency_overrides[require_user]=lambda:type('U',(),{'id':current['id'],'is_admin':False,'is_active':True})()
        with TestClient(app) as client:yield client,current,p.id,item.id,entry.id,foreign.id,db
    engine.dispose()


def test_stock_api_two_users_repeat_balance_storno(api_stock):
    c,user,p,item,entry,foreign,db=api_stock;base=f'/projects/{p}/purchase/{item}'
    receipt={'operation_key':'SYNTHETIC receipt','qty':'3','unit':'m'}
    assert c.post(base+'/physical-receipts',json=receipt).status_code==200
    command={'operation_key':'SYNTHETIC consume','qty':'2','unit':'m','work_log_entry_id':entry}
    first=c.post(base+'/writeoffs',json=command);assert first.status_code==200
    assert c.post(base+'/writeoffs',json=command).json()['id']==first.json()['id']
    assert c.get(base+'/physical-balance').json()=={'qty':'1.000'}
    assert c.post(base+'/writeoffs',json={**command,'operation_key':'overdraw','qty':'2'}).status_code==422
    user['id']=foreign
    assert c.get(base+'/physical-balance').status_code==403
    assert c.post(base+'/writeoffs',json=command).status_code==403
    assert c.post(f'/projects/{p}/purchase/writeoffs/{first.json()["id"]}/storno',json={'operation_key':'foreign-reversal'}).status_code==403


def test_stock_api_validates_strict_identity_and_storno(api_stock):
    c,user,p,item,entry,_,db=api_stock;base=f'/projects/{p}/purchase/{item}'
    assert c.post(base+'/physical-receipts',json={'operation_key':'r','qty':'3','unit':'m'}).status_code==200
    command={'operation_key':'c','qty':'2','unit':'m','work_log_entry_id':entry}
    assert c.post(base+'/writeoffs',json={**command,'work_log_entry_id':True}).status_code==422
    assert c.post(base+'/writeoffs',json={**command,'source_document_ref':'must-not-be-ignored'}).status_code==422
    first=c.post(base+'/writeoffs',json=command).json()
    response=c.post(f'/projects/{p}/purchase/writeoffs/{first["id"]}/storno',json={'operation_key':'s'})
    assert response.status_code==200
    assert c.post(f'/projects/{p}/purchase/writeoffs/{first["id"]}/storno',json={'operation_key':'s'}).json()['id']==response.json()['id']
    assert c.get(base+'/physical-balance').json()=={'qty':'3.000'}


def test_drawing_point_api_scope_repeat_and_bounds(api_stock):
    c,user,p,item,entry,foreign,db=api_stock
    from app.models.rd import RdVolume,RdVolumeVersion
    from app.defects import create_defect
    volume=RdVolume(project_id=p,title='SYNTHETIC',file_name='SYNTHETIC.pdf',provider='local');db.add(volume);db.flush()
    version=RdVolumeVersion(volume_id=volume.id,content_sha256='a'*64,size_bytes=0,file_name='SYNTHETIC.pdf',mime_type='application/pdf');db.add(version);db.flush()
    defect=create_defect(db,p,None,{'title':'SYNTHETIC'});db.commit()
    path=f'/projects/{p}/defects/{defect["id"]}/drawing-point';body={'rd_version_id':version.id,'page':1,'x':0.25,'y':0.5}
    assert c.post(path,json={**body,'rd_version_id':2147483647}).status_code==404
    assert c.post(path,json={**body,'page':True}).status_code==422
    first=c.post(path,json=body);assert first.status_code==200
    assert c.post(path,json=body).json()==first.json()
    assert c.post(path,json={**body,'page':2}).status_code==422
    assert c.get(f'/projects/{p}/defects/{defect["id"]}').json()['drawing_point']==first.json()
    user['id']=foreign
    assert c.post(path,json=body).status_code==403


def test_stock_scope_with_actual_bearer_auth_and_revoked_session(api_stock):
    c,user,p,item,entry,foreign,db=api_stock
    from app.core.auth import issue_session
    from app.models.auth_session import AuthSession
    own_id=user['id']
    own_token,_=issue_session(db,own_id)
    foreign_token,_=issue_session(db,foreign)
    c.app.dependency_overrides.pop(require_user)
    path=f'/projects/{p}/purchase/{item}/physical-balance'
    assert c.get(path).status_code==401
    assert c.get(path,headers={'Authorization':'Bearer '+own_token}).status_code==200
    assert c.get(path,headers={'Authorization':'Bearer '+foreign_token}).status_code==403
    db.query(AuthSession).filter(AuthSession.user_id==own_id).delete(synchronize_session=False);db.commit()
    assert c.get(path,headers={'Authorization':'Bearer '+own_token}).status_code==401


@pytest.mark.parametrize('kind',['work-log','defect'])
def test_typed_offline_create_real_http_replay_actor_and_revoke(api_stock,kind):
    c,user,p,item,entry,foreign,db=api_stock
    from app.api.mobile_sync import router as mobile_router
    from app.core.auth import issue_session
    from app.models.mobile_sync import MobileSyncCommand
    from app.models.defects import Defect
    own=user['id'];own_token,_=issue_session(db,own);foreign_token,_=issue_session(db,foreign)
    c.app.include_router(mobile_router);c.app.dependency_overrides.pop(require_user)
    model=WorkLogEntry if kind=='work-log' else Defect
    before=db.query(model).count()
    data={'work_date':'2026-10-05','work_name':'SYNTHETIC offline HTTP','qty':'1.250'} if kind=='work-log' else {'title':'SYNTHETIC offline HTTP'}
    operation='work_log.create' if kind=='work-log' else 'defect.create'
    body={'project_id':p,'client_mutation_id':'synthetic-http-create','device_id':'synthetic-browser','operation':operation,'client_created_at':'2026-10-05T00:00:00Z','data':data}
    path=f'/mobile-sync/{kind}-create';headers={'Authorization':'Bearer '+own_token,'X-PU-Expected-User':str(own)}
    assert c.post(path,json=body).status_code==401
    first=c.post(path,json=body,headers=headers);assert first.status_code==200
    assert c.post(path,json=body,headers=headers).json()==first.json()
    assert db.query(model).count()==before+1 and db.query(MobileSyncCommand).count()==1
    assert first.json()['result']['author_user_id']==own and first.json()['result']['project_id']==p
    assert c.post(path,json=body,headers={**headers,'Authorization':'Bearer '+foreign_token}).status_code==403
    changed={**body,'data':{**data,('work_name' if kind=='work-log' else 'title'):'SYNTHETIC changed'}}
    assert c.post(path,json=changed,headers=headers).status_code==409
    db.query(ProjectMember).filter_by(project_id=p,user_id=own).delete(synchronize_session=False);db.commit()
    assert c.post(path,json=body,headers=headers).status_code==403
    assert db.query(model).count()==before+1 and db.query(MobileSyncCommand).count()==1


def test_legacy_estimate_read_over_http_stays_correctable_under_strict_policy(api_stock):
    from decimal import Decimal
    from app.models.estimates import Estimate,EstimateLine
    from app.api.estimates import router as estimates_router
    c,user,p,item,entry,foreign,db=api_stock
    estimate=Estimate(project_id=p,name='SYNTHETIC legacy',total=Decimal('2.01'));db.add(estimate);db.flush()
    line=EstimateLine(project_id=p,estimate_id=estimate.id,name='SYNTHETIC legacy',qty=2,total=Decimal('2.01'),unit_price=None)
    db.add(line);db.commit();c.app.include_router(estimates_router)
    response=c.get(f'/projects/{p}/estimates/{estimate.id}')
    assert response.status_code==200
    assert response.json()['lines'][0]['unit_price'] is None
    assert response.json()['lines'][0]['total']=='2.01'


def test_history_and_expected_actor_precondition_for_stock_api(api_stock):
    from app.models.purchase import MaterialReceipt
    c,user,p,item,entry,foreign,db=api_stock
    base=f'/projects/{p}/purchase/{item}'
    assert c.post(base+'/physical-receipts',json={'operation_key':'wrong-actor','qty':'3','unit':'m'},headers={'X-PU-Expected-User':str(foreign)}).status_code==403
    assert db.query(MaterialReceipt).count()==0
    assert c.post(base+'/physical-receipts',json={'operation_key':'history-arrival','qty':'3','unit':'m'},headers={'X-PU-Expected-User':str(user['id'])}).status_code==200
    response=c.get(base+'/stock-history?limit=1')
    assert response.status_code==200 and response.json()['balance']=='3.000'
    assert response.json()['unit']=='m' and response.json()['movements'][0]['kind']=='receipt'
    assert c.get(base+'/stock-history?limit=101').status_code==422
    user['id']=foreign
    assert c.get(base+'/stock-history').status_code==403
