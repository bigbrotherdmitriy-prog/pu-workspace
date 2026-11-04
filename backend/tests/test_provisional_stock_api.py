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
