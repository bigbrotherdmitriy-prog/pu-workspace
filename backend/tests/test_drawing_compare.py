# Codex authorized fallback. Synthetic bytes only, no API/DB/providers.
from io import BytesIO
from hashlib import sha256
import pytest
from PIL import Image, PngImagePlugin
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
import app.models
from app.database import Base
from app.models.organization_contract import Organization
from app.models.project import Project
from app.models.rd import RdVolume, RdVolumeVersion
from app.integrations.pu_storage import RdVersionContentStore
from app.drawing_compare import compare_raster_versions, validate_stored_raster_page


def image_bytes(size=(3,2),color=(0,0,0,255),changes=(),metadata=None,mode='RGBA',fmt='PNG'):
    image=Image.new(mode,size,color)
    try:
        for point,value in changes:image.putpixel(point,value)
        out=BytesIO();kwargs={}
        if metadata is not None:
            info=PngImagePlugin.PngInfo();info.add_text('Description',metadata);kwargs['pnginfo']=info
        image.save(out,format=fmt,**kwargs);return out.getvalue()
    finally:image.close()


def compare(a,b,**kw):return compare_raster_versions(a,b,byte_budget=kw.get('byte_budget',len(a)+len(b)),pixel_budget=kw.get('pixel_budget',12))


def test_metadata_change_is_not_pixel_change():
    a=image_bytes(metadata='old');b=image_bytes(metadata='new')
    result=compare(a,b)
    assert result['previous']['sha256']!=result['current']['sha256']
    assert result['same_pixels'] and result['changed_pixels']==0 and result['changed_bbox'] is None


def test_rgb_changes_with_unchanged_alpha_are_detected():
    result=compare(image_bytes(),image_bytes(changes=[((1,0),(255,0,0,255)),((2,1),(0,255,0,255))]))
    assert result['changed_pixels']==2 and result['changed_bbox']==[1,0,3,2]
    assert not result['same_pixels']


def test_alpha_only_change_detected():
    result=compare(image_bytes(),image_bytes(changes=[((0,1),(0,0,0,0))]))
    assert result['changed_pixels']==1 and result['changed_bbox']==[0,1,1,2]


def test_different_dimensions_not_auto_aligned():
    result=compare(image_bytes(),image_bytes(size=(2,2)),pixel_budget=10)
    assert result['dimensions_changed'] and not result['same_pixels']
    assert result['changed_pixels'] is None and result['changed_bbox'] is None


def test_jpeg_is_supported():
    a=image_bytes(color=(20,30,40),mode='RGB',fmt='JPEG');result=compare(a,a)
    assert result['same_pixels'] and result['current']['format']=='JPEG'

@pytest.mark.parametrize('budget',[0,-1,True,'12'])
def test_invalid_pixel_budgets(budget):
    a=image_bytes()
    with pytest.raises(ValueError):compare(a,a,pixel_budget=budget)

@pytest.mark.parametrize('budget',[0,-1,True,'12'])
def test_invalid_byte_budgets(budget):
    a=image_bytes()
    with pytest.raises(ValueError):compare(a,a,byte_budget=budget)


def test_total_input_byte_limit():
    a=image_bytes()
    with pytest.raises(ValueError):compare(a,a,byte_budget=len(a)*2-1)


def test_aggregate_pixels_checked_before_load(monkeypatch):
    a=image_bytes()
    def forbidden(*args,**kwargs):raise AssertionError('Full decode happened before aggregate budget rejection')
    monkeypatch.setattr(Image.Image,'load',forbidden)
    with pytest.raises(ValueError,match='pixel budget'):compare(a,a,pixel_budget=11)

@pytest.mark.parametrize('content',[b'',b'not an image',b'%PDF-1.7'])
def test_invalid_inputs_rejected(content):
    with pytest.raises(ValueError):compare(content,image_bytes())


def test_animated_png_rejected():
    a=Image.new('RGBA',(2,2),'red');b=Image.new('RGBA',(2,2),'blue');out=BytesIO()
    try:a.save(out,format='PNG',save_all=True,append_images=[b],duration=20,loop=0)
    finally:a.close();b.close()
    with pytest.raises(ValueError,match='multiframe'):compare(out.getvalue(),image_bytes())


