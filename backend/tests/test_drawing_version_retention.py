# Codex fallback; synthetic DB and temporary actual CAS only.
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import Session
import app.models
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.rd import RdVolume, RdVolumeVersion
from app.integrations.pu_storage import RdVersionContentStore
from app.drawing_versions import retain_version_content
from app.drawing_compare import compare_stored_raster_versions
from test_drawing_compare import image_bytes

@pytest.fixture
def world(tmp_path):
    engine=create_engine('sqlite+pysqlite:///'+str(tmp_path/'versions.db'),connect_args={'timeout':10})
    event.listen(engine,'connect',lambda conn,_:conn.execute('PRAGMA foreign_keys=ON'))
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        org=Organization(name='Retention synthetic');db.add(org);db.flush()
        own=Project(name='Own',organization_id=org.id);foreign=Project(name='Foreign',organization_id=org.id)
        db.add_all([own,foreign]);db.flush()
        a=RdVolume(project_id=own.id,title='Own',file_name='own.png',provider='local')
        b=RdVolume(project_id=foreign.id,title='Foreign',file_name='foreign.png',provider='local')
        db.add_all([a,b]);db.flush();ids=(own.id,foreign.id,a.id,b.id,org.id);db.commit()
    store=RdVersionContentStore(tmp_path/'cas',quota_bytes=10000)
    yield engine,store,ids
    engine.dispose()


def retain(db,world,content,**kw):
    engine,store,ids=world
    return retain_version_content(db,store,ids[0],kw.get('volume',ids[2]),content,kw.get('name','version.png'),kw.get('mime','image/png'),byte_budget=kw.get('budget',len(content)))


def test_actual_bytes_replay_and_comparison(world):
    engine,store,ids=world;a=image_bytes();b=image_bytes(changes=[((1,0),(255,0,0,255))])
    with Session(engine) as db:
        first=retain(db,world,a);again=retain(db,world,a,name='renamed.png',mime='application/x-alternative')
        assert first==again and first['file_name']=='version.png'
        second=retain(db,world,b);assert second['id']!=first['id']
        assert store.read(ids[0],first['content_sha256'],len(a))==a
        result=compare_stored_raster_versions(db,store,ids[0],ids[2],first['id'],second['id'],byte_budget=len(a)+len(b),pixel_budget=12)
        assert result['changed_pixels']==1
        assert db.scalar(select(func.count()).select_from(RdVolumeVersion))==2


def test_foreign_volume_denied_before_storage(world,monkeypatch):
    engine,store,ids=world;a=image_bytes()
    def forbidden(*args,**kw):raise AssertionError('Content written before ownership check')
    monkeypatch.setattr(store,'put',forbidden)
    with Session(engine) as db:
        with pytest.raises(LookupError):retain(db,world,a,volume=ids[3])
        assert db.scalar(select(func.count()).select_from(RdVolumeVersion))==0

@pytest.mark.parametrize('change',[{'budget':0},{'budget':True},{'budget':1},{'name':''},{'name':'x'*501},{'mime':'x'*101}])
def test_invalid_input_before_storage(world,monkeypatch,change):
    engine,store,ids=world;a=image_bytes()
    def forbidden(*args,**kw):raise AssertionError('Storage called for invalid command')
    monkeypatch.setattr(store,'put',forbidden)
    with Session(engine) as db:
        with pytest.raises(ValueError):retain(db,world,a,**change)


def test_rollback_leaves_deduplicated_blob_and_retry_rebuilds_metadata(world):
    engine,store,ids=world;a=image_bytes()
    with Session(engine) as db:
        first=retain(db,world,a);db.rollback()
        assert db.scalar(select(func.count()).select_from(RdVolumeVersion))==0
        assert store.read(ids[0],first['content_sha256'],len(a))==a
        second=retain(db,world,a);db.commit()
        assert second['content_sha256']==first['content_sha256']
        assert db.scalar(select(func.count()).select_from(RdVolumeVersion))==1
        assert store._used_bytes(ids[0])==len(a)


def test_success_preserves_unflushed_caller_state(world):
    engine,store,ids=world;a=image_bytes()
    with Session(engine) as db:
        p=db.get(Project,ids[0]);p.name='Pending rename'
        pending=Project(name='Pending',organization_id=ids[4]);db.add(pending)
        retain(db,world,a)
        assert pending.id is None and pending in db.new and p in db.dirty
        db.rollback();assert p.name=='Own'


def test_two_committing_sessions_create_one_metadata_and_blob(world):
    engine,store,ids=world;a=image_bytes();barrier=Barrier(2)
    def worker(index):
        with Session(engine) as db:
            barrier.wait(timeout=5)
            result=retain(db,world,a,name=f'worker-{index}.png');db.commit();return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(worker,[1,2]))
    assert results[0]==results[1]
    with Session(engine) as db:assert db.scalar(select(func.count()).select_from(RdVolumeVersion))==1
    assert store._used_bytes(ids[0])==len(a)


def test_volume_scope_changed_during_content_retention_cannot_create_foreign_metadata(world,monkeypatch):
    from sqlalchemy import update
    engine,store,ids=world;a=image_bytes();real_put=store.put
    def move_after_put(project_id,content):
        digest=real_put(project_id,content)
        with Session(engine) as other:
            other.execute(update(RdVolume).where(RdVolume.id==ids[2]).values(project_id=ids[1]))
            other.commit()
        return digest
    monkeypatch.setattr(store,'put',move_after_put)
    with Session(engine) as db:
        with pytest.raises(LookupError):retain(db,world,a)
        assert db.scalar(select(func.count()).select_from(RdVolumeVersion))==0
