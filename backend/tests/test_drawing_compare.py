# Codex authorized fallback. Synthetic bytes only, no API/DB/providers.
from io import BytesIO
import pytest
from PIL import Image, PngImagePlugin
from app.drawing_compare import compare_raster_versions


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