# --- Standalone validate_stored_raster_page service tests (own fixture) ---

@pytest.fixture
def page_world(tmp_path):
    engine=create_engine('sqlite://')
    event.listen(engine,'connect',lambda connection,_:connection.execute('PRAGMA foreign_keys=ON'))
    Base.metadata.create_all(engine)
    store=RdVersionContentStore(tmp_path/'store',quota_bytes=10000)
    with Session(engine) as db:
        org=Organization(name='Synthetic page drawings');db.add(org);db.flush()
        own=Project(name='Own',organization_id=org.id);foreign=Project(name='Foreign',organization_id=org.id)
        db.add_all([own,foreign]);db.flush()
        vol1=RdVolume(project_id=own.id,title='v1',file_name='drawing.png',provider='pu',external_id='v1')
        vol2=RdVolume(project_id=own.id,title='v2',file_name='drawing.png',provider='pu',external_id='v2')
        volf=RdVolume(project_id=foreign.id,title='vf',file_name='drawing.png',provider='pu',external_id='vf')
        db.add_all([vol1,vol2,volf]);db.flush()
        png=image_bytes()
        jpeg=image_bytes(color=(20,30,40),mode='RGB',fmt='JPEG')
        def add(vol,content,mime='image/png'):
            digest=store.put(vol.project_id,content)
            version=RdVolumeVersion(volume_id=vol.id,content_sha256=digest,size_bytes=len(content),file_name='drawing.png',mime_type=mime)
            db.add(version);db.flush();return version
        v1=add(vol1,png)
        v2=add(vol2,png)
        vj=add(vol1,jpeg,'image/jpeg')
        vf=add(volf,png)
        db.commit()
        yield db,store,own,foreign,vol1,vol2,volf,v1,v2,vj,vf,png,jpeg
    engine.dispose()


def validate(world,version,page=1,**kw):
    db,store,own,foreign,vol1,vol2,volf,v1,v2,vj,vf,png,jpeg=world
    return validate_stored_raster_page(db,store,own.id,version.id,page,
        byte_budget=kw.get('byte_budget',len(png)+len(jpeg)),pixel_budget=kw.get('pixel_budget',12))


def test_valid_png_page1_returns_exact_metadata(page_world):
    db,store,own,foreign,vol1,vol2,volf,v1,v2,vj,vf,png,jpeg=page_world
    assert v2.id!=vol1.id and v2.volume_id!=vol1.id
    result=validate(page_world,v1)
    assert result=={'version_id':v1.id,'content_sha256':v1.content_sha256,'page':1,'page_count':1}
    assert result['content_sha256']==sha256(png).hexdigest()
    assert 'content' not in result and 'bytes' not in result


def test_jpeg_positive(page_world):
    result=validate(page_world,page_world[9])
    assert result['version_id']==page_world[9].id and result['page_count']==1


def test_foreign_project_version_not_found_before_read(page_world,monkeypatch):
    db,store,own,foreign,vol1,vol2,volf,v1,v2,vj,vf,png,jpeg=page_world
    def forbidden(*a,**k):raise AssertionError('Content read before scope validation')
    monkeypatch.setattr(store,'read',forbidden)
    with pytest.raises(LookupError):validate_stored_raster_page(db,store,foreign.id,v1.id,1,byte_budget=len(png),pixel_budget=12)


def test_missing_version_not_found_before_read(page_world,monkeypatch):
    db,store,own,foreign,vol1,vol2,volf,v1,v2,vj,vf,png,jpeg=page_world
    def forbidden(*a,**k):raise AssertionError('Content read before existence validation')
    monkeypatch.setattr(store,'read',forbidden)
    with pytest.raises(LookupError):validate_stored_raster_page(db,store,own.id,999999,1,byte_budget=len(png),pixel_budget=12)


def test_declared_byte_budget_rejects_before_read(page_world,monkeypatch):
    db,store,own,foreign,vol1,vol2,volf,v1,v2,vj,vf,png,jpeg=page_world
    def forbidden(*a,**k):raise AssertionError('Content read before byte budget validation')
    monkeypatch.setattr(store,'read',forbidden)
    with pytest.raises(ValueError,match='metadata'):validate_stored_raster_page(db,store,own.id,v1.id,1,byte_budget=len(png)-1,pixel_budget=12)


