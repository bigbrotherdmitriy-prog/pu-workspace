# Codex fallback project/volume CAS service checks, not HTTP authorization.
from pathlib import Path
from hashlib import sha256
from decimal import Decimal
import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session
import app.models
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.rd import RdVolume, RdVolumeVersion
from app.models.defects import Defect, DefectDrawingPoint
from app.integrations.pu_storage import RdVersionContentStore
from app.drawing_compare import compare_stored_raster_versions
from app.drawing_points import bind_point
from app.defects import create_defect
from test_drawing_compare import image_bytes

@pytest.fixture
def world(tmp_path):
    engine=create_engine('sqlite://')
    event.listen(engine,'connect',lambda connection,_:connection.execute('PRAGMA foreign_keys=ON'))
    Base.metadata.create_all(engine)
    store=RdVersionContentStore(tmp_path/'store',quota_bytes=10000)
    with Session(engine) as db:
        org=Organization(name='Synthetic drawings');db.add(org);db.flush()
        own=Project(name='Own',organization_id=org.id);foreign=Project(name='Foreign',organization_id=org.id)
        db.add_all([own,foreign]);db.flush()
        volumes=[RdVolume(project_id=pid,title=name,file_name='drawing.png',provider='pu',external_id=name) for pid,name in [(own.id,'own'),(own.id,'other-volume'),(foreign.id,'foreign')]]
        db.add_all(volumes);db.flush()
        a=image_bytes();b=image_bytes(changes=[((1,0),(255,0,0,255))])
        versions=[]
        for vol,content in [(volumes[0],a),(volumes[0],b),(volumes[1],a),(volumes[2],b)]:
            digest=store.put(vol.project_id,content)
            version=RdVolumeVersion(volume_id=vol.id,content_sha256=digest,size_bytes=len(content),file_name='drawing.png',mime_type='image/png')
            db.add(version);db.flush();versions.append(version)
        db.commit()
        yield db,store,own,volumes,versions,a,b
    engine.dispose()


def compare(world,previous=0,current=1,**kwargs):
    db,store,own,volumes,versions,a,b=world
    return compare_stored_raster_versions(db,store,own.id,volumes[0].id,versions[previous].id,versions[current].id,byte_budget=kwargs.get('byte_budget',len(a)+len(b)),pixel_budget=12)


def test_scoped_pair_reads_real_cas_and_identifies_versions(world):
    result=compare(world)
    assert result['changed_pixels']==1 and result['changed_bbox']==[1,0,2,1]
    assert result['previous_version_id']==world[4][0].id and result['current_version_id']==world[4][1].id

@pytest.mark.parametrize('previous,current',[(0,2),(2,0),(0,3),(3,0)])
def test_both_sides_project_volume_checked_before_content_read(world,monkeypatch,previous,current):
    def forbidden(*args,**kw):raise AssertionError('Content read before full pair scope validation')
    monkeypatch.setattr(world[1],'read',forbidden)
    with pytest.raises(LookupError):compare(world,previous,current)


def test_aggregate_byte_budget_before_read(world,monkeypatch):
    def forbidden(*args,**kw):raise AssertionError('Content read before metadata budget validation')
    monkeypatch.setattr(world[1],'read',forbidden)
    with pytest.raises(ValueError,match='byte budget'):compare(world,byte_budget=1)


def test_readonly_preserves_pending_project(world):
    db,store,own,volumes,versions,a,b=world
    ids=own.id,volumes[0].id,versions[0].id,versions[1].id
    org=own.organization_id;own.name='Pending rename';pending=Project(name='Pending',organization_id=org);db.add(pending)
    statements=[]
    def capture(conn,cursor,sql,parameters,context,executemany):statements.append(sql)
    event.listen(db.get_bind(),'before_cursor_execute',capture)
    try:result=compare_stored_raster_versions(db,store,*ids,byte_budget=len(a)+len(b),pixel_budget=12)
    finally:event.remove(db.get_bind(),'before_cursor_execute',capture)
    assert result['changed_pixels']==1
    assert pending.id is None and pending in db.new and own in db.dirty
    assert not any(s.lstrip().lower().startswith(('insert','update','delete')) for s in statements)
    assert any('limit' in s.lower() for s in statements)