def test_readonly_preserves_pending_state(page_world):
    db,store,own,foreign,vol1,vol2,volf,v1,v2,vj,vf,png,jpeg=page_world
    org=own.organization_id;own.name='Pending rename';pending=Project(name='Pending',organization_id=org);db.add(pending)
    statements=[]
    def capture(conn,cursor,sql,parameters,context,executemany):statements.append(sql)
    event.listen(db.get_bind(),'before_cursor_execute',capture)
    try:result=validate(page_world,v1)
    finally:event.remove(db.get_bind(),'before_cursor_execute',capture)
    assert result['version_id']==v1.id
    assert pending.id is None and pending in db.new and own in db.dirty
    assert not any(s.lstrip().lower().startswith(('insert','update','delete')) for s in statements)


def test_corrupt_bytes_rejected(page_world):
    db,store,own,foreign,vol1,vol2,volf,v1,v2,vj,vf,png,jpeg=page_world
    v1.size_bytes=len(png)+1;db.commit()
    with pytest.raises(ValueError,match='mismatch'):validate(page_world,v1)


def test_corrupt_hash_rejected(page_world):
    db,store,own,foreign,vol1,vol2,volf,v1,v2,vj,vf,png,jpeg=page_world
    v1.content_sha256='0'*64;db.commit()
    with pytest.raises(ValueError,match='mismatch'):validate(page_world,v1)


def test_png_with_stored_jpeg_mime_rejected(page_world):
    db,store,own,foreign,vol1,vol2,volf,v1,v2,vj,vf,png,jpeg=page_world
    v1.mime_type='image/jpeg';db.commit()
    with pytest.raises(ValueError,match='MIME'):validate(page_world,v1)


def test_malformed_image_with_correct_hash_rejected(page_world):
    db,store,own,foreign,vol1,vol2,volf,v1,v2,vj,vf,png,jpeg=page_world
    bad=b'not an image'
    digest=store.put(own.id,bad)
    v1.content_sha256=digest;v1.size_bytes=len(bad);db.commit()
    with pytest.raises(ValueError,match='unsupported'):validate(page_world,v1)


def test_multiframe_png_rejected(page_world):
    db,store,own,foreign,vol1,vol2,volf,v1,v2,vj,vf,png,jpeg=page_world
    a=Image.new('RGBA',(2,2),'red');b=Image.new('RGBA',(2,2),'blue');out=BytesIO()
    try:a.save(out,format='PNG',save_all=True,append_images=[b],duration=20,loop=0)
    finally:a.close();b.close()
    content=out.getvalue();digest=store.put(own.id,content)
    v1.content_sha256=digest;v1.size_bytes=len(content);db.commit()
    with pytest.raises(ValueError,match='multiframe'):validate(page_world,v1)


def test_page2_rejected(page_world):
    with pytest.raises(ValueError,match='page 1'):validate(page_world,page_world[7],page=2)


def test_pixel_budget_below_dimensions_rejects_before_load(page_world,monkeypatch):
    db,store,own,foreign,vol1,vol2,volf,v1,v2,vj,vf,png,jpeg=page_world
    def forbidden(*a,**k):raise AssertionError('Full decode happened before pixel budget rejection')
    monkeypatch.setattr(Image.Image,'load',forbidden)
    with pytest.raises(ValueError,match='pixel budget'):validate(page_world,v1,pixel_budget=5)


@pytest.mark.parametrize('field',['project_id','version_id','page','byte_budget','pixel_budget'])
@pytest.mark.parametrize('value',[0,-1,True,'1'])
def test_invalid_identifiers_and_budgets_rejected(page_world,field,value):
    db,store,own,foreign,vol1,vol2,volf,v1,v2,vj,vf,png,jpeg=page_world
    args={'project_id':own.id,'version_id':v1.id,'page':1,'byte_budget':len(png),'pixel_budget':12}
    args[field]=value
    with pytest.raises(ValueError,match='Invalid identifiers'):
        validate_stored_raster_page(db,store,args['project_id'],args['version_id'],args['page'],byte_budget=args['byte_budget'],pixel_budget=args['pixel_budget'])