def test_declared_size_and_mime_mismatch_rejected(world):
    db,store,own,volumes,versions,a,b=world
    versions[0].size_bytes=len(a)+1;db.commit()
    with pytest.raises(ValueError,match='mismatch'):compare(world,byte_budget=len(a)+len(b)+1)
    versions[0].size_bytes=len(a);versions[0].mime_type='image/jpeg';db.commit()
    with pytest.raises(ValueError,match='MIME'):compare(world)


def _single_frame_png_fixture(world):
    """Return the stored PNG bytes for the own-project version and assert exactly one frame."""
    from io import BytesIO
    from PIL import Image
    db,store,own,volumes,versions,a,b=world
    version=versions[0]
    content=store.read(own.id,version.content_sha256,max_bytes=version.size_bytes)
    assert sha256(content).hexdigest()==version.content_sha256
    with Image.open(BytesIO(content)) as image:
        assert image.format=='PNG'
        assert getattr(image,'n_frames',1)==1
        image.load()
    return content


def _fresh_defect(db,project_id,name):
    created=create_defect(db,project_id,None,{'title':name})
    db.flush();db.commit()
    defect_id=created['id'] if isinstance(created,dict) else created.id
    return db.get(Defect,defect_id)


def _snapshot(db,defect_id,project_id):
    """Read actual stored values via SQL, bypassing ORM identity-map/cached state.

    Returns (rd_version_id, ordered point column VALUES). The point rows are
    ordered deterministically by id so the tuple is stable across calls.
    """
    with db.no_autoflush:
        rd_version_id=db.execute(
            text('SELECT rd_version_id FROM defects WHERE id=:id AND project_id=:pid'),
            {'id':defect_id,'pid':project_id},
        ).scalar()
        rows=db.execute(
            text(
                'SELECT id, defect_id, project_id, rd_version_id, page, x, y '
                'FROM defect_drawing_points '
                'WHERE defect_id=:id AND project_id=:pid ORDER BY id'
            ),
            {'id':defect_id,'pid':project_id},
        ).all()
    return rd_version_id,tuple(tuple(row) for row in rows)


def test_service_single_frame_annotation_positive_control(world):
    db,store,own,volumes,versions,a,b=world
    _single_frame_png_fixture(world)
    defect=_fresh_defect(db,own.id,'positive')
    version=versions[0]
    first=bind_point(db,own.id,defect.id,version_id=version.id,page=1,x='0.25',y='0.50')
    db.commit()
    assert first['rd_version_id']==version.id and first['page']==1
    assert Decimal(first['x'])==Decimal('0.25') and Decimal(first['y'])==Decimal('0.50')
    rd_version_id,points=_snapshot(db,defect.id,own.id)
    assert rd_version_id==version.id and len(points)==1
    assert Decimal(str(points[0][5]))==Decimal('0.25') and Decimal(str(points[0][6]))==Decimal('0.50')
    assert points[0][4]==1 and points[0][3]==version.id
    replay=bind_point(db,own.id,defect.id,version_id=version.id,page=1,x='0.250',y='0.5')
    db.commit()
    assert Decimal(replay['x'])==Decimal('0.25') and Decimal(replay['y'])==Decimal('0.5')
    rd_version_id2,points2=_snapshot(db,defect.id,own.id)
    assert len(points2)==1 and rd_version_id2==version.id and points2==points


def test_service_single_frame_annotation_page2_rejected(world):
    db,store,own,volumes,versions,a,b=world
    _single_frame_png_fixture(world)
    defect=_fresh_defect(db,own.id,'negative')
    version=versions[0]
    before_rd_version_id,before_points=_snapshot(db,defect.id,own.id)
    assert before_rd_version_id is None and before_points==()
    with pytest.raises(ValueError,match='page'):
        bind_point(db,own.id,defect.id,version_id=version.id,page=2,x='0.25',y='0.50')
        # Assert stored state BEFORE any caller rollback so a partial mutation
        # cannot be hidden by the rollback below.
        mid_rd_version_id,mid_points=_snapshot(db,defect.id,own.id)
        assert mid_rd_version_id==before_rd_version_id
        assert mid_points==before_points
    db.rollback()
    after_rd_version_id,after_points=_snapshot(db,defect.id,own.id)
    assert after_rd_version_id==before_rd_version_id
    assert after_points==before_points
    assert db.scalar(select(Defect).where(Defect.id==defect.id,Defect.project_id==own.id)) is not None
